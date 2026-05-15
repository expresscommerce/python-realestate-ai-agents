"""Web scraping tools."""

from __future__ import annotations

import json
import logging

from ..integrations.scraper_client import ScraperClient

log = logging.getLogger(__name__)


def scrape_web_page(url: str, render_js: bool = True) -> str:
    if not url:
        return "Please provide a valid URL."
    try:
        client = ScraperClient(timeout=120.0)
        result = client.scrape(
            url=url,
            formats=["markdown", "metadata"],
            only_main_content=True,
            render_js=render_js,
            wait_ms=1000 if render_js else 0,
        )
    except Exception as exc:
        log.error("scrape_web_page failed: %s", exc)
        return "Couldn't reach the scraper service. Please ensure it is running and try again."

    metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
    markdown = (result.get("markdown", "") if isinstance(result, dict) else "").strip()
    title = metadata.get("title") or "Untitled page"
    final_url = metadata.get("final_url") or url

    preview = markdown[:3500] if markdown else ""
    if len(markdown) > 3500:
        preview += "\n\n...[content truncated]..."

    return (
        f"Scraped page successfully.\n"
        f"Title: {title}\n"
        f"Final URL: {final_url}\n\n"
        f"Content:\n{preview or '(no textual content extracted)'}"
    )


def extract_web_data(
    url: str,
    schema_json: str | dict | None = None,
    prompt: str = "",
    render_js: bool = True,
) -> str:
    if not url:
        return "Please provide a valid URL."

    default_schema: dict = {
        "type": "object",
        "properties": {
            "address": {"type": "string"},
            "price": {"type": ["string", "number", "null"]},
            "beds": {"type": ["string", "number", "null"]},
            "baths": {"type": ["string", "number", "null"]},
            "sqft": {"type": ["string", "number", "null"]},
        },
        "required": ["address"],
    }

    schema: dict
    if schema_json is None or schema_json == "":
        schema = default_schema
    elif isinstance(schema_json, dict):
        schema = schema_json
    elif isinstance(schema_json, str):
        try:
            schema = json.loads(schema_json)
            if not isinstance(schema, dict):
                return "schema_json must be a JSON object."
        except json.JSONDecodeError:
            return "schema_json is invalid JSON."
    else:
        return "schema_json must be either a JSON string or object."

    try:
        client = ScraperClient(timeout=120.0)
        result = client.extract(
            url=url,
            schema=schema,
            prompt=prompt or None,
            render_js=render_js,
        )
    except Exception as exc:
        log.error("extract_web_data failed: %s", exc)
        return "Couldn't extract data. Please try again."

    data = result.get("data", {}) if isinstance(result, dict) else {}
    return f"Extraction complete from {url}.\nExtracted JSON:\n{json.dumps(data, indent=2)}"
