"""
MAPA Spain — Avances de superficies y producciones de cultivos scraper.

Scrapes the MAPA (Ministerio de Agricultura, Pesca y Alimentación)
"Avances" page for monthly crop area and production advance reports.
These are PDF reports published monthly with planting/harvest estimates.

Target: https://www.mapa.gob.es/es/estadistica/temas/estadisticas-agrarias/agricultura/avances-superficies-producciones-agricolas
Also: https://www.mapa.gob.es/es/estadistica/temas/novedades (statistics news page)

Method: requests + BeautifulSoup (static HTML)

URL pattern for monthly bulletins:
  https://www.mapa.gob.es/dam/mapa/contenido/estadisticas/temas/publicaciones/
  boletin-mensual-de-estadistica/YYYY/bme-YYYY-MM-month.pdf
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

import aiohttp

from models import GeoLocation, Publication, RunMetrics, SourceConfig
from sources.scraper_base import (
    build_publication,
    fetch_html,
    parse_date_flexible,
    parse_html,
)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

MAPA_BASE = "https://www.mapa.gob.es"
MAPA_AVANCES_URL = (
    "https://www.mapa.gob.es/es/estadistica/temas/estadisticas-agrarias/"
    "agricultura/avances-superficies-producciones-agricolas"
)
MAPA_NOVEDADES_URL = "https://www.mapa.gob.es/es/estadistica/temas/novedades"

MAPA_GEOLOCATION = GeoLocation(
    place_name="Spain",
    country_iso="ES",
    latitude=40.42,
    longitude=-3.70,
)

# Spanish month names for URL pattern matching and date parsing
SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# Agriculture keywords (Spanish + English)
AG_KEYWORDS_ES = [
    "avances", "superficies", "producciones", "cultivos", "cosecha",
    "cereales", "oleaginosas", "herbáceos", "leñosos", "riego", "secano",
    "trigo", "cebada", "maíz", "girasol", "colza", "arroz", "algodón",
    "agricultura", "agrícola", "campaña", "siembra",
    # English fallbacks
    "crop", "cereal", "oilseed", "harvest", "planted", "area",
]


def _is_agriculture_related(text: str) -> bool:
    """Check if text is agriculture-related."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in AG_KEYWORDS_ES)


async def scrape_mapa(
    source: SourceConfig,
    session: aiohttp.ClientSession,
    metrics: RunMetrics | None = None,
) -> list[Publication]:
    """
    Scrape MAPA Spain for crop area and production advance reports.

    Strategy:
    1. Scrape the Avances page for links to PDF reports and data files
    2. Also check the Novedades (news) page for recent agriculture stats
    3. Extract publication links with dates
    """
    publications = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=168)  # 7-day lookback (monthly reports)

    # ── Approach 1: Avances page ──
    avances_pubs, avances_candidates = await _scrape_avances_page(source, session, cutoff)
    publications.extend(avances_pubs)

    # ── Approach 2: Novedades (statistics news) page ──
    novedades_pubs = await _scrape_novedades_page(session, cutoff)
    publications.extend(novedades_pubs)

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for pub in publications:
        if pub.url not in seen_urls:
            seen_urls.add(pub.url)
            unique.append(pub)
    publications = unique

    if metrics is not None:
        # Data-file links found on the Avances page before any date or dedup
        # filtering. 0 → the page or the /dam/ link pattern changed
        # (source_health → DEAD). Non-zero with nothing published → the 7-day
        # window is rejecting a monthly publisher (→ QUIET).
        metrics.source_raw_counts[source.name] = avances_candidates
    logger.info(
        f"MAPA: {avances_candidates} data-file links on Avances page → "
        f"{len(publications)} agriculture publications"
    )
    return publications


_SKIP_TITLES = re.compile(
    r"nota\s+metodol|glosario|glossary|methodolog|previous\s+year|años\s+anterior",
    re.I,
)

