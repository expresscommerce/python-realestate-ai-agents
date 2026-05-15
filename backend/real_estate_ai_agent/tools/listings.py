"""Property listing search and lookup tools."""

from __future__ import annotations

import logging
import re

from ..integrations.scraper_client import ScraperClient
from ..integrations.ghost_browser import ghost_scrape_listings_sync
from .redfin import _redfin_search_url, _redfin_address_url

log = logging.getLogger(__name__)

_LISTING_SCHEMA = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "address": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "state": {"type": ["string", "null"]},
                    "zip": {"type": ["string", "null"]},
                    "price": {"type": ["string", "number", "null"]},
                    "bedrooms": {"type": ["string", "number", "null"]},
                    "bathrooms": {"type": ["string", "number", "null"]},
                    "sqft": {"type": ["string", "number", "null"]},
                    "property_type": {"type": ["string", "null"]},
                    "url": {"type": ["string", "null"]},
                },
            },
        }
    },
    "required": ["listings"],
}


def _coerce_listings_from_api(listing_result: dict, search_url: str) -> list[dict]:
    """Prefer flat `results` from /v1/listings, then unwrap `listings` ListingItem objects."""
    raw: list[dict] = []
    search_norm = str(search_url).rstrip("/")

    for key in ("results", "items", "listings", "data"):
        value = listing_result.get(key)
        if isinstance(value, list) and value:
            raw = [x for x in value if isinstance(x, dict)]
            break
        if isinstance(value, dict):
            nested = value.get("results") or value.get("listings") or value.get("items")
            if isinstance(nested, list) and nested:
                raw = [x for x in nested if isinstance(x, dict)]
                break

    out: list[dict] = []
    for item in raw:
        data_inner = item.get("data")
        lu = str(item.get("listing_url") or "").rstrip("/")

        if isinstance(data_inner, dict) and data_inner:
            row = dict(data_inner)
            row.setdefault(
                "listing_url",
                (item.get("listing_url") or row.get("listing_url") or "") or "",
            )
            rl = str(row.get("listing_url") or "").rstrip("/")
            if rl == search_norm and not (
                row.get("address")
                or row.get("formatted_address")
                or row.get("street")
                or row.get("price_usd") is not None
                or row.get("price")
            ):
                continue
            out.append(row)
            continue

        if lu == search_norm:
            continue
        row_flat = dict(item)
        if row_flat.get("data") in (None, {}):
            row_flat.pop("data", None)
        has_detail_url = lu and "/home/" in lu
        has_signals = bool(
            row_flat.get("address")
            or row_flat.get("formatted_address")
            or row_flat.get("street")
            or row_flat.get("price_usd") is not None
            or row_flat.get("price")
            or row_flat.get("list_price")
        )
        if has_signals or has_detail_url:
            out.append(row_flat)

    return out


def _format_listing_card(i: int, card: dict, city: str, state: str) -> list[str]:
    """Format a single listing card dict into bullet-point display lines."""
    addr = card.get("address") or "Address unavailable"
    price = card.get("price") or "Price not listed"
    beds = card.get("beds") or card.get("bedrooms") or "—"
    baths = card.get("baths") or card.get("bathrooms") or "—"
    sqft = card.get("sqft")
    link = card.get("url") or card.get("listing_url")
    card_city = card.get("city") or city
    card_state = card.get("state") or state

    lines = [f"**Option {i}**"]
    lines.append(f"- **Address:** {addr}")
    lines.append(f"- **Location:** {card_city}, {card_state}")
    lines.append(f"- **Price:** {price}")
    lines.append(f"- **Beds/Baths:** {beds} bed / {baths} bath")
    if sqft:
        lines.append(f"- **Sqft:** {sqft}")
    if link:
        lines.append(f"- **View listing:** {link}")
    lines.append("")
    return lines


