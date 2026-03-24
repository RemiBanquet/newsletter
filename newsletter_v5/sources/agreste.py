"""
Agreste France — Statistical bulletins scraper.

Scrapes the French Ministry of Agriculture (Agreste) publication search page
for recent crop/agriculture statistical bulletins.

Target: https://agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/...

Method:
  1. Try requests + BeautifulSoup first (fast, no browser overhead).
  2. If no results found (JSF page requires JS rendering), fall back to
     Selenium headless Chrome — the proven approach from v4.6.

Agreste uses a JSF (JavaServer Faces) frontend. The search results page
almost always requires JavaScript to populate the h4.titreSearch elements.
The Selenium fallback is therefore expected to be the primary code path in
production.

v4.6 archive analysis (Notion Publications DB) confirms the Selenium approach
reliably produced daily results like:
  - "Echanges extérieurs de céréales et oléoprotéagineux, en volume..."
  - "Estimations des surfaces, rendements et productions des récoltes..."
All entries used the GUID search URL as the link (individual links use
JavaScript onclick handlers, not real hrefs).
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import (
    build_publication,
    fetch_html,
    parse_date_flexible,
    parse_html,
)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

AGRESTE_BASE = "https://agreste.agriculture.gouv.fr"
AGRESTE_SEARCH_URL = (
    "https://agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/"
    "4545f1a9-afe6-4c86-a141-693f2c72d550!1b69a349-ca8f-4353-82bb-4c00c502412c!"
    "729f399f-53c3-4952-9971-4753794a7c1b!c6be0c43-70a0-4666-853f-80de38a08ec7!"
    "0c593aed-b1d0-476e-9359-12d6347d8243!b125c6dc-13b7-4260-9abd-6e9321b2b963!"
    "fec0e278-6655-4c48-ac47-aab6d8847e15/search/"
)

AGRESTE_GEOLOCATION = GeoLocation(
    place_name="France",
    country_iso="FR",
    latitude=46.23,
    longitude=2.21,
)

# v4.6 used should_keep_publication() with crop keywords.
# We keep all Agreste publications since the search URL is already
# pre-filtered to agriculture/crops topics.


# ── Date parsing ─────────────────────────────────────────────────

def _parse_french_date(text: str) -> Optional[datetime]:
    """
    Parse French date strings like 'Mis à jour le 24/03/2026'
    or '24/03/2026' or '24 mars 2026'.
    """
    if not text:
        return None

    # Pattern 1: "Mis à jour le DD/MM/YYYY" or bare DD/MM/YYYY
    match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if match:
        return parse_date_flexible(match.group(1))

    # Pattern 2: "DD month YYYY" in French
    french_months = {
        "janvier": 1, "février": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    }
    for month_name, month_num in french_months.items():
        match = re.search(rf"(\d{{1,2}})\s+{month_name}\s+(\d{{4}})", text.lower())
        if match:
            day = int(match.group(1))
            year = int(match.group(2))
            try:
                return datetime(year, month_num, day, tzinfo=timezone.utc)
            except ValueError:
                continue

    return parse_date_flexible(text)


# ── Main entry point ─────────────────────────────────────────────

async def scrape_agreste(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape Agreste for recent agricultural statistical bulletins.

    Strategy:
    1. Try a fast requests+BS4 fetch first (works if server pre-renders)
    2. If no results, fall back to Selenium headless Chrome (proven v4.6 path)
    3. Filter by date (48h lookback) and build Publication objects
    """
    url = source.url or AGRESTE_SEARCH_URL
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    # ── Approach 1: Fast HTTP fetch ──
    entries = await _try_requests_approach(url, session)

    # ── Approach 2: Selenium fallback ──
    if not entries:
        logger.info(
            "Agreste: HTTP fetch returned no results — "
            "falling back to Selenium headless Chrome"
        )
        entries = await _try_selenium_approach(url)

    if not entries:
        logger.warning("Agreste: no publications found from either approach")
        return []

    # ── Build publications ──
    publications = []
    for entry in entries:
        title = entry.get("title", "").strip()
        link = entry.get("url", "").strip()
        date_str = entry.get("date", "")

        if not title:
            continue

        # Agreste individual links are JS onclick — use search page as fallback
        if not link:
            link = url

        if link.startswith("/"):
            link = urljoin(AGRESTE_BASE, link)

        pub_date = _parse_french_date(date_str)
        if pub_date and pub_date < cutoff:
            continue
        if not pub_date:
            logger.debug(f"Agreste: skipping undated item: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title,
            url=link,
            source_name="Agreste",
            country="France",
            flag_emoji="🇫🇷",
            published_at=pub_date,
            language="fr",
            location=AGRESTE_GEOLOCATION,
        ))

    logger.info(f"Agreste: found {len(publications)} publications")
    return publications


