"""Listing state management: stores structured property search results.

Separate from chat history to avoid re-sending listing data to the LLM.
Supports current search + history stack for cross-session references.

Structure:
{
    "current": {
        "results": [listings...],
        "total": 25,
        "offset": 0,
        "limit": 5,
        "search_params": {city, state, price_max, ...}
    },
    "history": [
        {
            "results": [...],
            "total": 25,
            "search_params": {...}
        }
    ]
}
"""

import copy
import logging

log = logging.getLogger(__name__)

DEFAULT_LISTING_STATE = {
    "current": {
        "results": [],
        "total": 0,
        "offset": 0,
        "limit": 5,
        "search_params": {},
    },
    "history": [],
}

MAX_HISTORY_ENTRIES = 10


def update_listing_state(state, listings, search_params, offset=0, limit=5):
    """Store new search results as current. Move old current to history."""
    if state["current"]["results"]:
        state["history"].append({
            "results": state["current"]["results"],
            "total": state["current"]["total"],
            "search_params": state["current"]["search_params"],
        })
        if len(state["history"]) > MAX_HISTORY_ENTRIES:
            state["history"] = state["history"][-MAX_HISTORY_ENTRIES:]

    state["current"] = {
        "results": listings,
        "total": len(listings),
        "offset": offset,
        "limit": limit,
        "search_params": search_params,
    }
    log.info(
        "Listing state updated: %d current, %d history",
        len(listings), len(state["history"]),
    )


def get_current_page(state):
    """Get the current page of listings based on offset and limit."""
    results = state["current"]["results"]
    offset = state["current"]["offset"]
    limit = state["current"]["limit"]
    return results[offset: offset + limit]


def get_listing_by_option(state, option_number):
    """Get a specific listing by option number (1-indexed)."""
    results = state["current"]["results"]
    if 1 <= option_number <= len(results):
        return results[option_number - 1]
    return None


def get_listings_from_history(state, city):
    """Find listings from history by city name."""
    city_lower = city.lower().strip()
    for entry in reversed(state["history"]):
        params = entry.get("search_params", {})
        if params.get("city", "").lower().strip() == city_lower:
            return entry
    return None


def paginate(state, direction="next"):
    """Move offset forward or backward. Returns True if successful."""
    current = state["current"]
    total = current["total"]
    offset = current["offset"]
    limit = current["limit"]

    if direction == "next":
        new_offset = offset + limit
        if new_offset >= total:
            return False
        current["offset"] = new_offset
        return True
    elif direction == "prev":
        new_offset = offset - limit
        if new_offset < 0:
            return False
        current["offset"] = max(0, new_offset)
        return True
    return False


def clear_current(state):
    """Clear current listings but keep history."""
    state["current"] = copy.deepcopy(DEFAULT_LISTING_STATE["current"])
    log.info("Listing state current cleared")
