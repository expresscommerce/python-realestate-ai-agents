import logging

log = logging.getLogger(__name__)

DEFAULT_SEARCH_STATE = {
    "city": None,
    "state": None,
    "listing_type": None,
    "property_type": None,
    "bedrooms": None,
    "bathrooms_min": None,
    "price_min": None,
    "price_max": None,
    "pending_city": None,
}

def update_search_state(state, tool_name, args):
    if tool_name != "search_listings":
        return

    for key, value in args.items():
        if value not in ("", None, 0):
            state[key] = value

    log.info("Search state updated: %s", state)


_TOOL_VALID_KEYS = {
    "city", "state", "listing_type", "property_type", "bedrooms",
    "bathrooms_min", "price_min", "price_max", "limit", "offset",
}


def merge_search_state(state, tool_name, args):

    if tool_name != "search_listings":
        return args

    merged = {k: v for k, v in state.items() if k in _TOOL_VALID_KEYS}

    merged.update(
        {
            k: v
            for k, v in args.items()
            if v not in ("", None, 0)
        }
    )

    log.info("Search state merged for %s: %s", tool_name, merged)
    return merged