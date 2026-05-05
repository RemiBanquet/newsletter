"""
TUIK Turkey — Crop production statistics scraper.

Scrapes TurkStat (Turkish Statistical Institute) for agricultural
bulletins and crop production statistics.

Target: https://data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111&dil=1
Bulletins: https://data.tuik.gov.tr/Bulten/Index?p=<slug>&dil=2

Method:
  1. Try requests + BeautifulSoup first (fast, no browser overhead).
  2. If no results found (JS-rendered page), fall back to Selenium
     headless Chrome — the proven approach from v4.6.

The TUIK agriculture category (tarim-111) contains crop production
bulletins rendered via JavaScript. The Selenium fallback is therefore
expected to be the primary code path in production.

v4.6 used Selenium with CSS selectors:
  - tr[role='row'] for table rows
  - div.news-content a for bulletin links
  - span.float-right for dates
  - Filtered on titles starting with "Bitkisel Üretim"
"""

import asyncio
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

TUIK_BASE = "https://data.tuik.gov.tr"

# Turkish version (primary — has the crop production bulletins)
TUIK_AGRICULTURE_URL_TR = "https://data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111&dil=1"
# English version (fallback)
TUIK_AGRICULTURE_URL_EN = "https://data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111&dil=2"

TUIK_GEOLOCATION = GeoLocation(
    place_name="Turkey",
    country_iso="TR",
    latitude=39.93,
    longitude=32.86,
)

# ── Turkish → English translation maps (from v4.6) ───────────────

TUIK_TITLE_MAP = {
    "Bitkisel Üretim İstatistikleri": "Crop production statistics",
    "Bitkisel Üretim": "Crop production",
}

TURKISH_MONTHS = {
    "Ocak": "January", "Şubat": "February", "Subat": "February",
    "Mart": "March", "Nisan": "April", "Mayıs": "May", "Mayis": "May",
    "Haziran": "June", "Temmuz": "July", "Ağustos": "August",
    "Agustos": "August", "Eylül": "September", "Eylul": "September",
    "Ekim": "October", "Kasım": "November", "Kasim": "November",
    "Aralık": "December", "Aralik": "December",
}


def _translate_tuik_title(tr_title: str) -> str:
    """Translate Turkish TUIK title to English using known prefix map."""
    for tr, en in TUIK_TITLE_MAP.items():
        if tr_title.startswith(tr):
            suffix = tr_title[len(tr):].lstrip(" ,.-")
            return f"{en} {suffix}".strip()
    return tr_title


def _parse_tuik_date(date_str: str) -> Optional[datetime]:
    """
    Replace Turkish month names with English equivalents, then parse.
    Returns None if the date is empty or unreadable.
    """
    if not date_str:
        return None
    for tr, en in TURKISH_MONTHS.items():
        date_str = date_str.replace(tr, en)
    return parse_date_flexible(date_str)


def _is_crop_title(title: str) -> bool:
    """Check if title is a crop production bulletin (v4.6 filter)."""
    return (
        title.startswith("Bitkisel Üretim")
        or title.startswith("Bitkisel Uretim")
        or title.lower().startswith("crop production")
    )


# ── Main entry point ─────────────────────────────────────────────

