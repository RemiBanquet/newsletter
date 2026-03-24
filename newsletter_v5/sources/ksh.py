"""
KSH Hungary — Central Statistical Office agriculture publications scraper.

Scrapes the KSH (Központi Statisztikai Hivatal) agriculture publications section
for recent statistical releases and crop data. KSH publishes agricultural production
statistics, crop estimates, and land use data for Hungary.

Target: https://www.ksh.hu/apps/shop.lista?p_lang=EN&p_temakor_kod=OM
Base: https://www.ksh.hu

Method: requests + BeautifulSoup (static HTML)
"""

import logging
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

KSH_BASE = "https://www.ksh.hu"
KSH_AGRICULTURE_URL = "https://www.ksh.hu/apps/shop.lista?p_lang=EN&p_temakor_kod=OM"

KSH_GEOLOCATION = GeoLocation(
    place_name="Hungary",
    country_iso="HU",
    latitude=47.50,
    longitude=19.04,
)


async def scrape_ksh(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape KSH agriculture publications.

    Strategy:
    1. Fetch the agriculture publications listing page
    2. Extract publication entries from td.descr elements
    3. Parse title, date, and URL
    4. Filter by 48-hour lookback
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    html = await fetch_html(source.url or KSH_AGRICULTURE_URL, session)
    if not html:
        logger.warning("KSH: could not fetch agriculture page")
        return publications

    soup = parse_html(html)

    # Find all td.descr elements containing publications
    entries = soup.find_all("td", class_="descr")

    for entry in entries:
        # Title is in div.pub_title a
        title_elem = entry.find("div", class_="pub_title")
        if not title_elem:
            continue

        title_link = title_elem.find("a", href=True)
        if not title_link:
            continue

        title = title_link.get_text(strip=True)
        url = title_link.get("href", "").strip()

        if not title or not url:
            continue

        # Make URL absolute
        if url.startswith("/"):
            url = urljoin(KSH_BASE, url)

        # Date is in div.pub_start b (format DD/MM/YYYY)
        date_str = ""
        date_elem = entry.find("div", class_="pub_start")
        if date_elem:
            date_b = date_elem.find("b")
            if date_b:
                date_str = date_b.get_text(strip=True)

        pub_date = parse_date_flexible(date_str) if date_str else None
        if pub_date and pub_date < cutoff:
            continue

        if not pub_date:
            logger.debug(f"KSH: skipping entry with unparseable date: {date_str}")
            continue

        publications.append(build_publication(
            title=title,
            url=url,
            source_name="KSH",
            country="Hungary",
            flag_emoji="🇭🇺",
            published_at=pub_date,
            language="en",
            location=KSH_GEOLOCATION,
        ))

    logger.info(f"KSH: found {len(publications)} agriculture publications")
    return publications
