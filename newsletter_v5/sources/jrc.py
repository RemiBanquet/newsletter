"""
JRC MARS — EU Joint Research Centre crop monitoring bulletins scraper.

Scrapes MARS (Monitoring Agricultural Resources) bulletins published by the
European Commission Joint Research Centre. These monthly/bi-monthly bulletins
provide crop monitoring data and outlook for Europe and the Mediterranean region.

Target: https://publications.jrc.ec.europa.eu/repository/search?sort=date-desc&filter=SCIENCE_AREA%3AS001%7CGROUP%3AG001&query=JRC+Mars+Bulletin
Method: requests + BeautifulSoup (static HTML)
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import aiohttp

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import (
    build_publication,
    fetch_html,
    parse_date_flexible,
    parse_html,
)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

JRC_BASE = "https://publications.jrc.ec.europa.eu"
JRC_MARS_URL = (
    "https://publications.jrc.ec.europa.eu/repository/search?"
    "sort=date-desc&filter=SCIENCE_AREA%3AS001%7CGROUP%3AG001&"
    "query=JRC+Mars+Bulletin"
)

JRC_GEOLOCATION = GeoLocation(
    place_name="European Union",
    country_iso="EU",
    latitude=50.85,
    longitude=4.35,
)

# Acceptable MARS bulletin titles
MARS_BULLETIN_PATTERNS = [
    r"JRC MARS Bulletin - Crop monitoring in Europe",
    r"JRC MARS Bulletin - Global outlook - Crop monitoring European neighbourhood",
]


async def scrape_jrc(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape JRC MARS crop monitoring bulletins.

    Strategy:
    1. Fetch the JRC MARS search results page
    2. Look for links with CSS selector a.search-entry-title
    3. Filter to only accept MARS bulletin titles
    4. Extract publication dates from detail pages or nearby elements
    5. Apply 7-day lookback (MARS bulletins are monthly/bi-monthly)
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    html = await fetch_html(source.url or JRC_MARS_URL, session)
    if not html:
        logger.warning("JRC: could not fetch MARS search page")
        return publications

    soup = parse_html(html)

    # Find all bulletin title links
    for link in soup.find_all("a", class_="search-entry-title"):
        title = link.get_text(strip=True)
        href = link.get("href", "").strip()

        if not title or not href:
            continue

        # Filter to only accepted MARS bulletin types
        if not _is_valid_mars_title(title):
            logger.debug(f"JRC: skipping non-MARS bulletin: {title[:60]}")
            continue

        # Make URL absolute
        if href.startswith("/"):
            href = urljoin(JRC_BASE, href)

        # Try to extract date from the entry or detail page
        pub_date = await _extract_mars_date(href, session)

        # Apply cutoff
        if pub_date and pub_date < cutoff:
            continue

        # If no date could be extracted, skip
        if not pub_date:
            logger.debug(f"JRC: skipping undated bulletin: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=href,
            source_name="JRC MARS",
            country="Europe",
            flag_emoji="🇪🇺",
            published_at=pub_date,
            language="en",
            location=JRC_GEOLOCATION,
        ))

    logger.info(f"JRC: found {len(publications)} MARS bulletins")
    return publications


def _is_valid_mars_title(title: str) -> bool:
    """Check if title matches expected MARS bulletin patterns."""
    for pattern in MARS_BULLETIN_PATTERNS:
        if re.search(pattern, title, re.I):
            return True
    return False


async def _extract_mars_date(url: str, session: aiohttp.ClientSession) -> Optional[datetime]:
    """
    Extract publication date from a MARS bulletin detail page.

    Tries to fetch the detail page and look for date fields, or extracts
    from the URL if it contains a date pattern.
    """
    # Try to extract from URL first (e.g., /2026-03-20/ or similar)
    date_match = re.search(r"/(\d{4})[/-](\d{2})[/-](\d{2})", url)
    if date_match:
        try:
            year = int(date_match.group(1))
            month = int(date_match.group(2))
            day = int(date_match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    # Fetch the detail page and look for date metadata
    detail_html = await fetch_html(url, session, timeout=15)
    if not detail_html:
        return None

    soup = parse_html(detail_html)

    # Look for common date element patterns
    for selector in [
        ("time", {"datetime": True}),
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "publish_date"}),
    ]:
        if selector[0] == "time":
            time_el = soup.find("time")
            if time_el:
                date_str = time_el.get("datetime", "")
                if date_str:
                    parsed = parse_date_flexible(date_str)
                    if parsed:
                        return parsed
        else:
            tag, attrs = selector
            meta_el = soup.find(tag, attrs=attrs)
            if meta_el:
                content = meta_el.get("content", "")
                if content:
                    parsed = parse_date_flexible(content)
                    if parsed:
                        return parsed

    # Try to find date in common text patterns
    for text_el in soup.find_all(["p", "span", "div"], class_=re.compile(r"date|published|release", re.I)):
        text = text_el.get_text(strip=True)
        parsed = parse_date_flexible(text)
        if parsed:
            return parsed

    return None
