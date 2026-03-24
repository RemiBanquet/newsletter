"""
Stat Gov UA Ukraine — State Statistics Service crop datasets scraper.

Scrapes the Ukrainian State Statistics Service (Stat Gov UA) for recent
agricultural datasets, crop production statistics, and land use data
for Ukraine.

Target: https://stat.gov.ua/en/search?f%5B0%5D=content_type%3Adataset&f%5B1%5D=topics%3A178&search_api_fulltext=crops&sort_by=created
Base: https://stat.gov.ua

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

GOVUA_BASE = "https://stat.gov.ua"
GOVUA_SEARCH_URL = "https://stat.gov.ua/en/search?f%5B0%5D=content_type%3Adataset&f%5B1%5D=topics%3A178&search_api_fulltext=crops&sort_by=created"

GOVUA_GEOLOCATION = GeoLocation(
    place_name="Ukraine",
    country_iso="UA",
    latitude=48.38,
    longitude=31.17,
)


def _is_crops_related(title: str) -> bool:
    """Check if title contains 'crops' (case-insensitive)."""
    return "crops" in title.lower()


def _extract_date_from_info(info_text: str) -> Optional[str]:
    """
    Extract date from info text matching pattern "DD Mon, YYYY" (e.g., "10 Jun, 2025").

    Examples:
    - "Published on 10 Jun, 2025" -> "10 Jun, 2025"
    - "10 Jun, 2025" -> "10 Jun, 2025"
    """
    if not info_text:
        return None

    # Match patterns like "10 Jun, 2025" or "24 Mar, 2026"
    date_pattern = r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec),?\s+\d{4}"
    match = re.search(date_pattern, info_text, re.IGNORECASE)
    if match:
        return match.group()

    return None


async def scrape_govua(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape Stat Gov UA crop datasets.

    Strategy:
    1. Fetch the crop dataset search results page
    2. Extract result entries from div.views-row elements (limit to first 10)
    3. Parse title from div.node__title h2 a
    4. Filter to titles containing "crops" (case-insensitive)
    5. Extract date from div.node__title-info (pattern "DD Mon, YYYY")
    6. Make URLs absolute
    7. Filter by 48-hour lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or GOVUA_SEARCH_URL, session)
    if not html:
        logger.warning("Stat Gov UA: could not fetch dataset search results")
        return publications

    soup = parse_html(html)

    # Find all div.views-row elements (limit to first 10)
    entries = soup.find_all("div", class_="views-row")[:10]

    for entry in entries:
        # Title in div.node__title h2 a
        title_div = entry.find("div", class_="node__title")
        if not title_div:
            continue

        h2 = title_div.find("h2")
        if not h2:
            continue

        title_link = h2.find("a", href=True)
        if not title_link:
            continue

        title = title_link.get_text(strip=True)
        url = title_link.get("href", "").strip()

        if not title or not url:
            continue

        # Filter to crops-related titles only
        if not _is_crops_related(title):
            continue

        # Make URL absolute
        if url.startswith("/"):
            url = urljoin(GOVUA_BASE, url)

        # Date in div.node__title-info (pattern "DD Mon, YYYY")
        date_str = ""
        info_div = title_div.find("div", class_="node__title-info")
        if info_div:
            info_text = info_div.get_text(strip=True)
            date_str = _extract_date_from_info(info_text) or ""

        pub_date = parse_date_flexible(date_str) if date_str else None
        if pub_date and pub_date < cutoff:
            continue

        if not pub_date:
            logger.debug(f"Stat Gov UA: skipping entry with unparseable date: {date_str}")
            continue

        publications.append(build_publication(
            title=title,
            url=url,
            source_name="Stat Gov UA",
            country="Ukraine",
            flag_emoji="🇺🇦",
            published_at=pub_date,
            language="en",
            location=GOVUA_GEOLOCATION,
        ))

    logger.info(f"Stat Gov UA: found {len(publications)} crop datasets")
    return publications
