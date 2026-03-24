"""
Destatis Germany — Federal Statistical Office field crop statistics scraper.

Scrapes Destatis (Statistisches Bundesamt) crop and agriculture statistics
for recent field crop production estimates, agricultural economic data, and
land use statistics for Germany.

Target: https://www.destatis.de/SiteGlobals/Forms/Suche/EN/Expertensuche_Formular.html?templateQueryString=crops&cl2Taxonomies_Themen_0=land_forstwirtschaft_fischerei#searchresults
Base: https://www.destatis.de

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

DESTATIS_BASE = "https://www.destatis.de"
DESTATIS_SEARCH_URL = "https://www.destatis.de/SiteGlobals/Forms/Suche/EN/Expertensuche_Formular.html?templateQueryString=crops&cl2Taxonomies_Themen_0=land_forstwirtschaft_fischerei#searchresults"

DESTATIS_GEOLOCATION = GeoLocation(
    place_name="Germany",
    country_iso="DE",
    latitude=52.52,
    longitude=13.41,
)


def _clean_title(title: str) -> str:
    """
    Clean up title by removing date prefixes and "Date:" labels.

    Examples:
    - "May 19, 2025 ... Title" -> "Title"
    - "Date: April 10, 2025 ... Title" -> "Title"
    """
    if not title:
        return ""

    # Remove "Date:" prefix
    title = re.sub(r"^Date:\s*", "", title, flags=re.IGNORECASE)

    # Remove date patterns at the beginning (e.g., "May 19, 2025" or "April 10, 2025")
    # Pattern: Month Day(,) Year at the start, followed by whitespace or punctuation
    title = re.sub(
        r"^(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\s*[\.\-\|]?\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return title.strip()


async def scrape_destatis(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape Destatis crop statistics search results.

    Strategy:
    1. Fetch the crop search results page
    2. Extract result entries from div.c-result elements (limit to first 10)
    3. Parse title from h3.c-result__heading a, link, and date
    4. Clean up title (remove date prefixes)
    5. Make URLs absolute
    6. Filter by 48-hour lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or DESTATIS_SEARCH_URL, session)
    if not html:
        logger.warning("Destatis: could not fetch search results")
        return publications

    soup = parse_html(html)

    # Find all div.c-result elements (limit to first 10)
    entries = soup.find_all("div", class_="c-result")[:10]

    for entry in entries:
        # Title in h3.c-result__heading a
        heading = entry.find("h3", class_="c-result__heading")
        if not heading:
            continue

        title_link = heading.find("a", href=True)
        if not title_link:
            continue

        title = title_link.get_text(strip=True)
        url = title_link.get("href", "").strip()

        if not title or not url:
            continue

        # Clean up title (remove date prefixes)
        title = _clean_title(title)

        # Make URL absolute
        if url.startswith("/"):
            url = urljoin(DESTATIS_BASE, url)
        elif url.startswith("EN/"):
            url = urljoin(DESTATIS_BASE, "/" + url)

        # Date in span.c-result__date within the heading
        date_str = ""
        date_span = heading.find("span", class_="c-result__date")
        if date_span:
            date_str = date_span.get_text(strip=True)

        pub_date = parse_date_flexible(date_str) if date_str else None
        if pub_date and pub_date < cutoff:
            continue

        if not pub_date:
            logger.debug(f"Destatis: skipping entry with unparseable date: {date_str}")
            continue

        publications.append(build_publication(
            title=title,
            url=url,
            source_name="Destatis",
            country="Germany",
            flag_emoji="🇩🇪",
            published_at=pub_date,
            language="en",
            location=DESTATIS_GEOLOCATION,
        ))

    logger.info(f"Destatis: found {len(publications)} crop statistics publications")
    return publications