# ── Approach 1: requests + BeautifulSoup ─────────────────────────

async def _try_requests_approach(
    url: str,
    session: aiohttp.ClientSession,
) -> list[dict]:
    """Try fetching and parsing with plain HTTP. Returns entries or []."""
    html = await fetch_html(url, session, timeout=45)
    if not html:
        return []

    soup = parse_html(html)
    entries = _extract_from_soup(soup)

    if entries:
        logger.info(f"Agreste: HTTP fetch succeeded — {len(entries)} entries")
    return entries


# ── Approach 2: Selenium headless Chrome ─────────────────────────

async def _try_selenium_approach(url: str) -> list[dict]:
    """
    Fall back to Selenium headless Chrome for JS-rendered pages.
    Runs the blocking Selenium code in a thread executor to stay async.
    Returns entries or [].
    """
    try:
        # Import selenium lazily — only needed when HTTP fetch fails
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        logger.warning(
            "Agreste: Selenium not available — cannot use browser fallback. "
            "Install selenium and webdriver-manager for Agreste support."
        )
        return []

    def _run_selenium() -> list[dict]:
        """Blocking Selenium logic — runs in executor thread."""
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        entries = []
        try:
            driver.get(url)

            # Wait for JSF to render the search results
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "h4.titreSearch")
                )
            )

            # Extract using Selenium elements (same selectors as v4.6)
            rows = driver.find_elements(By.CSS_SELECTOR, "h4.titreSearch")
            date_blocks = driver.find_elements(
                By.CLASS_NAME,
                "disar-split-panel-right-table-cell-content-info",
            )

            logger.info(
                f"Agreste (Selenium): {len(rows)} title rows, "
                f"{len(date_blocks)} date blocks found"
            )

            for idx, row in enumerate(rows):
                try:
                    a_tag = row.find_element(By.TAG_NAME, "a")
                    title = (
                        a_tag.get_attribute("title")
                        or a_tag.text
                        or ""
                    ).strip()

                    if not title:
                        continue

                    # Date from paired date block
                    date_str = ""
                    if idx < len(date_blocks):
                        raw_date = date_blocks[idx].text.strip()
                        # Extract "Mis à jour le DD/MM/YYYY"
                        date_str = raw_date

                    entries.append({
                        "title": title,
                        "url": "",  # Individual links are JS onclick — no real href
                        "date": date_str,
                    })

                except Exception as e:
                    logger.warning(
                        f"Agreste (Selenium): error extracting row {idx}: {e}"
                    )

        except Exception as e:
            logger.error(f"Agreste (Selenium): page load/wait failed: {e}")
        finally:
            driver.quit()

        return entries

    # Run blocking Selenium in a thread to avoid blocking the async loop
    loop = asyncio.get_running_loop()
    try:
        entries = await loop.run_in_executor(None, _run_selenium)
        if entries:
            logger.info(
                f"Agreste (Selenium): extracted {len(entries)} entries"
            )
        return entries
    except Exception as e:
        logger.error(f"Agreste (Selenium): executor failed: {e}")
        return []


# ── Shared HTML extraction (used by requests approach) ───────────

def _extract_from_soup(soup: BeautifulSoup) -> list[dict]:
    """
    Extract publications from parsed HTML.
    Tries the JSF-specific selectors first, then falls back to generic links.
    """
    entries = _extract_jsf_results(soup)
    if not entries:
        entries = _extract_generic_links(soup)
    return entries


def _extract_jsf_results(soup: BeautifulSoup) -> list[dict]:
    """Extract from Agreste's JSF search results structure."""
    entries = []

    titles = soup.select("h4.titreSearch")
    date_blocks = soup.select(
        ".disar-split-panel-right-table-cell-content-info"
    )

    for idx, title_el in enumerate(titles):
        a_tag = title_el.find("a")
        if not a_tag:
            continue

        title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
        href = a_tag.get("href", "")
        if href.startswith("javascript:") or not href:
            href = ""

        date_str = ""
        if idx < len(date_blocks):
            date_str = date_blocks[idx].get_text(strip=True)

        if title:
            entries.append({"title": title, "url": href, "date": date_str})

    return entries


def _extract_generic_links(soup: BeautifulSoup) -> list[dict]:
    """Fallback: extract publications from any link structure on the page."""
    entries = []

    for container in soup.find_all(
        ["article", "div", "li"],
        class_=re.compile(r"result|publication|document|entry|item", re.I),
    ):
        link = container.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"]

        if not title or len(title) < 10:
            continue

        date_str = ""
        date_el = container.find(
            class_=re.compile(r"date|time|info|meta", re.I)
        )
        if date_el:
            date_str = date_el.get_text(strip=True)

        entries.append({"title": title, "url": href, "date": date_str})

    return entries
