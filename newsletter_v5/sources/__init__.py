"""
Publication scraper registry.

Maps scraper_id values (from Notion/YAML config) to async scraper functions.
Each scraper function has the signature:

    async def scrape_xxx(source: SourceConfig, session: aiohttp.ClientSession) -> list[Publication]

To add a new scraper:
1. Create a new file in sources/ (e.g., sources/my_source.py)
2. Implement the async scraper function
3. Add it to SCRAPER_REGISTRY below

The fetcher.py module calls run_scraper_sources() which dispatches to the
correct scraper based on source.scraper_id.
"""

import asyncio
import logging
from typing import Callable, Awaitable

import aiohttp

from models import Publication, RunMetrics, SourceConfig, SourceType

logger = logging.getLogger(__name__)

# ── Import scrapers ───────────────────────────────────────────────
# Lazy imports to avoid circular dependencies and allow graceful
# degradation if a scraper has missing dependencies.

def _get_scraper_registry() -> dict[str, Callable]:
    """Build the scraper registry. Lazy to handle import errors gracefully."""
    registry = {}

    try:
        from sources.istat import scrape_istat
        registry["scrape_istat"] = scrape_istat
    except ImportError as e:
        logger.warning(f"Could not import ISTAT scraper: {e}")

    try:
        from sources.mapa import scrape_mapa
        registry["scrape_mapa"] = scrape_mapa
    except ImportError as e:
        logger.warning(f"Could not import MAPA scraper: {e}")

    try:
        from sources.tuik import scrape_tuik
        registry["scrape_tuik"] = scrape_tuik
    except ImportError as e:
        logger.warning(f"Could not import TUIK scraper: {e}")

    try:
        from sources.ibge_pam import scrape_ibge_pam
        registry["scrape_ibge_pam"] = scrape_ibge_pam
    except ImportError as e:
        logger.warning(f"Could not import IBGE PAM scraper: {e}")

    # ── Scrapers ported from v4.6 ────────────────────────────────

    try:
        from sources.agreste import scrape_agreste
        registry["scrape_agreste"] = scrape_agreste
    except ImportError as e:
        logger.warning(f"Could not import Agreste scraper: {e}")

    try:
        from sources.jrc import scrape_jrc
        registry["scrape_jrc"] = scrape_jrc
    except ImportError as e:
        logger.warning(f"Could not import JRC scraper: {e}")

    try:
        from sources.caa import scrape_caa
        registry["scrape_caa"] = scrape_caa
    except ImportError as e:
        logger.warning(f"Could not import CAA scraper: {e}")

    try:
        from sources.statcan import scrape_statcan
        registry["scrape_statcan"] = scrape_statcan
    except ImportError as e:
        logger.warning(f"Could not import StatCan scraper: {e}")

    try:
        from sources.uk_defra import scrape_uk
        registry["scrape_uk"] = scrape_uk
    except ImportError as e:
        logger.warning(f"Could not import UK DEFRA scraper: {e}")

    try:
        from sources.ksh import scrape_ksh
        registry["scrape_ksh"] = scrape_ksh
    except ImportError as e:
        logger.warning(f"Could not import KSH scraper: {e}")

    try:
        from sources.coceral import scrape_coceral
        registry["scrape_coceral"] = scrape_coceral
    except ImportError as e:
        logger.warning(f"Could not import COCERAL scraper: {e}")

    try:
        from sources.destatis import scrape_destatis
        registry["scrape_destatis"] = scrape_destatis
    except ImportError as e:
        logger.warning(f"Could not import Destatis scraper: {e}")

    try:
        from sources.govua import scrape_govua
        registry["scrape_govua"] = scrape_govua
    except ImportError as e:
        logger.warning(f"Could not import GovUA scraper: {e}")

    # ── New official-data scrapers (added 2026-06-10) ─────────────

    try:
        from sources.conab import scrape_conab
        registry["scrape_conab"] = scrape_conab
    except ImportError as e:
        logger.warning(f"Could not import CONAB scraper: {e}")

    try:
        from sources.grainsa import scrape_grainsa
        registry["scrape_grainsa"] = scrape_grainsa
    except ImportError as e:
        logger.warning(f"Could not import Grain SA scraper: {e}")

    try:
        from sources.bcr import scrape_bcr
        registry["scrape_bcr"] = scrape_bcr
    except ImportError as e:
        logger.warning(f"Could not import BCR scraper: {e}")

    try:
        from sources.esmis import scrape_esmis
        registry["scrape_esmis"] = scrape_esmis
    except ImportError as e:
        logger.warning(f"Could not import ESMIS scraper: {e}")

    return registry


# ── Public API ────────────────────────────────────────────────────

async def run_scraper_sources(
    sources: list[SourceConfig],
    metrics: RunMetrics,
) -> list[Publication]:
    """
    Run all enabled scraper sources concurrently — both requests-scraper
    AND selenium-scraper types.

    For each source with a valid scraper_id, dispatches to the registered
    scraper function. The scraper module itself is responsible for choosing
    its execution strategy (pure requests, requests-with-Selenium-fallback,
    or pure Selenium); blocking Selenium calls must run in an executor
    thread inside the scraper.

    Args:
        sources: All source configs (will be filtered to scraper types)
        metrics: Run metrics to update

    Returns:
        Combined list of publications from all scrapers
    """
    registry = _get_scraper_registry()

    scraper_sources = [
        s for s in sources
        if s.enabled
        and s.source_type in (SourceType.REQUESTS_SCRAPER, SourceType.SELENIUM_SCRAPER)
        and s.scraper_id
    ]

    if not scraper_sources:
        logger.info("No scraper sources to run")
        return []

    by_type = {}
    for s in scraper_sources:
        by_type.setdefault(s.source_type.value, []).append(s.name)
    logger.info(
        f"Running {len(scraper_sources)} scraper sources "
        f"({', '.join(f'{k}: {len(v)}' for k, v in by_type.items())}): "
        f"{', '.join(s.name for s in scraper_sources)}"
    )

    async with aiohttp.ClientSession() as session:
        tasks = []
        valid_sources = []

        for source in scraper_sources:
            scraper_fn = registry.get(source.scraper_id)
            if not scraper_fn:
                logger.warning(
                    f"No scraper registered for '{source.scraper_id}' "
                    f"(source: {source.name}). Skipping."
                )
                metrics.source_errors.append(
                    f"{source.name}: scraper '{source.scraper_id}' not found"
                )
                continue

            tasks.append(_run_single_scraper(scraper_fn, source, session))
            valid_sources.append(source)

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

    publications = []
    for i, result in enumerate(results):
        source = valid_sources[i]
        if isinstance(result, Exception):
            logger.error(f"Scraper error for {source.name}: {result}")
            metrics.source_errors.append(f"{source.name}: {result}")
            metrics.source_counts[source.name] = 0
        elif isinstance(result, list):
            publications.extend(result)
            metrics.sources_healthy += 1
            metrics.source_counts[source.name] = len(result)
            logger.info(f"[{source.name}] {len(result)} publications scraped")

    metrics.sources_total += len(valid_sources)
    logger.info(f"Scrapers: {len(publications)} publications from {len(valid_sources)} sources")
    return publications


async def _run_single_scraper(
    scraper_fn: Callable,
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """Run a single scraper with error isolation."""
    try:
        return await scraper_fn(source, session)
    except Exception as e:
        logger.error(f"Scraper '{source.scraper_id}' ({source.name}) failed: {e}")
        raise