def _parse_price_to_int(price: object) -> int | None:
    if price is None:
        return None
    s = str(price)
    m = re.search(r"\$?\s*([\d,]+)", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_city_state_from_address(address: object) -> tuple[str | None, str | None]:
    if not isinstance(address, str):
        return (None, None)
    m = re.search(r",\s*([^,]+),\s*([A-Z]{2})\b", address)
    if not m:
        return (None, None)
    return (m.group(1).strip(), m.group(2).strip())


def _normalize_city_for_compare(value: str) -> str:
    """Normalize city labels for robust comparisons."""
    text = (value or "").strip().lower()
    text = re.sub(r"[.,_/+-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _display_city_label(city: str, state: str) -> str:
    """Create a clean city label for user-facing output."""
    c = _normalize_city_for_compare(city)
    s = (state or "").strip().lower()
    if c.endswith(f" {s}"):
        c = c[: -(len(s) + 1)].strip()
    if c == "nyc":
        c = "new york city"
    return c.title() if c else city.strip()


_NYC_BOROUGH_CITY_MATCHERS: dict[str, set[str]] = {
    "queens": {
        "queens", "rego park", "jamaica", "long island city", "astoria",
        "flushing", "forest hills", "elmhurst", "jackson heights", "woodside",
        "sunnyside", "ridgewood", "ozone park", "south ozone park", "kew gardens",
        "fresh meadows", "middle village", "bayside", "whitestone", "college point",
        "maspeth", "glendale", "far rockaway", "arverne", "queens village",
        "cambria heights", "hollis", "rosedale", "howard beach", "corona",
    },
    "brooklyn": {"brooklyn"},
    "bronx": {"bronx"},
    "staten island": {"staten island"},
    "manhattan": {"new york", "manhattan"},
}


def _normalize_and_filter_cards(
    cards: list[dict],
    *,
    city: str,
    state: str,
    listing_type: str,
    price_min: int,
    price_max: int,
    bedrooms_min: int,
    bedrooms_max: int,
) -> list[dict]:
    out: list[dict] = []
    req_city = _normalize_city_for_compare(city)
    req_state = state.strip().upper()
    if req_city.endswith(f" {req_state.lower()}"):
        req_city = req_city[: -(len(req_state) + 1)].strip()
    is_nyc_search = req_state == "NY" and req_city in {"new york", "new york city", "nyc"}
    is_nyc_borough_search = req_state == "NY" and req_city in {
        "queens", "brooklyn", "bronx", "staten island", "manhattan",
    }
    borough_matchers = _NYC_BOROUGH_CITY_MATCHERS.get(req_city, set())

    for card in cards:
        if not isinstance(card, dict):
            continue
        c = dict(card)

        inferred_city, inferred_state = _extract_city_state_from_address(c.get("address"))
        if inferred_city and not c.get("city"):
            c["city"] = inferred_city
        if inferred_state and not c.get("state"):
            c["state"] = inferred_state

        card_city = _normalize_city_for_compare(str(c.get("city") or ""))
        card_state = str(c.get("state") or "").strip().upper()

        if card_state and card_state != req_state:
            continue

        if is_nyc_borough_search:
            if card_city and borough_matchers and card_city not in borough_matchers:
                continue
        elif not is_nyc_search:
            if card_city and card_city != req_city:
                continue

        price_n = _parse_price_to_int(c.get("price"))

        if listing_type == "sale" and price_n is not None and price_n < 10_000:
            continue

        if price_min and price_n is not None and price_n < price_min:
            continue
        if price_max and price_n is not None and price_n > price_max:
            continue

        beds_val = c.get("beds") or c.get("bedrooms")
        try:
            beds_n = float(beds_val) if beds_val not in (None, "") else None
        except (TypeError, ValueError):
            beds_n = None
        if bedrooms_min and beds_n is not None and beds_n < bedrooms_min:
            continue
        if bedrooms_max and beds_n is not None and beds_n > bedrooms_max:
            continue

        out.append(c)
    return out


def search_listings(
    city: str,
    state: str,
    listing_type: str = "",
    property_type: str = "",
    bedrooms_min: int = 0,
    bedrooms_max: int = 0,
    bathrooms_min: int = 0,
    price_min: int = 0,
    price_max: int = 0,
    limit: int = 5,
    offset: int = 0,
) -> str:
    missing: list[str] = []
    if not city or not state:
        missing.append("city and state")
    if not price_max:
        missing.append("budget (maximum price)")
    if not bedrooms_min and not bedrooms_max:
        missing.append("number of bedrooms")
    if missing:
        return (
            "I still need a few details before I can search: "
            + ", ".join(missing)
            + ". Could you let me know?"
        )

    show_limit = max(1, min(int(limit), 10))
    skip = max(0, int(offset))
    lt = listing_type if listing_type in ("sale", "rent") else "sale"
    display_city = _display_city_label(city, state)
    label = "rentals" if lt == "rent" else "homes"

    scrape_count = show_limit + skip + 5

    req_bedrooms_min = bedrooms_min
    req_bedrooms_max = bedrooms_max

    redfin_url = _redfin_search_url(
        city,
        state,
        listing_type=lt,
        price_min=price_min,
        price_max=price_max,
        bedrooms_min=bedrooms_min,
        bedrooms_max=bedrooms_max,
        property_type=property_type,
    )
    log.info("search_listings redfin url: %s  (limit=%d, offset=%d)", redfin_url, show_limit, skip)

    try:
        cards = ghost_scrape_listings_sync(
            redfin_url,
            max_listings=scrape_count,
            headless=True,
            timeout_ms=120_000,
        )
    except Exception as exc:
        log.error("search_listings ghost scrape failed: %s", exc)
        cards = []

    scraped_count = len(cards) if isinstance(cards, list) else 0
    log.info("search_listings scraped %d cards before filtering", scraped_count)

    cards = _normalize_and_filter_cards(
        cards,
        city=city,
        state=state,
        listing_type=lt,
        price_min=price_min,
        price_max=price_max,
        bedrooms_min=bedrooms_min,
        bedrooms_max=bedrooms_max,
    )

    filtered_count = len(cards)
    log.info("search_listings kept %d cards after filtering", filtered_count)

    page_cards = cards[skip: skip + show_limit]

    if page_cards:
        total = len(cards)
        showing_start = skip + 1
        showing_end = skip + len(page_cards)
        lines = [
            f"Here are {label} {showing_start}–{showing_end} (of {total} found) in {display_city}, {state}:\n",
        ]
        for i, card in enumerate(page_cards, showing_start):
            lines.extend(_format_listing_card(i, card, display_city, state))
        if showing_end < total:
            lines.append(f"There are {total - showing_end} more listings available. Say 'show more' to see the next batch.")
        return "\n".join(lines).strip()

    if skip > 0:
        return f"No more listings beyond the ones already shown for {display_city}, {state}."

    if req_bedrooms_min and req_bedrooms_min > 5 and scraped_count > 0 and filtered_count == 0:
        return (
            f"I found {scraped_count} listings matching your city/budget, but none of them clearly show "
            f"{req_bedrooms_min}+ bedrooms.\n"
            f"Redfin only supports a 5+ bedroom filter, so I search 5+ and then filter for your exact request.\n"
            f"If you want, I can still show the best 5+ bedroom matches and you can open them to verify bedroom count."
        )
    if req_bedrooms_max and req_bedrooms_max > 5 and scraped_count > 0 and filtered_count == 0:
        return (
            f"I found {scraped_count} listings matching your city/budget, but none clearly show "
            f"up to {req_bedrooms_max} bedrooms.\n"
            f"Redfin only supports a 5+ bedroom cap filter, so I searched broadly and filtered locally."
        )

    return (
        f"I couldn't find matching listings in {display_city}, {state} right now.\n"
        f"If you want, I can expand the budget slightly or widen the nearby areas."
    )


def web_search_property(address: str, city: str, state: str, **_kw) -> str:
    if not address or not city or not state:
        return "I need address, city, and state to search this property."

    redfin_url = _redfin_address_url(address, city, state)
    log.info("web_search_property redfin url: %s", redfin_url)

    try:
        client = ScraperClient(timeout=120.0)
        result = client.scrape(
            url=redfin_url,
            formats=["markdown", "metadata"],
            only_main_content=True,
            render_js=True,
            wait_ms=1000,
        )
    except Exception as exc:
        log.error("web_search_property failed: %s", exc)
        return f"Couldn't fetch property details from Redfin right now.\nRedfin URL used: {redfin_url}"

    metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
    markdown = (result.get("markdown", "") if isinstance(result, dict) else "").strip()
    title = metadata.get("title") or "No title"
    final_url = metadata.get("final_url") or redfin_url
    status = metadata.get("status_code", "?")

    preview = markdown[:2800] if markdown else "(no textual content extracted)"
    if len(markdown) > 2800:
        preview += "\n...[truncated]..."

    return (
        f"Property lookup complete.\n"
        f"Source: Redfin\n"
        f"Title: {title}\n"
        f"URL: {final_url}\n"
        f"Status: {status}\n\n"
        f"{preview}"
    )