# Only keep Spanish (/es/) or direct data (/dam/) links — skip language variants
_LANG_VARIANT_RE = re.compile(r"^/(en|fr|ca|gl|eu|va)/", re.I)


async def _scrape_avances_page(
    source: SourceConfig,
    session: aiohttp.ClientSession,
    cutoff: datetime,
) -> tuple[list[Publication], int]:
    """
    Scrape the Avances de Superficies page for PDF/Excel data links.

    Real page structure (observed 2026-03-24):
      - 193 /dam/ links spanning 2012-2025
      - URL pattern: /dam/mapa/.../2025/cuaderno_enero2025.pdf
      - Year is in the URL path: /<YYYY>/
      - Non-data items: "Nota metodológica", "IME-Avances..." (general info)
      - Language variants: /es/, /en/, /fr/, /ca/, /gl/, /eu/, /va/ prefixes

    Filtering strategy:
      1. Only keep /dam/ links (actual data files)
      2. Extract year from URL path — skip if year < current_year - 1
      3. Skip non-data items (methodology notes, glossaries)
      4. Deduplicate PDF vs Excel variants of the same report

    Returns (publications, candidate_count) where candidate_count is the number
    of structurally-valid data-file links seen before date/dedup filtering.
    """
    publications = []
    url = source.url if source.url != MAPA_AVANCES_URL else MAPA_AVANCES_URL

    html = await fetch_html(MAPA_AVANCES_URL, session)
    if not html:
        logger.warning("MAPA: could not fetch Avances page")
        return publications, 0

    soup = parse_html(html)
    candidates = 0
    now = datetime.now(timezone.utc)
    # Accept reports from current year and previous year only
    min_year = now.year - 1

    seen_reports = set()  # Track report identity to dedupe PDF/Excel variants

    for link in soup.find_all("a", href=True):
        href = link["href"]
        title = link.get_text(strip=True)

        # Only process /dam/ links (actual data files)
        if "/dam/" not in href:
            continue

        # Skip language variant links
        if _LANG_VARIANT_RE.search(href):
            continue

        # Must be a data file (PDF or Excel)
        if not re.search(r"\.(pdf|xlsx?|csv)$", href, re.I):
            continue

        # Skip non-data items (methodology notes, glossaries)
        if _SKIP_TITLES.search(title) or _SKIP_TITLES.search(href):
            continue

        candidates += 1

        # Make URL absolute
        if href.startswith("/"):
            href = urljoin(MAPA_BASE, href)

        if not title:
            title = _title_from_url(href)

        # Extract year from URL path — e.g. .../2025/cuaderno_enero2025.pdf
        url_year = _extract_year_from_path(href)
        if url_year and url_year < min_year:
            continue  # Too old

        # Extract publication date from URL or title
        pub_date = _extract_date_from_url(href) or _extract_date_from_text(title)

        # If we got a date, enforce cutoff
        if pub_date and pub_date < cutoff:
            continue

        # If no date and no year in URL, skip (undated = likely old/static content)
        if not pub_date and not url_year:
            continue

        # Deduplicate: same report in PDF + Excel = keep only PDF
        # Identity = year + month name (e.g., "2025_enero")
        report_key = _report_identity(href, title)
        if report_key in seen_reports:
            continue
        seen_reports.add(report_key)

        publications.append(build_publication(
            title=f"MAPA: {title}" if not title.startswith("MAPA") else title,
            url=href,
            source_name="MAPA Spain",
            country="Spain",
            flag_emoji="🇪🇸",
            published_at=pub_date,
            language="es",
            location=MAPA_GEOLOCATION,
        ))

    return publications, candidates


