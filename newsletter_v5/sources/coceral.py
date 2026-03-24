"""
COCERAL Europe — European crop forecast bulletins scraper.

Scrapes COCERAL (Comité du Commerce et de l'Alimentation) crop forecast bulletins
for recent agricultural production forecasts and crop estimates across Europe.

Target: https://www.coceral.com/web/coceral%20crop%20forecast%7C%20june%202025/1011306087/list1187970814/f1.html

Method: requests + BeautifulSoup (static HTML)
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

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

COCERAL_URL = "https://www.coceral.com/web/coceral%20crop%20forecast%7C%20june%202025/1011306087/list1187970814/f1.html"

COCERAL_GEOLOCATION = GeoLocation(
    place_name="Europe",
    country_iso="EU",
    latitude=50.85,
    longitude=4.35,
)


def _extract_date_from_text(text: str) -> Optional[str]:
    """
    Extract date from text matching pattern "DD Month YYYY" (e.g., "10 June 2025").

    Uses regex to find the pattern and returns the matched date string.
    """
    if not text:
        return None

    # Match patterns like "24 March 2026" or "10 June 2025"
    date_pattern = r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}"
    match = re.search(date_pattern, text, re.IGNORECASE)
    if match:
        return match.group()

    return None


async def scrape_coceral(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape COCERAL crop forecast bulletins.

    Strategy:
    1. Fetch the crop forecast page
    2. Extract publication entries from div.antpar1 elements (limit to first 10)
    3. Parse title from h1 tag and date from div.par1descr strong
    4. Use page URL as the publication link
    5. Filter by 48-hour lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or COCERAL_URL, session)
    if not html:
        logger.warning("COCERAL: could not fetch crop forecast page")
        return publications

    soup = parse_html(html)

    # Find all div.antpar1 elements (limit to first 10)
    entries = soup.find_all("div", class_="antpar1")[:10]

    for entry in entries:
        # Title in h1 tag
        title_elem = entry.find("h1")
        if not title_elem:
            continue

        title = title_elem.get_text(strip=True)
        if not title:
            continue

        # Date in div.par1descr strong
        date_str = ""
        descr_elem = entry.find("div", class_="par1descr")
        if descr_elem:
            strong_elem = descr_elem.find("strong")
            if strong_elem:
                date_text = strong_elem.get_text(strip=True)
                # Extract date from the text
                date_str = _extract_date_from_text(date_text)

        pub_date = parse_date_flexible(date_str) if date_str else None
        if pub_date and pub_date < cutoff:
            continue

        if not pub_date:
            logger.debug(f"COCERAL: skipping entry with unparseable date: {date_str}")
            continue

        # Use the page URL as the publication link
        url = source.url or COCERAL_URL

        publications.append(build_publication(
            title=title,
            url=url,
            source_name="COCERAL",
            country="Europe",
            flag_emoji="🇪🇺",
            published_at=pub_date,
            language="en",
            location=COCERAL_GEOLOCATION,
        ))

    logger.info(f"COCERAL: found {len(publications)} crop forecast bulletins")
    return publications
