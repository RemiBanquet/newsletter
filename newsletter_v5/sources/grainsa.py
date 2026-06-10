"""
Grain SA — South Africa production reports (CEC mirror).

Scrapes the Grain SA "Production Reports" page, the de-facto distribution
channel for the official Crop Estimates Committee (CEC) numbers: per-crop
area/yield/production Excel files (maize, soybean, sunflower, sorghum,
groundnuts, dry beans) plus crop-condition PDFs. Updated monthly on CEC
release day.

Page verified 2026-06-10:
https://www.grainsa.co.za/pages/industry-reports/production-reports
Server-rendered table with dated .xlsx links (e.g. files dated 26-05-2026).
DALRRD's own crop-estimates page blocks non-browser clients, so this
mirror is the reliable path.

Strategy: collect all document links (.xlsx/.xls/.pdf), parse a date from
the link text / filename / surrounding row, keep those within the lookback
window.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, unquote

import aiohttp

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import build_publication, fetch_html, parse_html

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.grainsa.co.za/pages/industry-reports/production-reports"

DOC_EXTENSIONS = (".xlsx", ".xls", ".pdf")

# Dates seen as 26-05-2026 / 26/05/2026 / 26.05.2026 (DD-MM-YYYY)
DATE_RE = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")

ZA_GEO = GeoLocation(
    place_name="South Africa", country_iso="ZA", latitude=-28.48, longitude=24.68,
)


def _parse_dmy(text: str) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _date_for_link(a) -> datetime | None:
    """Date from link text, filename, or the surrounding row."""
    candidates = [a.get_text(" ", strip=True), unquote(a.get("href", ""))]
    node = a
    for _ in range(3):
        node = node.parent
        if node is None:
            break
        candidates.append(node.get_text(" ", strip=True))
    for text in candidates:
        d = _parse_dmy(text)
        if d:
            return d
    return None


async def scrape_grainsa(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """Scrape Grain SA production reports for new CEC documents."""
    url = source.url or DEFAULT_URL
    html = await fetch_html(url, session)
    if not html:
        return []

    soup = parse_html(html)
    lookback_hours = source.lookback_hours if source.lookback_hours else 24 * 35
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    pubs = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().split("?")[0].endswith(DOC_EXTENSIONS):
            continue
        full_url = urljoin(url, href)
        if full_url in seen:
            continue

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 5:
            # Fall back to the filename
            title = unquote(full_url.rsplit("/", 1)[-1])

        doc_date = _date_for_link(a)
        if doc_date is None or doc_date < cutoff:
            continue

        seen.add(full_url)
        pubs.append(build_publication(
            title=f"Grain SA / CEC: {title}",
            url=full_url,
            source_name="Grain SA",
            country="South Africa",
            flag_emoji="🇿🇦",
            published_at=doc_date,
            summary="",
            language="en",
            location=ZA_GEO,
        ))

    logger.info(f"Grain SA: {len(pubs)} documents within lookback window")
    return pubs


if __name__ == "__main__":
    # Smoke test: python -m sources.grainsa (from newsletter_v5/)
    import asyncio
    from models import SourceCategory, SourceType

    async def _main():
        cfg = SourceConfig(
            name="Grain SA", url=DEFAULT_URL,
            category=SourceCategory.OFFICIAL_PUBLICATION,
            source_type=SourceType.REQUESTS_SCRAPER,
            lookback_hours=24 * 60,
        )
        async with aiohttp.ClientSession() as s:
            results = await scrape_grainsa(cfg, s)
        print(f"{len(results)} documents found")
        for p in results[:10]:
            print(f"  - [{p.published_at}] {p.title[:90]}")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