async def _scrape_novedades_page(
    session: aiohttp.ClientSession,
    cutoff: datetime,
) -> list[Publication]:
    """Scrape the Novedades (statistics news) page for recent agriculture updates."""
    publications = []

    html = await fetch_html(MAPA_NOVEDADES_URL, session)
    if not html:
        return publications

    soup = parse_html(html)

    # The novedades page lists recent statistics releases
    # Look for list items or article blocks with links
    main = soup.find("main") or soup.find(id=re.compile(r"content", re.I)) or soup

    for item in main.find_all(["li", "article", "div"], class_=re.compile(r"novedad|item|entry|resultado", re.I)):
        link = item.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"]

        if not title or len(title) < 10:
            continue
        if not _is_agriculture_related(title):
            continue

        if href.startswith("/"):
            href = urljoin(MAPA_BASE, href)

        # Look for date
        date_str = ""
        date_el = item.find(class_=re.compile(r"fecha|date|time", re.I))
        if date_el:
            date_str = date_el.get_text(strip=True)
        pub_date = parse_date_flexible(date_str) if date_str else None

        if pub_date and pub_date < cutoff:
            continue

        publications.append(build_publication(
            title=title,
            url=href,
            source_name="MAPA Spain",
            country="Spain",
            flag_emoji="🇪🇸",
            published_at=pub_date,
            language="es",
            location=MAPA_GEOLOCATION,
        ))

    return publications


def _extract_year_from_path(url: str) -> Optional[int]:
    """Extract a 4-digit year from the URL path (e.g. .../2025/cuaderno_enero2025.pdf)."""
    match = re.search(r"/(\d{4})/", url)
    if match:
        year = int(match.group(1))
        if 2010 <= year <= 2030:
            return year
    return None


def _report_identity(url: str, title: str) -> str:
    """
    Generate a dedup key for a MAPA report.
    Same monthly report exists as PDF + Excel → keep only one.
    Identity = year + month from filename (e.g. "2025_enero").
    """
    url_lower = url.lower()
    # Strip extension to normalize PDF vs Excel
    base = re.sub(r"\.(pdf|xlsx?|csv)$", "", url_lower)
    # Extract the filename as the key
    return base.rstrip("/").split("/")[-1]


def _extract_date_from_url(url: str) -> Optional[datetime]:
    """
    Extract a date from a MAPA URL.

    Real patterns observed:
      .../2025/cuaderno_enero2025.pdf         → Jan 2025
      .../2025/cuaderno_enero2025.xls         → Jan 2025
      .../bme-2026-02-febrero.pdf             → Feb 2026
      .../avances-superficies-2025-03.xlsx    → Mar 2025
    """
    url_lower = url.lower()

    # Strategy 1: month name + year in filename (most common MAPA pattern)
    # e.g. cuaderno_enero2025.pdf or bme-2026-02-febrero.pdf
    year_match = re.search(r"/(\d{4})/", url)
    if year_match:
        year = int(year_match.group(1))
        for month_name, month_num in SPANISH_MONTHS.items():
            if month_name in url_lower:
                return datetime(year, month_num, 15, tzinfo=timezone.utc)

    # Strategy 2: YYYY-MM pattern in filename
    match = re.search(r"(\d{4})[/-](\d{2})", url)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if 2010 <= year <= 2030 and 1 <= month <= 12:
            return datetime(year, month, 15, tzinfo=timezone.utc)

    return None


def _extract_date_from_text(text: str) -> Optional[datetime]:
    """Extract a date from Spanish text like 'Febrero 2026' or 'Campaña 2025/2026'."""
    text_lower = text.lower()

    # "Febrero 2026" pattern
    for month_name, month_num in SPANISH_MONTHS.items():
        match = re.search(rf"{month_name}\s+(\d{{4}})", text_lower)
        if match:
            year = int(match.group(1))
            if 2020 <= year <= 2030:
                return datetime(year, month_num, 15, tzinfo=timezone.utc)

    return None


def _title_from_url(url: str) -> str:
    """Generate a human-readable title from a file URL."""
    # Extract filename
    filename = url.rstrip("/").split("/")[-1]
    # Remove extension
    name = re.sub(r"\.(pdf|xlsx?|csv)$", "", filename, flags=re.I)
    # Replace hyphens/underscores with spaces
    name = name.replace("-", " ").replace("_", " ")
    # Capitalize
    return name.strip().title()
