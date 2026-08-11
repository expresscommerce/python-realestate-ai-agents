"""PropertyBot tools for LLM function calling."""

from .listings import search_listings, web_search_property
from .visits import schedule_visit, check_availability
from .web import scrape_web_page, extract_web_data

__all__ = [
    "search_listings",
    "web_search_property",
    "schedule_visit",
    "check_availability",
    "scrape_web_page",
    "extract_web_data",
    "TOOL_DEFS",
    "run_tool",
]

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "search_listings",
            "description": "Search property listings in any US city by scraping Redfin. URL is generated automatically from city/state/budget/bedrooms.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. Austin"},
                    "state": {"type": "string", "description": "Two-letter state code, e.g. TX"},
                    "listing_type": {"type": "string", "enum": ["sale", "rent"]},
                    "property_type": {"type": "string", "enum": ["SFR", "CONDO", "MFR", "LAND", "MOBILE", "OTHER"]},
                    "bedrooms": {"type": "integer", "description": "Exact number of bedrooms"},
                    "bathrooms_min": {"type": "integer"},
                    "price_min": {"type": "integer"},
                    "price_max": {"type": "integer"},
                    "limit": {"type": "integer", "description": "Number of listings to return (default 5, max 10)"},
                    "offset": {"type": "integer", "description": "Skip first N results for pagination. Use when user asks 'show more' (e.g. offset=5 to show next batch)."},
                },
                "required": ["city", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_property",
            "description": "Look up a specific property by address on Redfin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "city": {"type": "string"},
                    "state": {"type": "string"},
                },
                "required": ["address", "city", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_visit",
            "description": "Book a property visit. All five fields are required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_id": {"type": "string"},
                    "client_name": {"type": "string"},
                    "client_phone": {"type": "string"},
                    "preferred_date": {"type": "string"},
                    "preferred_time": {"type": "string"},
                },
                "required": ["property_id", "client_name", "client_phone", "preferred_date", "preferred_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check available visit slots for a property over the next 7 days.",
            "parameters": {
                "type": "object",
                "properties": {"property_id": {"type": "string"}},
                "required": ["property_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_web_page",
            "description": "Scrape a webpage URL and return clean markdown + metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "render_js": {"type": "boolean", "default": True},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_web_data",
            "description": "Extract structured JSON from a webpage URL using a schema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "schema_json": {"type": "string"},
                    "prompt": {"type": "string"},
                    "render_js": {"type": "boolean", "default": True},
                },
                "required": ["url"],
            },
        },
    },
]

_DISPATCH = {
    "search_listings": lambda **kw: search_listings(**kw),
    "web_search_property": lambda **kw: web_search_property(**kw),
    "schedule_visit": lambda **kw: schedule_visit(**kw),
    "check_availability": lambda **kw: check_availability(**kw),
    "scrape_web_page": lambda **kw: scrape_web_page(**kw),
    "extract_web_data": lambda **kw: extract_web_data(**kw),
}


def run_tool(name: str, arguments: dict) -> str | dict:
    import logging
    log = logging.getLogger(__name__)
    fn = _DISPATCH.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return fn(**arguments)
    except TypeError as exc:
        log.warning("Tool %s arg error: %s", name, exc)
        return f"Missing or invalid arguments for {name}."
    except Exception as exc:
        log.error("Tool %s failed: %s", name, exc)
        return f"Something went wrong running {name}. Please try again."
