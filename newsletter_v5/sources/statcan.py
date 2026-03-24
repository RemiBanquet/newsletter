"""
Statistics Canada — Crop production statistics scraper.

Scrapes the Statistics Canada agriculture section for crop production
statistics, estimates, and related reports. StatsCan publishes comprehensive
agricultural and crop data for Canada.

Target: https://www150.statcan.gc.ca/n1/en/subjects/agriculture_and_food/crop_production?count=10
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

STATCAN_BASE = "https://www150.statcan.gc.ca"
STATCAN_CROP_URL = (
    "https://www150.statcan.gc.ca/n1/en/subjects/"
    "agriculture_and_food/crop_production?count=10"
)

STATCAN_GEOLOCATION = GeoLocation(
    place_name="Canada",
    country_iso="CA",
    latitude=56.13,
    longitude=-106.35,
)

# Agriculture keywords for filtering
AG_KEYWORDS = [
    "agricultur", "crop", "harvest", "cereal", "grain", "oilseed",
    "wheat", "barley", "maize", "corn", "rice", "production",
    "farm", "cultivation", "sowing", "seeding", "yield",
    "acreage", "planting", "livestock", "agricultural", "farm",
]


def _is_agriculture_related(text: str) -> bool:
    """Check if text is agriculture-related."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in AG_KEYWORDS)


async def scrape_statcan(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape Statistics Canada for crop production publications.

    Strategy:
    1. Fetch the crop production subject page
    2. Look for div.ndm-result-container elements
    3. Extract title from div.ndm-result-title a
    4. Extract date from div.ndm-result-date span.ndm-result-date (YYYY-MM-DD format)
    5. Deduplicate by link URL
    6. Filter to agriculture-relevant entries
    7. Apply 48h lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    seen_urls = set()

    html = await fetch_html(source.url or STATCAN_CROP_URL, session)
    if not html:
        logger.warning("StatsCan: could not fetch crop production page")
        return publications

    soup = parse_html(html)

    # Find result containers
    for container in soup.find_all("div", class_="ndm-result-container"):
        # Extract title and link
        title_el = container.find("div", class_="ndm-result-title")
        if not title_el:
            continue

        link = title_el.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"].strip()

        if not title or not href:
            continue

        # Make URL absolute
        if href.startswith("/"):
            href = urljoin(STATCAN_BASE, href)

        # Deduplicate by link
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # Filter to agriculture-related entries
        if not _is_agriculture_related(title):
            logger.debug(f"StatsCan: skipping non-agriculture entry: {title[:60]}")
            continue

        # Extract date from div.ndm-result-date span.ndm-result-date
        date_str = ""
        date_el = container.find("div", class_="ndm-result-date")
        if date_el:
            # Look for the last span with class ndm-result-date
            date_spans = date_el.find_all("span", class_="ndm-result-date")
            if date_spans:
                date_str = date_spans[-1].get_text(strip=True)

        pub_date = None
        if date_str:
            pub_date = parse_date_flexible(date_str)

        # Apply cutoff
        if pub_date and pub_date < cutoff:
            continue

        # Skip undated items
        if not pub_date:
            logger.debug(f"StatsCan: skipping undated item: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=href,
            source_name="Statistics Canada",
            country="Canada",
            flag_emoji="🇨🇦",
            published_at=pub_date,
            language="en",
            location=STATCAN_GEOLOCATION,
        ))

        # Max 10 unique publications
        if len(publications) >= 10:
            break

    logger.info(f"StatsCan: found {len(publications)} crop production publications")
    return publications
