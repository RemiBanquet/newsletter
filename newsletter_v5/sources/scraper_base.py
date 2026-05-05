"""
Base utilities for requests+BeautifulSoup publication scrapers.

All scrapers follow the same pattern:
1. Fetch HTML from a URL using aiohttp
2. Parse with BeautifulSoup
3. Extract publication entries (title, URL, date, summary)
4. Return list[Publication]

Each scraper is an async function with signature:
    async def scrape_xxx(source: SourceConfig, session: aiohttp.ClientSession) -> list[Publication]
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from models import GeoLocation, Publication

logger = logging.getLogger(__name__)

# ── Shared constants ──────────────────────────────────────────────

DEFAULT_TIMEOUT = 30  # seconds
# Many government statistics portals (MAPA, KSH, Destatis, ISTAT) 403 custom UAs.
# Use a real-browser UA to bypass naive WAF rules. Site-specific scrapers can
# override `headers` when calling fetch_html().
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,es;q=0.7,de;q=0.6,it;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ── Shared helpers ────────────────────────────────────────────────

def make_id(url: str, title: str = "") -> str:
    """
    Generate a short hash ID from URL + title.

    Some scrapers (Agreste, MAPA Avances, ISTAT) extract multiple distinct
    publications from a single SPA-style page where every entry reports
    the SAME URL. Hashing on URL alone collapses them to a single dedup
    key. Including the title gives each publication a unique identity.

    `title` is optional for backward compatibility; pass it whenever
    available.
    """
    key = f"{url.strip()}|{title.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


async def fetch_html(
    url: str,
    session: aiohttp.ClientSession,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[dict] = None,
) -> Optional[str]:
    """Fetch raw HTML from a URL. Returns None on failure."""
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        async with session.get(
            url,
            headers=hdrs,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.warning(f"Scraper: {url} returned status {resp.status}")
                return None
    except Exception as e:
        logger.warning(f"Scraper: failed to fetch {url}: {e}")
        return None


def parse_html(html: str) -> BeautifulSoup:
    """Parse HTML into a BeautifulSoup object."""
    return BeautifulSoup(html, "html.parser")


def parse_date_flexible(date_str: str) -> Optional[datetime]:
    """
    Try multiple date formats to parse a date string.
    Returns a timezone-aware datetime or None.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    formats = [
        "%d/%m/%Y",          # 24/03/2026 (common in EU)
        "%d-%m-%Y",          # 24-03-2026
        "%Y-%m-%d",          # 2026-03-24 (ISO)
        "%d %B %Y",          # 24 March 2026
        "%d %b %Y",          # 24 Mar 2026
        "%B %d, %Y",         # March 24, 2026 (US)
        "%b %d, %Y",         # Mar 24, 2026
        "%d.%m.%Y",          # 24.03.2026 (common in DE/TR)
        "%Y-%m-%dT%H:%M:%S", # ISO with time
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Try email-style dates (RFC 2822)
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass

    logger.debug(f"Could not parse date: '{date_str}'")
    return None


def build_publication(
    title: str,
    url: str,
    source_name: str,
    country: str,
    flag_emoji: str = "",
    published_at: Optional[datetime] = None,
    summary: str = "",
    language: str = "en",
    location: Optional[GeoLocation] = None,
) -> Publication:
    """Build a Publication object with sensible defaults."""
    return Publication(
        id=make_id(url, title),
        title=title.strip(),
        url=url.strip(),
        source_name=source_name,
        country=country,
        flag_emoji=flag_emoji,
        published_at=published_at,
        original_language=language,
        summary=summary.strip() if summary else "",
        relevant=True,
        location=location or GeoLocation(),
    )
