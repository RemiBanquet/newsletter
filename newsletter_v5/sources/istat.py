"""
ISTAT Italy — Agriculture press releases scraper.

Scrapes the ISTAT agriculture section for recent press releases and
statistical publications. ISTAT publishes crop production estimates,
agricultural economic accounts, and land use statistics.

Target: https://www.istat.it/en/statistical-themes/economy/agriculture/
Fallback: https://www.istat.it/en/tema/agriculture/feed/ (RSS, handled by fetcher.py)

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

ISTAT_BASE = "https://www.istat.it"
ISTAT_AGRICULTURE_URL = "https://www.istat.it/en/statistical-themes/economy/agriculture/"
# Alternative: the archivio (archive) page lists all press releases
ISTAT_ARCHIVE_URL = "https://www.istat.it/en/archivio/agriculture"

ISTAT_GEOLOCATION = GeoLocation(
    place_name="Italy",
    country_iso="IT",
    latitude=41.87,
    longitude=12.57,
)

# Agriculture keywords to filter ISTAT publications (they cover all domains)
AG_KEYWORDS = [
    "agricultur", "crop", "harvest", "cereal", "oilseed", "wheat", "maize",
    "corn", "rice", "olive", "wine", "fruit", "vegetable", "livestock",
    "farm", "land use", "agricol", "coltur", "semina", "raccolt",
    "produzione agricol", "superfici", "resa",
]


def _is_agriculture_related(text: str) -> bool:
    """Check if text is agriculture-related."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in AG_KEYWORDS)


async def scrape_istat(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape ISTAT agriculture press releases.

    Strategy:
    1. Try the agriculture theme page first (structured list of publications)
    2. If that fails, try the archive page
    3. Extract press release links with dates and titles
    4. Filter to agriculture-relevant entries
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)  # 3-day lookback

    # Try the main agriculture page
    html = await fetch_html(source.url or ISTAT_AGRICULTURE_URL, session)
    if not html:
        # Fallback to archive
        logger.info("ISTAT: main page failed, trying archive...")
        html = await fetch_html(ISTAT_ARCHIVE_URL, session)

    if not html:
        logger.warning("ISTAT: could not fetch any page")
        return publications

    soup = parse_html(html)

    # ISTAT uses several structures for listing publications.
    # Common patterns:
    # 1. <article> elements with <h2> or <h3> containing links
    # 2. <div class="archivio-item"> or similar archive containers
    # 3. <a> links within list items in the main content area

    entries = _extract_from_articles(soup) or _extract_from_links(soup)

    for entry in entries:
        title = entry.get("title", "").strip()
        url = entry.get("url", "").strip()
        date_str = entry.get("date", "")

        if not title or not url:
            continue

        # Make URL absolute
        if url.startswith("/"):
            url = urljoin(ISTAT_BASE, url)

        # Filter to agriculture only
        if not _is_agriculture_related(title):
            continue

        pub_date = parse_date_flexible(date_str)
        if pub_date and pub_date < cutoff:
            continue
        # If no date could be parsed, skip — undated items are likely old content
        if not pub_date:
            logger.debug(f"ISTAT: skipping undated item: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=url,
            source_name="ISTAT",
            country="Italy",
            flag_emoji="🇮🇹",
            published_at=pub_date,
            language="en",
            location=ISTAT_GEOLOCATION,
        ))

    logger.info(f"ISTAT: found {len(publications)} agriculture publications")
    return publications


def _extract_from_articles(soup) -> list[dict]:
    """Extract publications from <article> elements (common ISTAT pattern)."""
    entries = []

    # Try article tags first
    for article in soup.find_all("article"):
        link = article.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        url = link["href"]

        # Look for a date in the article
        date_str = ""
        date_el = article.find(class_=re.compile(r"date|data|time|pubdate", re.I))
        if date_el:
            date_str = date_el.get_text(strip=True)
        else:
            time_el = article.find("time")
            if time_el:
                date_str = time_el.get("datetime", "") or time_el.get_text(strip=True)

        if title and url:
            entries.append({"title": title, "url": url, "date": date_str})

    return entries


def _extract_from_links(soup) -> list[dict]:
    """
    Fallback: extract publications from any structured link list.
    Looks for links within the main content area that point to
    press releases or publications.
    """
    entries = []

    # Target the main content area (skip nav, footer, sidebar)
    main = soup.find("main") or soup.find(id=re.compile(r"content|main", re.I)) or soup

    # Find links that look like press releases
    press_release_pattern = re.compile(
        r"(press-release|archivio|comunicato|nota|report|publication)",
        re.I,
    )

    for link in main.find_all("a", href=True):
        href = link["href"]
        title = link.get_text(strip=True)

        # Skip nav links, empty links, and very short titles
        if not title or len(title) < 15:
            continue

        # Check if the URL looks like a publication
        if press_release_pattern.search(href) or press_release_pattern.search(title):
            # Try to find a date near this link
            date_str = _find_nearby_date(link)
            entries.append({"title": title, "url": href, "date": date_str})

    return entries


def _find_nearby_date(element) -> str:
    """Look for a date string near an element (sibling, parent)."""
    import re

    # Check siblings
    for sibling in [element.previous_sibling, element.next_sibling]:
        if sibling and hasattr(sibling, "get_text"):
            text = sibling.get_text(strip=True)
            # Look for date patterns
            date_match = re.search(
                r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}|\d{4}[/\-]\d{2}[/\-]\d{2}",
                text,
            )
            if date_match:
                return date_match.group()

    # Check parent for a date
    parent = element.parent
    if parent:
        date_el = parent.find(class_=re.compile(r"date|data|time", re.I))
        if date_el:
            return date_el.get_text(strip=True)

        time_el = parent.find("time")
        if time_el:
            return time_el.get("datetime", "") or time_el.get_text(strip=True)

    return ""
