"""Chat pipeline: LLM with tool calling via litellm.

Flow: user message -> LLM -> (optional tool calls -> LLM) -> response.
Max 5 tool-call rounds to prevent infinite loops.

When search/detail tools return actual listings, the output is returned
directly to prevent the LLM from summarizing away data. When no listings
are found, the LLM handles the response naturally (suggestions, alternatives).

Pagination, option references, and cross-session lookups are handled
directly from listing_state without re-sending data to the LLM.
"""

import json
import logging
import re

import litellm

from .config import DEEPINFRA_API_KEY, DEEPINFRA_MODEL, MAX_HISTORY
from .listing_state import (
    get_current_page,
    get_listing_by_option,
    get_listings_from_history,
    paginate,
    update_listing_state,
)
from .prompts import SYSTEM_PROMPT
from .search_state import merge_search_state, update_search_state
from .session import get_listing_state, get_search_state, save_listing_state, save_search_state
from .tools import TOOL_DEFS, run_tool

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5

LISTING_TOOLS = {"search_listings"}

_BUDGET_RE = re.compile(
    r"\$[\d,]+|"
    r"\d+\s*k\b|"
    r"\bbudget\b|"
    r"\bunder\b|"
    r"\bover\b|"
    r"\bmax\s*(?:price|budget)?\b|"
    r"\bup\s*to\b|"
    r"\bafford\b|"
    r"\bprice\s*range\b",
    re.I,
)
_BEDROOMS_RE = re.compile(
    r"\d+[\s-]*(?:bed|bedroom|br|bd)|"
    r"(?:bed|bedroom|br|bd)s?[\s-]*\d+|"
    r"\bstudio\b|"
    r"\b(?:one|two|three|four|five)[\s-]*(?:bed|bedroom)|"
    r"\b(?:bed|bedroom)s?[\s-]*(?:one|two|three|four|five)\b",
    re.I,
)

_LOCATION_CHANGE_RE = re.compile(
    r"\b(?:change|switch|move|go|update)\s+from\s+.+?\s+to\s+(.+?)(?:\s+instead)?\s*$",
    re.I,
)
_LOCATION_CHANGE_TO_RE = re.compile(
    r"\b(?:change|switch|move|go|update)\s+(?:to|in)\s+(?:the\s+)?(.+?)(?:\s+instead)?\s*$",
    re.I,
)
_LOCATION_IN_RE = re.compile(
    r"\b(?:search|find|look)\s+(?:in|at|for)\s+(.+?)(?:\s+instead)?\s*$",
    re.I,
)
_MAKE_IT_RE = re.compile(
    r"\b(?:make\s+it)\s+(.+?)(?:\s+instead)?\s*$",
    re.I,
)


def _recent_user_content(history: list[dict], *, max_user_msgs: int = 6) -> str:
    """Return concatenated content of the last few user turns."""
    chunks: list[str] = []
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        chunks.append(str(msg.get("content", "")))
        if len(chunks) >= max_user_msgs:
            break
    chunks.reverse()
    return "\n".join(chunks)


def _recent_user_mentioned(history: list[dict], pattern: re.Pattern) -> bool:  # type: ignore[type-arg]
    """Check pattern in recent user turns."""
    return bool(pattern.search(_recent_user_content(history)))


