"""
UK DEFRA — Agricultural land use statistics scraper.

Scrapes the UK Department for Environment, Food and Rural Affairs (DEFRA)
research and statistics section for agricultural land use reports and updates.
DEFRA publishes official statistics on crop production, land use, and farm trends.

Target: https://www.gov.uk/search/all?content_purpose_supergroup[]=research_and_statistics&keywords=crops&order=updated-newest&organisations[]=department-for-environment-food-rural-affairs&page=1&parent=department-for-environment-food-rural-affairs
Base: https://www.gov.uk
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

DEFRA_BASE = "https://www.gov.uk"
DEFRA_SEARCH_URL = (
    "https://www.gov.uk/search/all?"
    "content_purpose_supergroup[]=research_and_statistics&"
    "keywords=crops&"
    "order=updated-newest&"
    "organisations[]=department-for-environment-food-rural-affairs&"
    "page=1&"
    "parent=department-for-environment-food-rural-affairs"
)

DEFRA_GEOLOCATION = GeoLocation(
    place_name="United Kingdom",
    country_iso="GB",
    latitude=55.38,
    longitude=-3.44,
)

# Agriculture keywords for filtering
AG_KEYWORDS = [
    "agricultur", "crop", "harvest", "cereal", "grain", "oilseed",
    "wheat", "barley", "maize", "corn", "rice", "production",
    "farm", "land use", "cultivation", "sowing", "yield",
    "acreage", "planting", "livestock", "farming",
]


def _is_agriculture_related(text: str) -> bool:
    """Check if text is agriculture-related."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in AG_KEYWORDS)


async def scrape_uk(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape UK DEFRA for agricultural land use statistics.

    Strategy:
    1. Fetch the DEFRA search results page for crop-related statistics
    2. Look for li.gem-c-document-list__item elements
    3. Extract title from div.gem-c-document-list__item-title a
    4. Filter to titles starting with "Agricultural land use"
    5. Extract date from time element (datetime attribute or text content)
    6. Apply 48h lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or DEFRA_SEARCH_URL, session)
    if not html:
        logger.warning("DEFRA: could not fetch search results page")
        return publications

    soup = parse_html(html)

    # Find document list items
    for item in soup.find_all("li", class_="gem-c-document-list__item"):
        # Extract title and link
        title_el = item.find("div", class_="gem-c-document-list__item-title")
        if not title_el:
            continue

        link = title_el.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"].strip()

        if not title or not href:
            continue

        # Filter to titles starting with "Agricultural land use"
        if not title.startswith("Agricultural land use"):
            logger.debug(f"DEFRA: skipping non-land-use item: {title[:60]}")
            continue

        # Make URL absolute
        if href.startswith("/"):
            href = urljoin(DEFRA_BASE, href)

        # Extract date from time element
        pub_date = None
        time_el = item.find("time")
        if time_el:
            # Try datetime attribute first
            date_str = time_el.get("datetime", "")
            if not date_str:
                # Fall back to text content
                date_str = time_el.get_text(strip=True)

            if date_str:
                pub_date = parse_date_flexible(date_str)

        # Apply cutoff
        if pub_date and pub_date < cutoff:
            continue

        # Skip undated items
        if not pub_date:
            logger.debug(f"DEFRA: skipping undated item: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=href,
            source_name="DEFRA UK",
            country="United Kingdom",
            flag_emoji="🇬🇧",
            published_at=pub_date,
            language="en",
            location=DEFRA_GEOLOCATION,
        ))

    logger.info(f"DEFRA: found {len(publications)} agricultural land use publications")
    return publications
