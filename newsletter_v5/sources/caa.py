"""
CAA Spain — Cooperativas Agro-Alimentarias cereals publications scraper.

Scrapes the CAA (Cooperativas Agro-Alimentarias) document library for
cereals-related publications and reports. CAA publishes market analysis,
production statistics, and sector reports for Spanish agricultural cooperatives.

Target: https://www.agro-alimentarias.coop/documents?cat_sel=33
Base: https://www.agro-alimentarias.coop
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

CAA_BASE = "https://www.agro-alimentarias.coop"
CAA_DOCUMENTS_URL = "https://www.agro-alimentarias.coop/documents?cat_sel=33"

CAA_GEOLOCATION = GeoLocation(
    place_name="Spain",
    country_iso="ES",
    latitude=40.42,
    longitude=-3.70,
)

# Agriculture keywords for filtering
AG_KEYWORDS_ES = [
    "cereales", "trigo", "cebada", "maíz", "avena", "centeno",
    "arroz", "sorgo", "leguminosas", "cultivos herbáceos",
    "agricultura", "producción", "mercado", "sector",
    "crop", "cereal", "grain", "production",
]


def _is_agriculture_related(text: str) -> bool:
    """Check if text is agriculture-related."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in AG_KEYWORDS_ES)


async def scrape_caa(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape CAA Spain for cereals publications and documents.

    Strategy:
    1. Fetch the CAA documents page (cereals category)
    2. Look for div.card-info elements containing date and link
    3. Filter to titles starting with "CEREALES."
    4. Extract dates in DD/MM/YYYY format
    5. Apply 48h lookback (frequent publication schedule)
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or CAA_DOCUMENTS_URL, session)
    if not html:
        logger.warning("CAA: could not fetch documents page")
        return publications

    soup = parse_html(html)

    # Look for card elements containing documents
    for card in soup.find_all("div", class_="card-info"):
        # Extract date from div.date
        date_str = ""
        date_el = card.find("div", class_="date")
        if date_el:
            date_text = date_el.get_text(strip=True)
            # Date format: "DD/MM/YYYY" (may have extra text with |)
            if date_text:
                # Split by | and take first part if present
                date_str = date_text.split("|")[0].strip()

        # Extract link and title from div.text a
        link_el = card.find("div", class_="text")
        if not link_el:
            continue

        link = link_el.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"].strip()

        if not title or not href:
            continue

        # Filter to titles starting with "CEREALES."
        if not title.startswith("CEREALES."):
            logger.debug(f"CAA: skipping non-cereales document: {title[:60]}")
            continue

        # Make URL absolute
        if href.startswith("/"):
            href = urljoin(CAA_BASE, href)

        # Parse date
        pub_date = None
        if date_str:
            pub_date = parse_date_flexible(date_str)

        # Apply cutoff
        if pub_date and pub_date < cutoff:
            continue

        # Skip undated documents
        if not pub_date:
            logger.debug(f"CAA: skipping undated document: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=href,
            source_name="CAA Spain",
            country="Spain",
            flag_emoji="🇪🇸",
            published_at=pub_date,
            language="es",
            location=CAA_GEOLOCATION,
        ))

    logger.info(f"CAA: found {len(publications)} cereals publications")
    return publications
