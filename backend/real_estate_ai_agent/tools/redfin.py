"""Redfin URL builders and mappings."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

PROPERTY_TYPE_MAP = {
    "house": "SFR",
    "apartment": "CONDO",
    "condo": "CONDO",
    "plot": "LAND",
    "land": "LAND",
    "commercial": "OTHER",
    "mobile": "MOBILE",
    "multi-family": "MFR",
    "townhouse": "SFR",
}

_PROPERTY_QUERY_LABEL = {
    "SFR": "house",
    "CONDO": "condo",
    "MFR": "multi family",
    "LAND": "land",
    "MOBILE": "mobile home",
}

_REDFIN_CITY_IDS = {
    "austin_tx": 30818,
    "houston_tx": 8903,
    "dallas_tx": 30794,
    "seattle_wa": 16163,
    "miami_fl": 11458,
    "new-york_ny": 30749,
    "new-york-city_ny": 30749,
    "san-francisco_ca": 17151,
    "los-angeles_ca": 11203,
    "chicago_il": 29470,
    "queens_ny": 30749,
    "brooklyn_ny": 30749,
    "bronx_ny": 30749,
    "staten-island_ny": 30749,
    "jersey-city_nj": 9880,
    "boston_ma": 1826,
    "denver_co": 11093,
    "phoenix_az": 14240,
    "atlanta_ga": 30756,
    "nashville_tn": 25640,
    "san-diego_ca": 16904,
    "portland_or": 14734,
    "charlotte_nc": 4319,
    "raleigh_nc": 15028,
    "tampa_fl": 18142,
    "orlando_fl": 13585,
    "salt-lake-city_ut": 16578,
    "minneapolis_mn": 12072,
}

_REDFIN_NEIGHBORHOOD_IDS: dict[str, int] = {}

_REDFIN_PTYPE_FILTER = {
    "SFR": "house",
    "CONDO": "condo",
    "MFR": "multifamily",
    "LAND": "land",
    "MOBILE": "mobile",
}


def _redfin_rent_path_segment(property_type: str) -> str:
    """Path segment for rentals. Redfin ignores filter/status=for-rent on the default /city/... for-sale URL."""
    raw = (property_type or "").strip()
    if not raw:
        return "apartments-for-rent"
    normalized = PROPERTY_TYPE_MAP.get(raw.lower(), raw.upper())
    n = str(normalized).upper()
    if n in ("SFR", "MOBILE"):
        return "houses-for-rent"
    if n == "CONDO":
        return "apartments-for-rent"
    if n == "MFR":
        return "townhomes-for-rent"
    if n == "LAND":
        return "apartments-for-rent"
    return "apartments-for-rent"


def _normalize_city_slug(city: str, state_slug: str) -> str:
    """Normalize user-entered city text into a stable Redfin slug key."""
    raw = (city or "").strip().lower()
    raw = re.sub(r"[.,_/]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    if raw in {"nyc", "new york city"}:
        return "new-york"

    st = state_slug.lower()
    if raw.endswith(f" {st}"):
        raw = raw[: -(len(st) + 1)].strip()
    elif raw == st:
        raw = ""

    if raw.endswith(" city") and raw not in {"salt lake city", "jersey city"}:
        raw = raw[: -len(" city")].strip()

    return raw.replace(" ", "-")


def _redfin_search_url(
    city: str,
    state: str,
    *,
    listing_type: str = "sale",
    price_min: int = 0,
    price_max: int = 0,
    bedrooms: int = 0,
    property_type: str = "",
) -> str:
    """Build Redfin URL automatically (prefer path-style over hash URLs)."""
    if bedrooms and bedrooms > 5:
        bedrooms = 5
    state_slug = state.strip().upper()
    city_slug = _normalize_city_slug(city, state_slug)
    if state_slug == "NY" and city_slug in {"queens", "brooklyn", "bronx", "staten-island", "manhattan"}:
        city_slug = "new-york"
    key = f"{city_slug}_{state_slug.lower()}"
    city_id = _REDFIN_CITY_IDS.get(key)
    neighborhood_id = _REDFIN_NEIGHBORHOOD_IDS.get(key)

    if neighborhood_id:
        base = f"https://www.redfin.com/neighborhood/{neighborhood_id}/{state_slug}/New-York/{city_slug.title()}"
    elif city_id:
        base = f"https://www.redfin.com/city/{city_id}/{state_slug}/{city_slug}"
    else:
        base = None

    if base:
        if listing_type == "rent":
            rent_seg = _redfin_rent_path_segment(property_type)
            if bedrooms:
                rent_seg = f"{bedrooms}-bedroom-{rent_seg}"
            base = f"{base}/{rent_seg}"
            return base

        filters: list[str] = []
        if bedrooms:
            filters.append(f"min-beds={bedrooms}")
            filters.append(f"max-beds={bedrooms}")
        if price_max:
            filters.append(f"max-price={price_max}")
        if price_min:
            filters.append(f"min-price={price_min}")
        if property_type:
            normalized = PROPERTY_TYPE_MAP.get(property_type.lower(), property_type.upper())
            ptype = _REDFIN_PTYPE_FILTER.get(str(normalized).upper())
            if ptype:
                filters.append(f"property-type={ptype}")
        return f"{base}/filter/{','.join(filters)}" if filters else base

    parts: list[str] = [f"{city} {state}", "homes"]
    parts.append("for rent" if listing_type == "rent" else "for sale")
    if bedrooms:
        parts.append(f"{bedrooms} bed")
    if price_min and price_max:
        parts.append(f"{price_min} to {price_max}")
    elif price_max:
        parts.append(f"under {price_max}")
    elif price_min:
        parts.append(f"over {price_min}")
    if property_type:
        normalized = PROPERTY_TYPE_MAP.get(property_type.lower(), property_type.upper())
        label = _PROPERTY_QUERY_LABEL.get(str(normalized).upper())
        if label:
            parts.append(label)
    query = " ".join(parts)
    return f"https://www.redfin.com/search#{quote_plus(query)}"


def _redfin_address_url(address: str, city: str, state: str) -> str:
    address_slug = address.strip().replace(" ", "-")
    city_slug = city.strip().replace(" ", "-")
    state_slug = state.strip().upper()
    return f"https://www.redfin.com/{state_slug}/{city_slug}/{address_slug}"
