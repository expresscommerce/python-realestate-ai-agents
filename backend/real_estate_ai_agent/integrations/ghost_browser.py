"""Ghost-cursor Playwright browser with human-like anti-bot evasion.

Implements Bezier-curve mouse movement, realistic scrolling, random typing
cadence, and stealth patches to bypass fingerprint-based bot detection.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bezier math for ghost-cursor movement
# ---------------------------------------------------------------------------

Point = tuple[float, float]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _bezier_point(pts: Sequence[Point], t: float) -> Point:
    """De Casteljau evaluation of an arbitrary-order Bezier curve."""
    n = len(pts)
    if n == 1:
        return pts[0]
    reduced = [
        (_lerp(pts[i][0], pts[i + 1][0], t), _lerp(pts[i][1], pts[i + 1][1], t))
        for i in range(n - 1)
    ]
    return _bezier_point(reduced, t)


def _distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _generate_bezier_path(
    start: Point,
    end: Point,
    *,
    steps: int | None = None,
    overshoot_spread: float = 0.15,
) -> list[Point]:
    """Generate a natural-looking Bezier path between two screen points.

    Uses a cubic Bezier with randomised control points to simulate the
    kind of arc a human hand produces with a mouse / trackpad.  An
    optional overshoot beyond the target followed by correction makes
    the movement even more realistic (Fitts's Law behaviour).
    """
    dist = _distance(start, end)
    if steps is None:
        steps = max(20, int(dist / 4))

    # Randomised control points off the straight line.
    dx, dy = end[0] - start[0], end[1] - start[1]
    spread = max(30, dist * overshoot_spread)
    cp1 = (
        start[0] + dx * random.uniform(0.2, 0.45) + random.gauss(0, spread * 0.4),
        start[1] + dy * random.uniform(0.2, 0.45) + random.gauss(0, spread * 0.4),
    )
    cp2 = (
        start[0] + dx * random.uniform(0.55, 0.8) + random.gauss(0, spread * 0.3),
        start[1] + dy * random.uniform(0.55, 0.8) + random.gauss(0, spread * 0.3),
    )

    # Optionally overshoot.
    do_overshoot = dist > 80 and random.random() < 0.35
    if do_overshoot:
        overshoot_dist = random.uniform(4, min(25, dist * 0.12))
        angle = math.atan2(dy, dx)
        overshoot_pt: Point = (
            end[0] + math.cos(angle) * overshoot_dist,
            end[1] + math.sin(angle) * overshoot_dist,
        )
    else:
        overshoot_pt = end

    pts = [start, cp1, cp2, overshoot_pt]
    path: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        path.append(_bezier_point(pts, t))

    if do_overshoot:
        correction_steps = random.randint(5, 12)
        for i in range(1, correction_steps + 1):
            t = i / correction_steps
            path.append((_lerp(overshoot_pt[0], end[0], t), _lerp(overshoot_pt[1], end[1], t)))

    return path


# ---------------------------------------------------------------------------
# Stealth JS patches injected into every page
# ---------------------------------------------------------------------------

_STEALTH_SCRIPTS: list[str] = [
    # Hide webdriver flag
    """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """,
    # Fake plugins array (Chrome-like)
    """
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' },
            ];
            plugins.length = 3;
            return plugins;
        },
    });
    """,
    # Fake languages
    """
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    """,
    # Chrome runtime stub
    """
    if (!window.chrome) {
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
    }
    """,
    # Permissions query patch
    """
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params);
    """,
    # Canvas fingerprint noise
    """
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const style = ctx.fillStyle;
            ctx.fillStyle = 'rgba(0,0,1,0.003)';
            ctx.fillRect(0, 0, 1, 1);
            ctx.fillStyle = style;
        }
        return _toDataURL.apply(this, arguments);
    };
    """,
    # WebGL vendor/renderer spoof
    """
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return 'Intel Inc.';
        if (param === 37446) return 'Intel Iris OpenGL Engine';
        return getParam.call(this, param);
    };
    """,
]

STEALTH_INIT_SCRIPT = "\n".join(_STEALTH_SCRIPTS)


# ---------------------------------------------------------------------------
# User-Agent pool (recent Chrome on desktop)
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]


# ---------------------------------------------------------------------------
# GhostCursor — human-like cursor controller bound to a Page
# ---------------------------------------------------------------------------

@dataclass
class GhostCursor:
    """Drives a Playwright Page with human-like mouse behaviour."""

    page: Page
    _pos: Point = (0.0, 0.0)

    async def move_to(self, x: float, y: float) -> None:
        path = _generate_bezier_path(self._pos, (x, y))
        for px, py in path:
            await self.page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.002, 0.012))
        self._pos = (x, y)

    async def click_at(self, x: float, y: float, *, button: str = "left") -> None:
        await self.move_to(x, y)
        await asyncio.sleep(random.uniform(0.04, 0.15))
        await self.page.mouse.down(button=button)
        await asyncio.sleep(random.uniform(0.03, 0.12))
        await self.page.mouse.up(button=button)

    async def click_element(self, selector: str, *, timeout: float = 10_000) -> None:
        el = await self.page.wait_for_selector(selector, timeout=timeout)
        if not el:
            raise ValueError(f"Element not found: {selector}")
        box = await el.bounding_box()
        if not box:
            raise ValueError(f"Element has no bounding box: {selector}")
        x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
        await self.click_at(x, y)

    async def human_scroll(
        self,
        *,
        direction: str = "down",
        distance: int | None = None,
        steps: int | None = None,
    ) -> None:
        """Scroll in small, variable increments like a real scroll wheel."""
        total = distance or random.randint(300, 900)
        n = steps or random.randint(4, 10)
        sign = -1 if direction == "up" else 1
        for _ in range(n):
            chunk = (total / n) * random.uniform(0.6, 1.4)
            await self.page.mouse.wheel(0, sign * chunk)
            await asyncio.sleep(random.uniform(0.05, 0.20))

    async def human_type(self, selector: str, text: str) -> None:
        await self.click_element(selector)
        for ch in text:
            await self.page.keyboard.press(ch)
            await asyncio.sleep(random.uniform(0.04, 0.18))

    async def random_idle(self, min_s: float = 0.5, max_s: float = 2.5) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def random_mouse_jitter(self, n: int = 3) -> None:
        """Small random cursor movements that mimic idle fidgeting."""
        for _ in range(n):
            dx = random.gauss(0, 15)
            dy = random.gauss(0, 10)
            nx = max(0, self._pos[0] + dx)
            ny = max(0, self._pos[1] + dy)
            await self.move_to(nx, ny)
            await asyncio.sleep(random.uniform(0.1, 0.5))


# ---------------------------------------------------------------------------
# GhostBrowser — managed Playwright lifecycle with stealth
# ---------------------------------------------------------------------------

@dataclass
class GhostBrowser:
    """Stealth Playwright browser with ghost-cursor integration.

    Usage::

        async with GhostBrowser() as gb:
            page, cursor = await gb.new_page()
            await page.goto("https://www.redfin.com/...")
            await cursor.human_scroll()
            content = await page.content()
    """

    headless: bool = True
    slow_mo: int = 0
    _pw: Playwright | None = field(default=None, repr=False, init=False)
    _browser: Browser | None = field(default=None, repr=False, init=False)
    _context: BrowserContext | None = field(default=None, repr=False, init=False)

    async def launch(self) -> None:
        self._pw = await async_playwright().start()
        ua = random.choice(_USER_AGENTS)
        vp = random.choice(_VIEWPORTS)
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="en-US",
            timezone_id="America/Chicago",
            color_scheme="light",
            java_script_enabled=True,
            bypass_csp=False,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-CH-UA": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
            },
        )
        await self._context.add_init_script(STEALTH_INIT_SCRIPT)
        log.info("GhostBrowser launched (headless=%s, UA=%s)", self.headless, ua[:60])

    async def new_page(self) -> tuple[Page, GhostCursor]:
        if not self._context:
            raise RuntimeError("Browser not launched — call launch() or use async with")
        page = await self._context.new_page()
        vp = page.viewport_size or {"width": 1920, "height": 1080}
        start_x = random.uniform(vp["width"] * 0.3, vp["width"] * 0.7)
        start_y = random.uniform(vp["height"] * 0.3, vp["height"] * 0.7)
        cursor = GhostCursor(page=page, _pos=(start_x, start_y))
        return page, cursor

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._context = self._browser = self._pw = None

    async def __aenter__(self) -> GhostBrowser:
        await self.launch()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# High-level helper: scrape a URL with full ghost-cursor evasion
# ---------------------------------------------------------------------------

async def ghost_scrape(
    url: str,
    *,
    wait_ms: int = 2000,
    scroll: bool = True,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    """Scrape *url* using a stealth browser with ghost-cursor behaviour.

    Returns ``{"html": str, "markdown": str, "metadata": dict}``.
    """
    async with GhostBrowser(headless=headless) as gb:
        page, cursor = await gb.new_page()

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            log.error("ghost_scrape navigation failed: %s", exc)
            return {"html": "", "markdown": "", "metadata": {"error": str(exc)}}

        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Mimic human: small idle jitter, then scroll down the page.
        await cursor.random_mouse_jitter(n=random.randint(2, 4))

        if scroll:
            for _ in range(random.randint(2, 5)):
                await cursor.human_scroll(direction="down")
                await cursor.random_idle(0.4, 1.2)

        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

        html = await page.content()

        # Lightweight HTML → text extraction (no heavy deps).
        title = await page.title()
        final_url = page.url
        status = resp.status if resp else 0

        # Try to extract readable text via the browser itself.
        markdown = await page.evaluate("""
            () => {
                const sel = document.querySelector('main') || document.body;
                return sel ? sel.innerText : '';
            }
        """) or ""

    return {
        "html": html,
        "markdown": markdown,
        "metadata": {
            "title": title,
            "final_url": final_url,
            "status_code": status,
        },
    }


async def ghost_extract_links(
    url: str,
    *,
    link_pattern: str = "",
    max_links: int = 200,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> list[str]:
    """Navigate to *url* with stealth and extract matching links."""
    async with GhostBrowser(headless=headless) as gb:
        page, cursor = await gb.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            log.error("ghost_extract_links navigation failed: %s", exc)
            return []

        await asyncio.sleep(random.uniform(0.8, 1.5))
        await cursor.random_mouse_jitter(n=2)

        for _ in range(random.randint(2, 4)):
            await cursor.human_scroll(direction="down")
            await cursor.random_idle(0.3, 1.0)

        hrefs: list[str] = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.href)
                        .filter(h => h.startsWith('http'))
        """)

    if link_pattern:
        import re
        pat = re.compile(link_pattern)
        hrefs = [h for h in hrefs if pat.search(h)]

    seen: set[str] = set()
    unique: list[str] = []
    for h in hrefs:
        clean = h.split("?")[0]
        if clean not in seen:
            seen.add(clean)
            unique.append(clean)
            if len(unique) >= max_links:
                break
    return unique


