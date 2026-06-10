"""
CONAB — Companhia Nacional de Abastecimento (Brazil).

Scrapes the CONAB news page for monthly grain survey announcements
("levantamento da safra de grãos") and other crop publications. CONAB's
monthly survey is Brazil's reference forward-looking crop estimate
(area, yield, production per state per crop) — richer and far more
current than IBGE PAM, which is annual and backward-looking.

Page verified 2026-06-10: https://www.gov.br/conab/pt-br/assuntos/noticias
is a server-rendered Plone site; article links + DD/MM/YYYY dates are in
the initial HTML. No RSS exists (Joomla-style feed paths redirect home).

Data companion (not scraped here, linked in publications): the Série
Histórica CSV refreshed with each survey —
https://portaldeinformacoes.conab.gov.br/downloads/arquivos/SerieHistoricaGraos.txt

Extraction strategy is deliberately generic (dated links + keyword filter)
so minor Plone template changes don't break it.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import aiohttp

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import build_publication, fetch_html, parse_html

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.gov.br/conab/pt-br/assuntos/noticias"

# Portuguese crop/survey terms — title must match at least one
KEYWORDS = [
    "safra", "levantamento", "grão", "graos", "grãos", "milho", "soja",
    "trigo", "algodão", "algodao", "arroz", "feijão", "feijao", "colheita",
    "plantio", "produção", "producao", "área", "area plantada", "café", "cafe",
]

DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

CONAB_GEO = GeoLocation(
    place_name="Brazil", country_iso="BR", latitude=-15.78, longitude=-47.93,
)


def _find_date_near(tag) -> datetime | None:
    """Look for a DD/MM/YYYY date in the link's surroundings (parent blocks)."""
    node = tag
    for _ in range(3):  # climb up to 3 levels
        if node is None:
            break
        m = DATE_RE.search(node.get_text(" ", strip=True))
        if m:
            day, month, year = (int(g) for g in m.groups())
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return None
        node = node.parent
    return None


async def scrape_conab(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """Scrape CONAB news listing for crop survey announcements."""
    url = source.url or DEFAULT_URL
    html = await fetch_html(url, session)
    if not html:
        return []

    soup = parse_html(html)
    lookback_hours = source.lookback_hours if source.lookback_hours else 168
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    pubs = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        href = a["href"]
        if not title or len(title) < 25:
            continue
        if "/noticias/" not in href:
            continue
        full_url = urljoin(url, href)
        if full_url in seen_urls or full_url.rstrip("/") == url.rstrip("/"):
            continue

        title_lower = title.lower()
        if not any(kw in title_lower for kw in KEYWORDS):
            continue

        pub_date = _find_date_near(a)
        if pub_date and pub_date < cutoff:
            continue

        seen_urls.add(full_url)
        pubs.append(build_publication(
            title=title,
            url=full_url,
            source_name="CONAB",
            country="Brazil",
            flag_emoji="🇧🇷",
            published_at=pub_date or datetime.now(timezone.utc),
            summary="",
            language="pt",
            location=CONAB_GEO,
        ))

    logger.info(f"CONAB: {len(pubs)} publications within lookback window")
    return pubs


if __name__ == "__main__":
    # Smoke test: python -m sources.conab (from newsletter_v5/)
    import asyncio
    from models import SourceCategory, SourceType

    async def _main():
        cfg = SourceConfig(
            name="CONAB Brazil", url=DEFAULT_URL,
            category=SourceCategory.OFFICIAL_PUBLICATION,
            source_type=SourceType.REQUESTS_SCRAPER,
            lookback_hours=24 * 45,
        )
        async with aiohttp.ClientSession() as s:
            results = await scrape_conab(cfg, s)
        print(f"{len(results)} publications found")
        for p in results[:10]:
            print(f"  - [{p.published_at}] {p.title[:90]}")

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