async def scrape_tuik(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Scrape TUIK agriculture bulletins.

    Strategy:
    1. Try a fast requests+BS4 fetch first (works if server pre-renders)
    2. If no results, fall back to Selenium headless Chrome (proven v4.6 path)
    3. Filter to "Bitkisel Üretim" titles, translate to English, build pubs
    """
    url = source.url or TUIK_AGRICULTURE_URL_TR
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    # ── Approach 1: Fast HTTP fetch ──
    entries = await _try_requests_approach(url, session)

    # ── Approach 2: Selenium fallback ──
    if not entries:
        logger.info(
            "TUIK: HTTP fetch returned no results — "
            "falling back to Selenium headless Chrome"
        )
        entries = await _try_selenium_approach(url)

    if not entries:
        logger.warning("TUIK: no publications found from either approach")
        return []

    # ── Build publications ──
    publications = []
    for entry in entries:
        title = entry.get("title", "").strip()
        link = entry.get("url", "").strip()
        date_str = entry.get("date", "")

        if not title:
            continue

        # Filter to crop production bulletins
        if not _is_crop_title(title):
            continue

        # Translate Turkish title to English
        title_en = _translate_tuik_title(title)

        if not link:
            link = url
        if link.startswith("/"):
            link = urljoin(TUIK_BASE, link)

        pub_date = _parse_tuik_date(date_str)
        if pub_date and pub_date < cutoff:
            continue
        if not pub_date:
            logger.debug(f"TUIK: skipping undated item: {title[:60]}")
            continue

        publications.append(build_publication(
            title=title_en,
            url=link,
            source_name="TUIK",
            country="Turkey",
            flag_emoji="🇹🇷",
            published_at=pub_date,
            language="en",
            location=TUIK_GEOLOCATION,
        ))

    logger.info(f"TUIK: found {len(publications)} crop production bulletins")
    return publications


# ── Approach 1: requests + BeautifulSoup ─────────────────────────

async def _try_requests_approach(
    url: str,
    session: aiohttp.ClientSession,
) -> list[dict]:
    """Try fetching and parsing with plain HTTP. Returns entries or []."""
    # Try Turkish page first, then English
    for page_url, lang in [(url, "tr"), (TUIK_AGRICULTURE_URL_EN, "en")]:
        html = await fetch_html(page_url, session, timeout=45)
        if not html:
            continue

        entries = _extract_from_soup(html)
        if entries:
            logger.info(f"TUIK: HTTP fetch ({lang}) succeeded — {len(entries)} entries")
            return entries
        else:
            logger.info(
                f"TUIK ({lang}): page fetched but no bulletins extracted — "
                "may be JS-rendered"
            )

    return []


# ── Approach 2: Selenium headless Chrome ─────────────────────────

async def _try_selenium_approach(url: str) -> list[dict]:
    """
    Fall back to Selenium headless Chrome for JS-rendered pages.
    Mirrors the proven v4.6 logic exactly:
    - Navigate to the agriculture category page
    - Wait for tr[role='row'] elements
    - Extract links from div.news-content a
    - Extract dates from span.float-right or first <td>
    - Filter to "Bitkisel Üretim" titles

    Runs blocking Selenium code in a thread executor to stay async.
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from sources._selenium_helper import build_chrome_driver
    except ImportError:
        logger.warning(
            "TUIK: Selenium not available — cannot use browser fallback. "
            "Install selenium for TUIK support."
        )
        return []

    def _run_selenium() -> list[dict]:
        """Blocking Selenium logic — mirrors v4.6 scrape_tuik()."""
        driver = build_chrome_driver()

        entries = []
        try:
            driver.get(url)

            # Wait for JS to render the table rows (v4.6 selector)
            WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "tr[role='row']")
                )
            )

            rows = driver.find_elements(By.CSS_SELECTOR, "tr[role='row']")
            logger.info(f"TUIK (Selenium): {len(rows)} table rows detected")

            for idx, row in enumerate(rows, start=1):
                try:
                    # Link to bulletin: div.news-content a
                    link_els = row.find_elements(
                        By.CSS_SELECTOR, "div.news-content a"
                    )
                    if not link_els:
                        continue

                    link_el = link_els[0]
                    title_tr = link_el.text.strip()
                    if not title_tr:
                        continue

                    href = link_el.get_attribute("href") or ""
                    if href and not href.startswith("http"):
                        href = TUIK_BASE + href

                    # Date extraction (v4.6 logic):
                    # 1) span.float-right
                    date_text = ""
                    spans = row.find_elements(
                        By.CSS_SELECTOR, "span.float-right"
                    )
                    if spans:
                        date_text = spans[0].text.strip()

                    # 2) Fallback: first <td> innerText
                    if not date_text:
                        tds = row.find_elements(By.TAG_NAME, "td")
                        if tds:
                            date_text = tds[0].get_attribute("innerText").strip()

                    entries.append({
                        "title": title_tr,
                        "url": href,
                        "date": date_text,
                    })

                except Exception as e:
                    logger.warning(
                        f"TUIK (Selenium): error on row {idx}: {e}"
                    )

        except Exception as e:
            logger.error(f"TUIK (Selenium): page load/wait failed: {e}")
        finally:
            driver.quit()

        return entries

    # Run blocking Selenium in a thread to avoid blocking the async loop
    loop = asyncio.get_running_loop()
    try:
        entries = await loop.run_in_executor(None, _run_selenium)
        if entries:
            logger.info(
                f"TUIK (Selenium): extracted {len(entries)} entries"
            )
        return entries
    except Exception as e:
        logger.error(f"TUIK (Selenium): executor failed: {e}")
        return []


# ── Shared HTML extraction (used by requests approach) ───────────

def _extract_from_soup(html: str) -> list[dict]:
    """Extract bulletins from parsed HTML using BS4."""
    soup = parse_html(html)
    entries = []

    # Pattern 1: Bulletin links (a hrefs to /Bulten/ pages)
    bulletin_links = soup.find_all(
        "a", href=re.compile(r"Bulten|bulten|Bulletin", re.I)
    )
    for link in bulletin_links:
        title = link.get_text(strip=True)
        href = link.get("href", "")

        if not title or len(title) < 10:
            continue

        if href.startswith("/"):
            href = urljoin(TUIK_BASE, href)
        elif not href.startswith("http"):
            href = f"{TUIK_BASE}/{href}"

        # Look for date in parent/sibling elements
        date_str = ""
        parent = link.find_parent(["tr", "div", "li"])
        if parent:
            date_el = parent.find(
                class_=re.compile(r"float-right|date|time", re.I)
            )
            if date_el:
                date_str = date_el.get_text(strip=True)

        entries.append({"title": title, "url": href, "date": date_str})

    # Pattern 2: Table rows with news-content
    if not entries:
        for row in soup.select("tr[role='row']"):
            link = row.select_one("div.news-content a")
            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get("href", "")

            if not title:
                continue

            if href and not href.startswith("http"):
                href = urljoin(TUIK_BASE, href)

            date_str = ""
            span = row.select_one("span.float-right")
            if span:
                date_str = span.get_text(strip=True)

            entries.append({"title": title, "url": href, "date": date_str})

    return entries
