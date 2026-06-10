"""
BCR — Bolsa de Comercio de Rosario, GEA national production estimates
(Argentina).

The GEA (Guía Estratégica para el Agro) team publishes monthly national
wheat/corn/soy area-yield-production estimates plus a weekly crop-condition
report. Numbers appear in plain server-rendered HTML (Drupal 8), with a
monthly PDF link.

Page verified 2026-06-10:
https://www.bcr.com.ar/es/mercados/gea/estimaciones-nacionales-de-produccion/estimaciones
returned current estimates (e.g. soy 25/26: 50.0 Mt) without JavaScript.

Strategy (two-pronged, no state file needed):
1. Collect dated PDF/report links within the lookback window.
2. Month-change detection: find the most recent Spanish "month year" mention
   in the page heading area and emit one publication titled with it. The
   title feeds the dedup hash, so a new month = new ID = passes dedup, and
   re-runs within the same month are deduplicated automatically.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import aiohttp

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import build_publication, fetch_html, parse_html

logger = logging.getLogger(__name__)

DEFAULT_URL = (
    "https://www.bcr.com.ar/es/mercados/gea/"
    "estimaciones-nacionales-de-produccion/estimaciones"
)

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MONTH_RE = re.compile(
    r"\b(" + "|".join(SPANISH_MONTHS) + r")\b[\s,]+(?:de\s+)?(\d{4})",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")

AR_GEO = GeoLocation(
    place_name="Argentina", country_iso="AR", latitude=-34.60, longitude=-58.38,
)


def _latest_month_mention(soup) -> tuple[str, datetime] | None:
    """Most recent 'month year' mention in the page. Returns (label, date)."""
    best = None
    for m in MONTH_RE.finditer(soup.get_text(" ", strip=True)):
        month = SPANISH_MONTHS[m.group(1).lower()]
        year = int(m.group(2))
        if not 2020 <= year <= 2035:
            continue
        dt = datetime(year, month, 1, tzinfo=timezone.utc)
        if best is None or dt > best[1]:
            best = (f"{m.group(1).lower()} {year}", dt)
    return best


async def scrape_bcr(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """Scrape BCR GEA estimates page for new monthly estimates."""
    url = source.url or DEFAULT_URL
    html = await fetch_html(url, session)
    if not html:
        return []

    soup = parse_html(html)
    lookback_hours = source.lookback_hours if source.lookback_hours else 24 * 40
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    pubs = []
    seen = set()

    # Strategy 1: dated document links (monthly PDF reports)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        full_url = urljoin(url, href)
        if full_url in seen:
            continue
        context = a.get_text(" ", strip=True)
        parent = a.parent.get_text(" ", strip=True) if a.parent else ""
        m = DATE_RE.search(context) or DATE_RE.search(parent)
        if not m:
            continue
        day, month, year = (int(g) for g in m.groups())
        try:
            doc_date = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            continue
        if doc_date < cutoff:
            continue
        seen.add(full_url)
        title = context if len(context) > 10 else f"Informe GEA {day:02d}/{month:02d}/{year}"
        pubs.append(build_publication(
            title=f"BCR GEA: {title}",
            url=full_url,
            source_name="BCR (Bolsa de Comercio de Rosario)",
            country="Argentina",
            flag_emoji="🇦🇷",
            published_at=doc_date,
            summary="",
            language="es",
            location=AR_GEO,
        ))

    # Strategy 2: month-change detection on the estimates page itself.
    # Title carries the month → dedup hash changes once per month.
    latest = _latest_month_mention(soup)
    if latest and latest[1] >= cutoff.replace(day=1):
        label, month_dt = latest
        pubs.append(build_publication(
            title=f"BCR GEA: estimaciones nacionales de producción — {label}",
            url=url,
            source_name="BCR (Bolsa de Comercio de Rosario)",
            country="Argentina",
            flag_emoji="🇦🇷",
            published_at=month_dt,
            summary="Monthly national wheat/corn/soybean area, yield and production estimates from the GEA team.",
            language="es",
            location=AR_GEO,
        ))

    logger.info(f"BCR GEA: {len(pubs)} publications")
    return pubs


if __name__ == "__main__":
    # Smoke test: python -m sources.bcr (from newsletter_v5/)
    import asyncio
    from models import SourceCategory, SourceType

    async def _main():
        cfg = SourceConfig(
            name="BCR GEA", url=DEFAULT_URL,
            category=SourceCategory.OFFICIAL_PUBLICATION,
            source_type=SourceType.REQUESTS_SCRAPER,
            lookback_hours=24 * 60,
        )
        async with aiohttp.ClientSession() as s:
            results = await scrape_bcr(cfg, s)
        print(f"{len(results)} publications found")
        for p in results[:10]:
            print(f"  - [{p.published_at}] {p.title[:90]}")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