def _build_messages(history: list[dict], session_id: str = None) -> list[dict]:
    """Build messages for LLM with smart trimming."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if session_id:
        state = get_search_state(session_id)
        active_filters = {k: v for k, v in state.items() if v is not None}
        if active_filters:
            state_msg = f"[Current search filters: {active_filters}]"
            messages.append({"role": "system", "content": state_msg})

        listing_state = get_listing_state(session_id)
        current = listing_state["current"]
        if current["results"]:
            params = current.get("search_params", {})
            city = params.get("city", "")
            total = current["total"]
            offset = current["offset"]
            limit = current["limit"]
            showing_end = min(offset + limit, total)
            listing_msg = (
                f"[Active listing results: {total} total listings for "
                f"{city}, showing {offset + 1}–{showing_end}. "
                f"User can say 'show more', 'option N', or 'compare N and M'.]"
            )
            messages.append({"role": "system", "content": listing_msg})

    if len(history) <= MAX_HISTORY:
        return messages + history

    return messages + history[-MAX_HISTORY:]


def _call_llm(messages: list[dict], use_tools: bool = True):
    print(f"[LLM] Calling LLM (use_tools={use_tools}, messages={len(messages)})")
    kwargs: dict = {
        "model": DEEPINFRA_MODEL,
        "messages": messages,
        "api_key": DEEPINFRA_API_KEY,
        "api_base": "https://api.deepinfra.com/v1/openai",
        "temperature": 0.5,
        "max_tokens": 2048,
    }
    if use_tools:
        kwargs["tools"] = TOOL_DEFS
        kwargs["tool_choice"] = "auto"
    return litellm.completion(**kwargs)


# ── Listing view / pagination helpers ──

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
}

_COMPARE_RE = re.compile(
    r"\bcompar(?:e|ing)\s+(.+)",
    re.I,
)
_COMPARE_NUM_RE = re.compile(r"(?:option\s*)?(\d+)")
_SCHEDULE_INTENT_RE = re.compile(
    r"\b(?:schedule|visit|book|appointment|tour)\b", re.I
)

_OPTION_RE = re.compile(r"\boption\s*(\d+)\b", re.I)

_HISTORY_CITY_RE = re.compile(
    r"\bfrom\s+(?:the\s+)?(.+?)\s+(?:search|session|results|listings)\b",
    re.I,
)

_SIMPLE_PAGINATION = [
    "show more", "more listings", "next page", "next batch",
    "show next", "see more", "more results", "more options",
    "show previous", "go back", "previous page", "previous results",
]

NEXT_PATTERNS = [
    r"\bshow me more\b",
    r"\bshow more\b",
    r"\bgive me more\b",
    r"\bi want more\b",
    r"\bmore please\b",
    r"\bnext\b",
    r"\bnext\s+\d+\b",
    r"\banother\b",
    r"\banother\s+\d+\b",
    r"\bcontinue\b",
    r"\badditional\b",
    r"\bmore (?:houses|homes|properties|listings|results|options)\b",
]

PREV_PATTERNS = [
    r"\bprevious\b",
    r"\bgo back\b",
    r"\bback\b",
    r"\bearlier\b",
]

VISIT_PATTERN = [
    r"\bvisit\b",
    r"\bschedule\b",
    r"\bbook\b",
]


def _is_pagination_request(msg: str) -> str | None:
    msg = msg.lower().strip()

    # Next page
    for pattern in NEXT_PATTERNS:
        if re.search(pattern, msg):
            return "next"

    # Previous page
    for pattern in PREV_PATTERNS:
        if re.search(pattern, msg):
            return "prev"

    return None

def _visit_request(msg: str):
    msg = msg.lower().strip()

    for pattern in VISIT_PATTERN:
        if re.search(pattern, msg):
            return """ I'd be happy to schedule a visit. To book it, I'll need a few details from you:
                    1) Your full name
                    2) Your phone number
                    3) Preferred date for the visit
                    4) Preferred time for the visit"""
        
    return None


def _get_option_number(msg: str) -> int | None:
    """Extract option number from message."""
    m = _OPTION_RE.search(msg)
    if m:
        return int(m.group(1))
    msg_lower = msg.lower()
    for word, num in _ORDINALS.items():
        if word in msg_lower:
            return num
    return None


def _normalize_location(value: str) -> str:
    """Normalize location text for comparisons."""
    text = (value or "").strip().lower()
    text = text.replace("new york city", "new york")
    text = text.replace("nyc", "new york")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def _friendly_property_type(property_type: str | None) -> str:
    """Convert stored property-type values into user-facing labels."""
    mapping = {
        "SFR": "house",
        "CONDO": "apartment/condo",
        "MFR": "multi-family",
        "LAND": "land",
        "MOBILE": "mobile home",
        "OTHER": "property",
    }
    if not property_type:
        return "home"
    return mapping.get(property_type.upper(), property_type.lower())


def _get_contextual_follow_up(user_msg: str, search_state: dict, session_id: str = None) -> str | None:
    """Handle location-change follow-ups without sending them to the LLM."""
    existing_city = (search_state.get("city") or "").strip()
    if not existing_city:
        return None

    change_keywords = ("change", "switch", "move", "instead", "make it", "update", "different")
    if not any(keyword in user_msg.lower() for keyword in change_keywords):
        return None

    target_city = None

    location_match = _LOCATION_CHANGE_RE.search(user_msg)
    if location_match:
        target_city = location_match.group(1).strip()
    else:
        location_match = _LOCATION_CHANGE_TO_RE.search(user_msg)
        if location_match:
            target_city = location_match.group(1).strip()
        else:
            location_match = _LOCATION_IN_RE.search(user_msg)
            if location_match:
                target_city = location_match.group(1).strip()
            else:
                location_match = _MAKE_IT_RE.search(user_msg)
                if location_match:
                    target_city = location_match.group(1).strip()

    if not target_city:
        return None

    if _normalize_location(target_city) == _normalize_location(existing_city):
        return None

    # Store pending city for confirmation
    search_state["pending_city"] = target_city
    if session_id:
        save_search_state(session_id, search_state)

    property_type = _friendly_property_type(search_state.get("property_type"))
    return (
        f"I can search the same {property_type} requirements in {target_city}. "
        "Would you like me to search with the same budget and bedrooms? (yes/no)"
    )


def _get_comparison_numbers(msg: str) -> tuple[int, ...] | None:
    """Extract comparison option numbers. Supports 2 or more options."""
    m = _COMPARE_RE.search(msg)
    if not m:
        return None
    nums = [int(n) for n in _COMPARE_NUM_RE.findall(m.group(1))]
    if len(nums) >= 2:
        return tuple(nums)
    return None


def _format_single_listing(listing: dict, option_number: int = None) -> str:
    """Format a single listing dict for user display."""
    addr = listing.get("address") or "Address unavailable"
    price = listing.get("price") or "Price not listed"
    beds = listing.get("beds") or listing.get("bedrooms") or "\u2014"
    baths = listing.get("baths") or listing.get("bathrooms") or "\u2014"
    sqft = listing.get("sqft")
    link = listing.get("url") or listing.get("listing_url")
    city = listing.get("city") or ""
    state = listing.get("state") or ""

    lines = []
    if option_number is not None:
        lines.append(f"**Option {option_number}**")
    lines.append(f"- **Address:** {addr}")
    if city or state:
        lines.append(f"- **Location:** {city}, {state}")
    lines.append(f"- **Price:** {price}")
    lines.append(f"- **Beds/Baths:** {beds} bed / {baths} bath")
    if sqft:
        lines.append(f"- **Sqft:** {sqft}")
    if link:
        lines.append(f"- **View listing:** {link}")
    return "\n".join(lines)


def _handle_listing_view(session_id: str, user_msg: str, listing_state: dict) -> str | None:
    """Handle pagination, option references, comparisons from listing_state.

    Returns formatted response string, or None if the message doesn't match
    any listing-view pattern.
    """
    print(f"[listings.py] _handle_listing_view: results_count={len(listing_state['current']['results'])}, msg={user_msg!r}")
    if not listing_state["current"]["results"]:
        return None

    # ── Cross-session / history reference ──
    hist_match = _HISTORY_CITY_RE.search(user_msg)
    if hist_match:
        city = hist_match.group(1).strip()
        entry = get_listings_from_history(listing_state, city)
        if entry and entry["results"]:
            option_num = _get_option_number(user_msg)
            if option_num and 1 <= option_num <= len(entry["results"]):
                listing = entry["results"][option_num - 1]
                return (
                    f"From your **{city}** search:\n\n"
                    + _format_single_listing(listing, option_num)
                )
            lines = [f"Here are your **{city}** listings from a previous search:\n"]
            for i, l in enumerate(entry["results"][:10], 1):
                lines.append(_format_single_listing(l, i))
                lines.append("")
            return "\n".join(lines)

    # ── Comparison (handled by LLM in process_chat) ──
    if _get_comparison_numbers(user_msg):
        return None

    # ── Specific option reference ──
    if _SCHEDULE_INTENT_RE.search(user_msg):
        return None

    option_num = _get_option_number(user_msg)
    if option_num is not None:
        listing = get_listing_by_option(listing_state, option_num)
        if listing:
            return _format_single_listing(listing, option_num)

    # ── Pagination (text-based "show more" disabled; use accordion button in UI) ──
    direction = _is_pagination_request(user_msg)
    if direction:
        return "That's all the listings I found. If you'd like, I can help you compare specific options, look up more details on a particular property, or adjust your search (like expanding the budget or looking at nearby areas)."

    return None


# ── Main pipeline ──

def parse_price(price):
    if not price:
        return None
    return int(re.sub(r"[^\d]", "", str(price)))

def parse_sqft(sqft):
    if not sqft:
        return None
    return int(re.sub(r"[^\d]", "", str(sqft)))


def process_chat(session_id, history: list[dict]) -> str:
    """Run the chat pipeline. Mutates history in place. Returns the assistant reply."""

    user_msg = history[-1]["content"] if history else ""
    print(f"[chat] process_chat: session={session_id}, msg={user_msg!r}")

    # ── Check for listing view / pagination requests first ──
    listing_state = get_listing_state(session_id)
    print(f"[chat] listing_state: results={len(listing_state['current']['results'])}, total={listing_state['current']['total']}, offset={listing_state['current']['offset']}")
    view_response = _handle_listing_view(session_id, user_msg, listing_state)
    if view_response is not None:
        print(f"[chat] view_response handled directly, no LLM call")
        history.append({"role": "assistant", "content": view_response})
        return view_response
    
    visit_schedule = _visit_request(user_msg)
    if visit_schedule is not None:
        print(f"[chat] visit_schedule questions handled directly, no LLM call")
        history.append({"role": "assistant", "content":visit_schedule})
        return visit_schedule

    # ── LLM-based comparison ──
    comp = _get_comparison_numbers(user_msg)
    if comp:
        nums = list(comp)
        listings = []
        for n in nums:
            listings.append(get_listing_by_option(listing_state, n))

        for i, n in enumerate(nums):
            print("Option", n, "found:", listings[i] is not None)

        print("Current total:", len(listing_state["current"]["results"]))

        if all(listings):
            # Build dynamic column widths for the markdown table
            col_widths = [len(f"Option {n}") for n in nums]
            col_widths.append(len("Feature"))
            header_width = max(col_widths)

            def _row(label, values):
                cells = " | ".join(str(v) for v in values)
                return f"| {label} | {cells} |"

            headers = " | ".join(f"Option {n}" for n in nums)
            separators = " | ".join("-" * max(len(f"Option {n}"), 8) for n in nums)

            prices = [l.get("price") for l in listings]
            beds = [l.get("beds") for l in listings]
            baths = [l.get("baths") for l in listings]
            sqfts = [l.get("sqft") for l in listings]
            cities = [l.get("city") or "" for l in listings]
            states = [l.get("state") or "" for l in listings]
            addresses = [l.get("address") for l in listings]

            comparison = (
                f"**Property Comparison**\n\n"
                f"| Feature | {headers} |\n"
                f"|---------| {separators} |\n"
                + _row("Price", prices) + "\n"
                + _row("Beds/Baths", [f"{b} bed / {ba} bath" for b, ba in zip(beds, baths)]) + "\n"
                + _row("Sqft", sqfts) + "\n"
                + _row("Location", [f"{c}, {s}" for c, s in zip(cities, states)]) + "\n"
                + _row("Address", addresses) + "\n"
            )

            # Build LLM context dynamically
            facts_lines = []
            for n, l in zip(nums, listings):
                facts_lines.append(
                    f"Option {n}\n"
                    f"Price: {l.get('price')}\n"
                    f"Beds: {l.get('beds')}\n"
                    f"Baths: {l.get('baths')}\n"
                    f"Sqft: {l.get('sqft')}"
                )
            facts = "\n\n".join(facts_lines)

            option_labels = ", ".join(f"Option {n}" for n in nums[:-1]) + f", and Option {nums[-1]}"

            listing_context = f"""
            You are a real estate assistant.

            Facts:

            {facts}

            Compare {option_labels} on price, location, and size.
            Highlight the key trade-offs and recommend the best option.

            Return ONLY ONE paragraph beginning with:

            Bottom line:

            Do NOT repeat the facts.
            Do NOT rewrite the listings.
            Do NOT use headings.
            Do NOT ask questions.
            Maximum 4 sentences.
            """

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": listing_context},
                {"role": "user", "content": user_msg},
            ]

            option_str = " and ".join(str(n) for n in nums)
            print(f"[chat] LLM comparison: options {option_str}")
            try:
                resp = _call_llm(messages, use_tools=False)
                text = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                log.error("LLM comparison call failed: %s", exc)
                text = ""
            if not text:
                fallback = "Here's a quick comparison:\n\n"
                for n, l in zip(nums, listings):
                    fallback += _format_single_listing(l, n) + "\n\n"
                text = fallback.strip()
            final_response = comparison + "\n\n" + text
            history.append({"role": "assistant", "content": final_response})
            return final_response

    # ── Contextual follow-up handling (no LLM call) ──
    search_state = get_search_state(session_id)
    follow_up_reply = _get_contextual_follow_up(user_msg, search_state, session_id)
    if follow_up_reply is not None:
        print("[chat] contextual follow-up handled directly, no LLM call")
        history.append({"role": "assistant", "content": follow_up_reply})
        return follow_up_reply

    # ── Handle pending city confirmation ──
    pending_city = search_state.get("pending_city")
    if pending_city:
        affirmative = ("yes", "sure", "yeah", "yep", "same", "do it", "go ahead")
        if any(word in user_msg.lower() for word in affirmative):
            args = {
                "city": pending_city,
                "state": search_state.get("state", ""),
                "listing_type": search_state.get("listing_type", "sale"),
                "property_type": search_state.get("property_type", ""),
                "bedrooms": search_state.get("bedrooms", 0),
                "price_max": search_state.get("price_max", 0),
                "price_min": search_state.get("price_min", 0),
            }
            del search_state["pending_city"]
            save_search_state(session_id, search_state)

            result = run_tool("search_listings", args)
            update_search_state(search_state, "search_listings", args)
            save_search_state(session_id, search_state)

            if isinstance(result, dict):
                listing_state = get_listing_state(session_id)
                update_listing_state(
                    listing_state,
                    result.get("listings", []),
                    result.get("search_params", {}),
                    offset=args.get("offset", 0),
                    limit=args.get("limit", 5),
                )
                save_listing_state(session_id, listing_state)
                result_str = result.get("message", "")
            else:
                result_str = result if isinstance(result, str) else str(result)

            if isinstance(result, dict) and "Option " in result_str:
                tail = "\n\nWould you like more details on any of these, or should I adjust the search?"
                reply = result_str + tail
            else:
                reply = result_str

            history.append({"role": "assistant", "content": reply})
            return reply

    # ── Normal LLM flow ──
    messages = _build_messages(history, session_id)

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            resp = _call_llm(messages, use_tools=True)
        except Exception as exc:
            log.error("LLM call failed (round %d): %s", _round, exc)
            try:
                resp = _call_llm(messages, use_tools=False)
            except Exception as fallback_exc:
                log.error("LLM fallback also failed: %s", fallback_exc)
                reply = "I'm having trouble connecting right now. Please try again in a moment."
                history.append({"role": "assistant", "content": reply})
                return reply

        msg = resp.choices[0].message  # type: ignore[union-attr]
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            text = (msg.content or "").strip()
            if not text and _round == 0:
                log.warning("Empty LLM response on round 0, retrying")
                continue
            if not text:
                text = "How can I help you with your property search?"
            history.append({"role": "assistant", "content": text})
            return text

        # Record the assistant's tool-call message
        assistant_entry: dict = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        history.append(assistant_entry)
        messages.append(assistant_entry)

        # Execute each tool call
        listing_output = None
        listing_tool_name = None

        for tc in tool_calls:
            search_state = get_search_state(session_id)
            try:
                args = json.loads(tc.function.arguments)
                args = merge_search_state(search_state, tc.function.name, args)
            except (json.JSONDecodeError, TypeError):
                args = {}

            # Hard guard: block search_listings if user never mentioned
            # budget or bedrooms in any message this session.
            if tc.function.name == "search_listings":
                missing = []
                if not _recent_user_mentioned(history, _BUDGET_RE):
                    missing.append("budget (maximum price)")
                if not _recent_user_mentioned(history, _BEDROOMS_RE):
                    missing.append("number of bedrooms")
                print(f"[chat] hard_guard: missing={missing}, search_state={search_state}")
                if missing:
                    block_msg = (
                        "I still need a few details before I can search: "
                        + " and ".join(missing)
                        + ". Could you let me know?"
                    )
                    log.warning(
                        "Blocked search_listings — user never mentioned: %s",
                        ", ".join(missing),
                    )
                    tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": block_msg}
                    history.append(tool_msg)
                    messages.append(tool_msg)
                    history.append({"role": "assistant", "content": block_msg})
                    return block_msg

            result = run_tool(tc.function.name, args)
            update_search_state(search_state, tc.function.name, args)
            save_search_state(session_id, search_state)

            # ── Handle dict results from listing tools ──
            if isinstance(result, dict) and tc.function.name in LISTING_TOOLS:
                listing_state = get_listing_state(session_id)
                update_listing_state(
                    listing_state,
                    result.get("listings", []),
                    result.get("search_params", {}),
                    offset=args.get("offset", 0),
                    limit=args.get("limit", 5),
                )
                save_listing_state(session_id, listing_state)
                result_str = result.get("message", "")

                listing_output = result_str
                listing_tool_name = tc.function.name

                tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_str}
                history.append(tool_msg)
                messages.append(tool_msg)
                continue

            result_str = result if isinstance(result, str) else str(result)

            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_str}
            history.append(tool_msg)
            messages.append(tool_msg)

            if tc.function.name in LISTING_TOOLS:
                listing_output = result_str
                listing_tool_name = tc.function.name

        # When listing tools returned ACTUAL results, return them directly
        if listing_output is not None and "Option " in listing_output:
            reply = listing_output
            history.append({"role": "assistant", "content": reply})
            return reply

        if (
            listing_output is not None
            and listing_tool_name == "search_listings"
            and "couldn't find" in listing_output
        ):
            reply = listing_output
            history.append({"role": "assistant", "content": reply})
            return reply

    text = (msg.content or "").strip() if msg else ""  # type: ignore[possibly-undefined]
    if not text:
        text = "I've done several lookups — let me know how you'd like to proceed."
    history.append({"role": "assistant", "content": text})
    return text