# ---------------------------------------------------------------------------
# Redfin listing card extraction from search page
# ---------------------------------------------------------------------------

_REDFIN_CARD_JS = """
() => {
    const cards = document.querySelectorAll(
        '[class*="HomeCard"], [class*="homecard"], [class*="MapHomeCard"], ' +
        '[data-rf-test-id="photo-card"], .HomeViews, .bottomV2, ' +
        '[class*="RentalCard"], [class*="rentalCard"], [class*="listingCard"], ' +
        '[class*="PropertyCard"], [class*="HomeViews"]'
    );
    const results = [];
    for (const card of cards) {
        // Find listing detail link (rental pages use /apartment/ as well as /home/).
        const linkEl = card.querySelector('a[href*="/home/"]')
                    || card.querySelector('a[href*="/apartment/"]')
                    || card.querySelector('a[href]');
        const href = linkEl ? linkEl.href : '';
        if (!href || !href.includes('redfin.com')) continue;
        const normalizedHref = href.split('?')[0];
        if (
            normalizedHref.includes('/search') ||
            normalizedHref.includes('/city/') ||
            normalizedHref.includes('/state/') ||
            normalizedHref.endsWith('/rentals-near-me')
        ) continue;

        const text = card.innerText || '';

        // Price: $6,831+/mo or $123,456 or $1,234/mo
        const priceMatch = text.match(/\\$(\\d[\\d,]+)/);
        const price = priceMatch ? '$' + priceMatch[1] : null;

        // Beds: "1 bed", "2 beds", "1-2 bed", "Studio"
        const bedMatch = text.match(/(\\d+(?:\\.\\d+)?)\\s*(?:beds?|bd|Beds?|BD)/i);
        const studioMatch = !bedMatch && /\\bstudio\\b/i.test(text);
        const beds = bedMatch ? parseFloat(bedMatch[1]) : (studioMatch ? 0 : null);

        // Baths: "1 bath", "1-2 bath"
        const bathMatch = text.match(/(\\d+(?:\\.\\d+)?)(?:-\\d+)?\\s*(?:baths?|ba|Baths?|BA)/i);
        const baths = bathMatch ? parseFloat(bathMatch[1]) : null;

        // Sqft: "762 sq ft", "762-1,146 sq ft"
        const sqftMatch = text.match(/([\\d,]+)(?:-[\\d,]+)?\\s*(?:sq\\.?\\s*ft|Sq\\.?\\s*Ft|SF)/i);
        const sqft = sqftMatch ? parseInt(sqftMatch[1].replace(/,/g, ''), 10) : null;

        // Address: try specific elements, then parse from URL.
        const addrEl = card.querySelector(
            '[class*="address"], [class*="Address"], .homeAddressV2, .link-and-anchor'
        );
        let address = addrEl ? addrEl.innerText.trim() : null;
        // Clean up pipe separators from rental cards ("Waterline Square | 400 W 61st St, ...")
        if (address) {
            const pipeIdx = address.indexOf('|');
            if (pipeIdx > -1) {
                address = address.substring(pipeIdx + 1).trim();
            }
            address = address.replace(/\\u00a0/g, ' ').trim();
        }
        if (!address) {
            const addrTextMatch = text.match(/[|\\u00a0]+\\s*([^\\n]+?,\\s*[A-Z]{2}\\s*\\d{5})/);
            if (addrTextMatch) {
                address = addrTextMatch[1].trim();
            }
        }
        if (!address) {
            const parts = href.split('/');
            const homeIdx = parts.findIndex(p => p === 'home' || p === 'apartment');
            const addrIdx = homeIdx - 1;
            if (addrIdx >= 0 && parts[addrIdx]) {
                address = decodeURIComponent(parts[addrIdx]).replace(/-/g, ' ');
            }
        }

        if (price || beds !== null || baths || sqft || address) {
            results.push({ address, price, beds, baths, sqft, url: normalizedHref });
        }
    }
    const seen = new Set();
    return results.filter(r => {
        if (seen.has(r.url)) return false;
        seen.add(r.url);
        return true;
    });
}
"""


