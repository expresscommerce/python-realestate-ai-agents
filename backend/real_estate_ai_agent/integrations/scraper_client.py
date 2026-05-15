"""Ghost-browser scraper client (no remote scraper API calls)."""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_GHOST_FALLBACK_ENABLED = os.getenv("GHOST_BROWSER_ENABLED", "1") == "1"


class ScraperClient:
    """Ghost-only scraper wrapper using local Playwright + cursor behavior."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        ghost_fallback: bool | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self.ghost_fallback = ghost_fallback if ghost_fallback is not None else _GHOST_FALLBACK_ENABLED

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def scrape(
        self,
        url: str,
        *,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        render_js: bool = False,
        wait_for_selector: str | None = None,
        wait_ms: int = 0,
        extra_headers: dict[str, str] | None = None,
        schema: dict[str, Any] | None = None,
        extraction_prompt: str | None = None,
    ) -> dict[str, Any]:
        del formats, only_main_content, render_js, wait_for_selector, extra_headers, schema, extraction_prompt
        return self._ghost_scrape_fallback(url, wait_ms=wait_ms)

    def map(self, url: str, *, max_links: int = 200, render_js: bool = False) -> dict[str, Any]:
        del render_js
        return self._ghost_map_fallback(url, max_links=max_links)

    def extract(
        self,
        url: str,
        schema: dict[str, Any],
        *,
        prompt: str | None = None,
        render_js: bool = False,
    ) -> dict[str, Any]:
        del schema, prompt, render_js
        ghost = self._ghost_scrape_fallback(url, wait_ms=1200)
        return {
            "data": {"listings": []},
            "markdown": ghost.get("markdown", ""),
            "metadata": ghost.get("metadata", {}),
        }

    def listings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ghost-only listings fallback based on Redfin listing links."""
        url = str(payload.get("url") or "")
        if not url:
            raise ValueError("listings payload requires 'url'")
        max_links = int(payload.get("max_listings") or 5)
        fallback = self._ghost_map_fallback(url, max_links=max(5, max_links * 3))
        links = fallback.get("links", []) if isinstance(fallback, dict) else []
        return {"results": [{"listing_url": link} for link in links if isinstance(link, str)]}

    def start_crawl(
        self,
        url: str,
        *,
        max_depth: int | None = None,
        max_pages: int | None = None,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        same_origin_only: bool = True,
        render_js: bool = False,
    ) -> dict[str, Any]:
        del max_depth, max_pages, include_paths, exclude_paths, same_origin_only, render_js
        links = self._ghost_map_fallback(url, max_links=200).get("links", [])
        return {"status": "completed", "links": links}

    def get_crawl(self, job_id: str) -> dict[str, Any]:
        del job_id
        return {"status": "not_supported_in_ghost_only_mode"}

    def cancel_crawl(self, job_id: str) -> dict[str, Any]:
        del job_id
        return {"status": "not_supported_in_ghost_only_mode"}

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "mode": "ghost_only"}

    # -- Ghost-browser fallback helpers -----------------------------------

    def _ghost_scrape_fallback(self, url: str, *, wait_ms: int = 2000) -> dict[str, Any]:
        from .ghost_browser import ghost_scrape_sync

        log.info("GhostBrowser scrape: %s", url)
        return ghost_scrape_sync(url, wait_ms=wait_ms, headless=True, timeout_ms=int(self.timeout * 1000))

    def _ghost_map_fallback(self, url: str, *, max_links: int = 200) -> dict[str, Any]:
        from .ghost_browser import ghost_extract_links_sync

        log.info("GhostBrowser map: %s", url)
        links = ghost_extract_links_sync(url, max_links=max_links, headless=True, timeout_ms=int(self.timeout * 1000))
        return {"links": links}


def get_default_client() -> ScraperClient:
    return ScraperClient()
