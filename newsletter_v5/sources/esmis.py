"""
USDA ESMIS — Economics, Statistics and Market Information System.

Monitors a USDA publication page on esmis.nal.usda.gov for new releases.
Default target: WASDE (World Agricultural Supply and Demand Estimates),
published monthly with direct PDF/TXT/XLS/XML links.

Background (verified 2026-06-10): the old usda.library.cornell.edu URLs
redirect to esmis.nal.usda.gov. The publication page is a clean
server-rendered Drupal table whose rows carry release dates like
"May 12, 2026" and direct file links. The same site hosts every
NASS/ERS/WAOB publication, so pointing additional Notion rows (with this
same scraper_id) at other ESMIS publication URLs extends coverage with
zero new code.

Strategy: scan the page for rows/blocks containing a parseable
"Month DD, YYYY" date; keep those within the lookback window; link to the
best file in the block (PDF preferred) or the page itself.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import aiohttp

from models import GeoLocation, Publication, RunMetrics, SourceConfig
from sources.scraper_base import build_publication, fetch_html, parse_html

logger = logging.getLogger(__name__)

DEFAULT_URL = (
    "https://esmis.nal.usda.gov/publication/"
    "world-agricultural-supply-and-demand-estimates"
)

US_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b"
)

FILE_EXTENSIONS = (".pdf", ".txt", ".xls", ".xlsx", ".xml")

USA_GEO = GeoLocation(
    place_name="United States", country_iso="US", latitude=38.90, longitude=-77.04,
)


def _parse_us_date(text: str) -> datetime | None:
    m = US_DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(
            f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _best_file_link(node, base_url: str) -> str | None:
    """Prefer a PDF link inside the block; else any document file link."""
    links = node.find_all("a", href=True) if node else []
    pdf, other = None, None
    for a in links:
        href = a["href"].lower().split("?")[0]
        if href.endswith(".pdf") and not pdf:
            pdf = urljoin(base_url, a["href"])
        elif href.endswith(FILE_EXTENSIONS) and not other:
            other = urljoin(base_url, a["href"])
    return pdf or other


async def scrape_esmis(
    source: SourceConfig,
    session: aiohttp.ClientSession,
    metrics: RunMetrics | None = None,
) -> list[Publication]:
    """Scan a USDA ESMIS publication page for new releases."""
    url = source.url or DEFAULT_URL
    html = await fetch_html(url, session)
    if not html:
        if metrics is not None:
            metrics.source_raw_counts[source.name] = 0
        return []

    soup = parse_html(html)
    lookback_hours = source.lookback_hours if source.lookback_hours else 168
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # Publication name from the page heading (fallback: from the URL slug)
    h1 = soup.find("h1")
    pub_name = h1.get_text(" ", strip=True) if h1 else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()

    pubs = []
    seen_keys = set()
    all_dates = set()

    # Scan table rows first (the canonical layout), then generic blocks
    candidates = soup.find_all("tr") or []
    if not candidates:
        candidates = soup.find_all(["li", "article", "div"], limit=400)

    for node in candidates:
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 600:
            continue
        release_date = _parse_us_date(text)
        if not release_date:
            continue
        # Every dated release on the page, regardless of the lookback window.
        # WASDE is monthly, so this is the number that says whether the page
        # still parses at all versus whether the window is simply too tight.
        all_dates.add(release_date.strftime("%Y-%m-%d"))
        if release_date < cutoff:
            continue
        key = release_date.strftime("%Y-%m-%d")
        if key in seen_keys:
            continue
        seen_keys.add(key)

        link = _best_file_link(node, url) or url
        pubs.append(build_publication(
            title=f"{pub_name} — {release_date.strftime('%d %b %Y')}",
            url=link,
            source_name="USDA",
            country="USA",
            flag_emoji="\U0001F1FA\U0001F1F8",
            published_at=release_date,
            summary="",
            language="en",
            location=USA_GEO,
        ))

    if metrics is not None:
        metrics.source_raw_counts[source.name] = len(all_dates)
    logger.info(
        f"ESMIS [{pub_name}]: {len(all_dates)} dated releases on page, "
        f"{len(pubs)} within {lookback_hours}h lookback window"
    )
    return pubs


if __name__ == "__main__":
    # Smoke test: python -m sources.esmis (from newsletter_v5/)
    import asyncio
    from models import SourceCategory, SourceType

    async def _main():
        cfg = SourceConfig(
            name="USDA ESMIS WASDE", url=DEFAULT_URL,
            category=SourceCategory.OFFICIAL_PUBLICATION,
            source_type=SourceType.REQUESTS_SCRAPER,
            lookback_hours=24 * 60,
        )
        async with aiohttp.ClientSession() as s:
            results = await scrape_esmis(cfg, s)
        print(f"{len(results)} releases found")
        for p in results[:10]:
            print(f"  - [{p.published_at}] {p.title[:90]} -> {p.url[:80]}")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