async def ghost_scrape_listings(
    url: str,
    *,
    max_listings: int = 10,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> list[dict[str, Any]]:
    """Scrape a Redfin search page and return structured listing cards.

    Returns a list of dicts with keys: address, price, beds, baths, sqft, url.
    """
    async with GhostBrowser(headless=headless) as gb:
        page, cursor = await gb.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            log.error("ghost_scrape_listings navigation failed: %s", exc)
            return []

        await asyncio.sleep(random.uniform(1.0, 2.0))
        await cursor.random_mouse_jitter(n=random.randint(2, 4))

        # Scroll down multiple times to load lazy-rendered cards.
        for _ in range(random.randint(3, 6)):
            await cursor.human_scroll(direction="down")
            await cursor.random_idle(0.4, 1.0)

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Extract structured card data from the DOM.
        try:
            cards: list[dict] = await page.evaluate(_REDFIN_CARD_JS)
        except Exception as exc:
            log.warning("ghost_scrape_listings JS extraction failed: %s", exc)
            cards = []

        if not cards:
            # Fallback 1: parse innerText with regex.
            log.info("ghost_scrape_listings: card JS returned 0, falling back to text parse")
            cards = await _parse_listings_from_text(page, url)

        if not cards:
            # Fallback 2 (important for NYC rentals): extract listing links from the
            # results page and enrich by scraping each listing detail page.
            log.info("ghost_scrape_listings: text parse returned 0, falling back to link-enrichment")
            links = await _extract_listing_links_from_page(page, max_links=max(10, max_listings * 4))
            cards = await _enrich_listing_links(gb, links, max_listings=max_listings)

    return cards[:max_listings]


async def _extract_listing_links_from_page(page: Page, *, max_links: int = 100) -> list[str]:
    """Extract unique Redfin listing detail links from current results page."""
    hrefs: list[str] = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href]'))
                  .map(a => a.href.split('?')[0])
                  .filter(h => h.includes('redfin.com'))
                  .filter(h => h.includes('/home/') || h.includes('/apartment/'))
        """
    ) or []
    seen: set[str] = set()
    out: list[str] = []
    for h in hrefs:
        if h in seen:
            continue
        seen.add(h)
        out.append(h)
        if len(out) >= max_links:
            break
    return out


async def _enrich_listing_links(gb: GhostBrowser, links: list[str], *, max_listings: int) -> list[dict[str, Any]]:
    """Open listing detail pages and extract key fields."""
    import re

    results: list[dict[str, Any]] = []
    for link in links[: max(5, max_listings * 2)]:
        page, cursor = await gb.new_page()
        try:
            await page.goto(link, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(random.uniform(0.6, 1.2))
            await cursor.human_scroll(direction="down", distance=random.randint(200, 600), steps=random.randint(2, 5))
            text = await page.evaluate(
                """
                () => {
                    const sel = document.querySelector('main') || document.body;
                    return sel ? sel.innerText : '';
                }
                """
            ) or ""
            title = await page.title()
        except Exception as exc:
            log.debug("enrich listing failed for %s: %s", link, exc)
            await page.close()
            continue
        await page.close()

        # Parse key fields from text/title.
        price_m = re.search(r"\$[\d,]+", text)
        beds_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:Beds?|bd)\b", text, re.I)
        baths_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:Baths?|ba)\b", text, re.I)
        sqft_m = re.search(r"([\d,]+)\s*(?:Sq\.?\s*Ft|sqft|square feet)\b", text, re.I)
        addr_m = re.search(r"\d+\s+[^,\n]+,\s*[^,\n]+,\s*[A-Z]{2}\s*\d{5}", text)
        if not addr_m and title:
            # Many Redfin detail pages have full address in title.
            title_addr = title.split("|", 1)[0].strip()
            if re.search(r"\d+\s+.+,\s*.+,\s*[A-Z]{2}", title_addr):
                addr = title_addr
            else:
                addr = None
        else:
            addr = addr_m.group(0) if addr_m else None

        if not any((price_m, beds_m, baths_m, sqft_m, addr)):
            continue

        results.append(
            {
                "address": addr,
                "price": price_m.group(0) if price_m else None,
                "beds": float(beds_m.group(1)) if beds_m else None,
                "baths": float(baths_m.group(1)) if baths_m else None,
                "sqft": int(sqft_m.group(1).replace(",", "")) if sqft_m else None,
                "url": link,
            }
        )
        if len(results) >= max_listings:
            break

    return results


async def _parse_listings_from_text(page: Page, search_url: str) -> list[dict[str, Any]]:
    """Regex-parse listing data from page innerText as last resort."""
    import re

    text = await page.evaluate("""
        () => {
            const sel = document.querySelector('main') || document.body;
            return sel ? sel.innerText : '';
        }
    """) or ""

    # Rental prices look like "$6,831+/mo"; sale prices like "$450,000".
    price_re = re.compile(r'\$(\d[\d,]+)\+?(?:/mo)?')
    bed_re = re.compile(r'(\d+(?:\.\d+)?)\s*(?:beds?|bd)\b', re.I)
    studio_re = re.compile(r'\bstudio\b', re.I)
    bath_re = re.compile(r'(\d+(?:\.\d+)?)(?:-\d+)?\s*(?:baths?|ba)\b', re.I)
    sqft_re = re.compile(r'([\d,]+)(?:-[\d,]+)?\s*(?:sq\.?\s*ft|sqft|SF)\b', re.I)
    addr_re = re.compile(
        r'\d+\s+[\w\s]+(?:St|Ave|Rd|Dr|Ln|Blvd|Ct|Way|Pkwy|Cir|Pl|Ter|Trl)',
        re.I,
    )
    # Rental cards often show address after a pipe: "| 400 W 61st St, New York, NY 10023"
    addr_pipe_re = re.compile(r'\|\s*(.+?,\s*[A-Z]{2}\s*\d{5})')

    hrefs: list[str] = await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href.split('?')[0])
                    .filter(h => h.includes('redfin.com'))
                    .filter(h => h.includes('/home/') || h.includes('/apartment/'))
    """) or []
    seen_urls: set[str] = set()
    unique_urls: list[str] = []
    for h in hrefs:
        if h not in seen_urls:
            seen_urls.add(h)
            unique_urls.append(h)

    lines = text.splitlines()
    results: list[dict] = []
    i = 0
    url_idx = 0
    while i < len(lines):
        line = lines[i].strip()
        pm = price_re.search(line)
        if not pm:
            i += 1
            continue

        window = "\n".join(lines[max(0, i - 3): i + 6])
        price_raw = "$" + pm.group(1)
        price_num = int(pm.group(1).replace(",", ""))
        if price_num < 500:
            i += 1
            continue

        bm = bed_re.search(window)
        is_studio = not bm and studio_re.search(window)
        btm = bath_re.search(window)
        sm = sqft_re.search(window)
        am = addr_pipe_re.search(window) or addr_re.search(window)

        link = unique_urls[url_idx] if url_idx < len(unique_urls) else None
        url_idx += 1

        results.append({
            "address": am.group(1).strip() if am and am.lastgroup else (am.group(0).strip() if am else None),
            "price": price_raw,
            "beds": float(bm.group(1)) if bm else (0.0 if is_studio else None),
            "baths": float(btm.group(1)) if btm else None,
            "sqft": int(sm.group(1).replace(",", "")) if sm else None,
            "url": link,
        })
        i += 4

    return results


# Sync wrappers for non-async callers (tools.py uses sync code).


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine from synchronous code, handling existing event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def ghost_scrape_sync(
    url: str,
    *,
    wait_ms: int = 2000,
    scroll: bool = True,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    """Synchronous wrapper around :func:`ghost_scrape`."""
    return _run_async(
        ghost_scrape(
            url,
            wait_ms=wait_ms,
            scroll=scroll,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    )


def ghost_extract_links_sync(
    url: str,
    *,
    link_pattern: str = "",
    max_links: int = 200,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> list[str]:
    """Synchronous wrapper around :func:`ghost_extract_links`."""
    return _run_async(
        ghost_extract_links(
            url,
            link_pattern=link_pattern,
            max_links=max_links,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    )


def ghost_scrape_listings_sync(
    url: str,
    *,
    max_listings: int = 10,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> list[dict[str, Any]]:
    """Synchronous wrapper around :func:`ghost_scrape_listings`."""
    return _run_async(
        ghost_scrape_listings(
            url,
            max_listings=max_listings,
            headless=headless,
            timeout_ms=timeout_ms,
        )
    )
