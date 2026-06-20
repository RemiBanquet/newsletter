#!/usr/bin/env python3
"""
Daily Agri-News Digest v5 — Main orchestrator.

Usage:
    python newsletter_v5.py                  # Production run
    python newsletter_v5.py --test           # Test mode: send only to admin, print report
    python newsletter_v5.py --dry-run        # Dry run: fetch + classify, no send
    python newsletter_v5.py --test --save-html output.html  # Save HTML to file for preview

Environment variables required:
    ANTHROPIC_API_KEY       — Claude API key
    LEMLIST_API_KEY         — Lemlist API key
    NOTION_TOKEN            — Notion integration token
    NOTION_SOURCES_DB_ID    — Notion "Newsletter Sources" database ID
    NOTION_RECIPIENTS_DB_ID — Notion "Newsletter Recipients" database ID
    NOTION_COMPANIES_DB_ID  — Notion "Tracked Companies" database ID
    NOTION_COUNTRIES_DB_ID  — Notion "Countries of Interest" database ID
    LEMLIST_ADMIN_LEAD_ID   — Lemlist lead ID for admin reports (optional)
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import traceback
import unicodedata
from datetime import datetime, timezone

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import CompanySignal, CompanyType, RunMetrics, SourceCategory
from config_loader import load_config
from fetcher import fetch_all_articles, fetch_all_publications, fetch_all_signals
from psd_fetcher import fetch_psd_data
from eurostat_fetcher import fetch_eurostat_publications
from sources import run_scraper_sources
from classifier import ArticleClassifier
from geocoder import Geocoder
from dedup import DeduplicationManager
from renderer import render_newsletter
from sender import send_newsletter, send_admin_report, _print_admin_report
from notion_archiver import archive_to_notion
from source_health import (
    SourceHealthTracker, DEFAULT_THRESHOLD_MEDIA, DEFAULT_THRESHOLD_OFFICIAL,
)

# ── Logging setup ─────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("newsletter_v5")


def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy dedup: lowercase, strip punctuation/accents, collapse whitespace."""
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")
    title = title.lower()
    title = re.sub(r"[^a-z0-9 ]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def dedup_signals_cross_company(signals: list[CompanySignal]) -> list[CompanySignal]:
    """Remove duplicate signals that appear across multiple company queries.

    When the same article appears for multiple companies (e.g., a fertilizer
    lawsuit naming CF Industries, Mosaic, and Yara), keep only the first
    occurrence. Uses normalized title matching.
    """
    seen_titles: dict[str, str] = {}  # normalized_title -> company_name
    unique = []
    dupes = 0
    for signal in signals:
        norm = _normalize_title(signal.title)
        if norm in seen_titles:
            dupes += 1
            logger.debug(
                f"Cross-company signal dedup: '{signal.title}' "
                f"({signal.company_name}) already seen for {seen_titles[norm]}"
            )
            continue
        seen_titles[norm] = signal.company_name
        unique.append(signal)
    if dupes > 0:
        logger.info(f"Cross-company signal dedup removed {dupes} duplicates")
    return unique


# ── Main pipeline ─────────────────────────────────────────────────

async def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full newsletter pipeline."""
    metrics = RunMetrics(start_time=datetime.now(timezone.utc))

    try:
        # ── Step 1: Load config from Notion (or YAML fallback) ──
        notion_token = os.environ.get("NOTION_TOKEN", "")
        if notion_token and os.environ.get("NOTION_SOURCES_DB_ID"):
            logger.info("Loading config from Notion...")
            config = load_config(
                notion_token=notion_token,
                sources_db_id=os.environ["NOTION_SOURCES_DB_ID"],
                recipients_db_id=os.environ["NOTION_RECIPIENTS_DB_ID"],
                companies_db_id=os.environ["NOTION_COMPANIES_DB_ID"],
                countries_db_id=os.environ["NOTION_COUNTRIES_DB_ID"],
            )
        else:
            logger.info("Notion env vars not set — loading from YAML cache...")
            from config_loader import load_config_from_cache
            config = load_config_from_cache()
            if not config:
                raise RuntimeError("No Notion config and no YAML cache found")
        logger.info(
            f"Config loaded: {len(config.active_sources)} sources, "
            f"{len(config.active_recipients)} recipients, "
            f"{len(config.clients)} clients, {len(config.prospects)} prospects, "
            f"{len(config.active_countries)} countries"
        )

        # ── Step 2: Fetch from all sources concurrently ──
        logger.info("Fetching articles, publications, signals, and PSD data...")

        # PSD download is sync (requests lib), run in executor to not block event loop
        loop = asyncio.get_event_loop()
        psd_future = loop.run_in_executor(None, fetch_psd_data)

        articles, publications, signals, eurostat_pubs, scraper_pubs = await asyncio.gather(
            fetch_all_articles(config.active_sources, metrics),
            fetch_all_publications(config.active_sources, metrics),
            fetch_all_signals(config.companies, metrics),
            fetch_eurostat_publications(),
            run_scraper_sources(config.active_sources, metrics),
        )

        # Merge Eurostat agriculture dataset updates into publications
        if eurostat_pubs:
            logger.info(f"Eurostat: {len(eurostat_pubs)} agriculture dataset updates added")
            publications.extend(eurostat_pubs)
        # Always record Eurostat health (even when 0)
        metrics.source_counts["Eurostat"] = len(eurostat_pubs or [])

        # Merge scraper publications (ISTAT, MAPA Avances, TUIK, etc.)
        if scraper_pubs:
            logger.info(f"Scrapers: {len(scraper_pubs)} publications added")
            publications.extend(scraper_pubs)

        psd_data = await psd_future
        logger.info(
            f"Fetched: {len(articles)} articles, {len(publications)} publications "
            f"(incl. {len(eurostat_pubs)} Eurostat, {len(scraper_pubs)} scraped), "
            f"{len(signals)} signals, PSD: {'OK' if psd_data.get('available') else 'unavailable'}"
        )

        # ── Step 3: Deduplicate ──
        logger.info("Deduplicating...")
        dedup = DeduplicationManager(base_dir=os.path.dirname(os.path.abspath(__file__)))
        articles, metrics.articles_duplicate = dedup.filter_new_articles(articles)
        publications, metrics.publications_duplicate = dedup.filter_new_publications(publications)
        signals, metrics.signals_duplicate = dedup.filter_new_signals(signals)
        logger.info(
            f"After dedup: {len(articles)} articles, {len(publications)} publications, "
            f"{len(signals)} signals"
        )

        # ── Step 4: Classify with Claude (concurrent) ──
        logger.info("Classifying with Claude...")
        api_key = os.environ["ANTHROPIC_API_KEY"]
        classifier = ArticleClassifier(api_key=api_key, metrics=metrics)

        articles = await classifier.classify_articles_batch(articles)
        signals = await classifier.classify_signals_batch(signals)

        # Filter to relevant only
        relevant_articles = [a for a in articles if a.relevant]
        irrelevant_count = len(articles) - len(relevant_articles)
        metrics.articles_accepted = len(relevant_articles)
        metrics.articles_rejected = irrelevant_count

        # Filter signals: remove those marked irrelevant (signal_type is None)
        relevant_signals = [s for s in signals if s.signal_type is not None]

        # Cross-company signal dedup (same article appearing for multiple companies)
        relevant_signals = dedup_signals_cross_company(relevant_signals)

        metrics.signals_accepted = len(relevant_signals)
        metrics.signals_rejected = len(signals) - len(relevant_signals)

        # Classify publications (relevance filter + translation in one step)
        publications = await classifier.classify_publications_batch(publications)
        relevant_publications = [p for p in publications if p.relevant]
        metrics.publications_accepted = len(relevant_publications)
        metrics.publications_rejected = len(publications) - len(relevant_publications)
        publications = relevant_publications

        # Translate remaining non-English articles and signals
        await classifier.translate_non_english(
            publications=publications,
            articles=relevant_articles,
            signals=relevant_signals,
        )

        logger.info(
            f"After classification: {len(relevant_articles)} articles, "
            f"{len(publications)} publications, {len(relevant_signals)} signals"
        )

        # ── Step 5: Geocode ──
        logger.info("Geocoding...")
        geocoder = Geocoder(metrics=metrics)
        relevant_articles = await geocoder.geocode_articles(relevant_articles)
        publications = await geocoder.geocode_publications(publications)
        relevant_signals = await geocoder.geocode_signals(relevant_signals)

        # ── Step 6: Split signals into client vs prospect ──
        client_signals = [s for s in relevant_signals if s.company_type == CompanyType.CLIENT]
        prospect_signals = [s for s in relevant_signals if s.company_type == CompanyType.PROSPECT]

        # ── Step 6b: Generate market brief (fail-open) ──
        # Sits on top of the digest. If generation or verification fails, the
        # digest ships without it rather than with a wrong claim.
        today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
        market_brief = None
        try:
            logger.info("Generating market brief...")
            from market_brief import MarketBriefGenerator
            brief_gen = MarketBriefGenerator(api_key=api_key, metrics=metrics)
            market_brief = await brief_gen.generate(
                date=today,
                articles=relevant_articles,
                client_signals=client_signals,
                prospect_signals=prospect_signals,
                publications=publications,
            )
            metrics.brief_status = brief_gen.last_status
            if market_brief:
                logger.info(f"Market brief ready: {len(market_brief.sections)} sections")
            else:
                logger.warning(f"Market brief unavailable — {brief_gen.last_status}")
        except Exception as e:
            logger.warning(f"Market brief generation failed, sending without it: {e}")
            market_brief = None
            metrics.brief_status = f"omitted (error: {type(e).__name__})"

        # ── Step 7: Render newsletter HTML ──
        logger.info("Rendering newsletter...")
        html = render_newsletter(
            date=today,
            articles=relevant_articles,
            publications=publications,
            client_signals=client_signals,
            prospect_signals=prospect_signals,
            psd_data=psd_data,
            market_brief=market_brief,
        )

        # Save HTML to file if requested
        if args.save_html:
            with open(args.save_html, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"Saved HTML to {args.save_html}")

        # ── Step 7b: Archive to Notion ──
        if not args.dry_run:
            logger.info("Archiving to Notion...")
            archive_result = await archive_to_notion(
                articles=relevant_articles,
                publications=publications,
                signals=relevant_signals,
            )
            if not archive_result.get("skipped"):
                logger.info(
                    f"Notion archive: "
                    f"{archive_result.get('articles_ok', 0)} articles, "
                    f"{archive_result.get('pubs_ok', 0)} publications, "
                    f"{archive_result.get('signals_ok', 0)} signals"
                )
        else:
            logger.info("DRY RUN — skipping Notion archive")

        # ── Step 8: Send newsletter ──
        if args.dry_run:
            logger.info("DRY RUN — skipping email send")
        else:
            logger.info("Sending newsletter...")
            subject = f"🛰️ Daily Agri-News Digest — {datetime.now(timezone.utc).strftime('%d %b %Y')}"
            send_newsletter(config, subject, html, metrics, test_mode=args.test)

        # ── Step 9: Mark sent items and save dedup state ──
        if not args.dry_run:
            dedup.mark_sent(relevant_articles, publications, relevant_signals)
            logger.info("Dedup state saved")

        # ── Step 9b: Update source-health tracker ──
        # Record per-source counts (passed + raw); flag sources past their
        # cadence threshold (Notion "Max Silent Days", else category default).
        tracker = SourceHealthTracker(
            base_dir=os.path.dirname(os.path.abspath(__file__))
        )
        thresholds = {}
        for s in config.sources:
            if not s.enabled:
                continue
            default = (
                DEFAULT_THRESHOLD_MEDIA
                if s.category == SourceCategory.MEDIA
                else DEFAULT_THRESHOLD_OFFICIAL
            )
            thresholds[s.name] = s.max_silent_days or default

        if not args.dry_run:
            for source_name, count in metrics.source_counts.items():
                tracker.record(
                    source_name, count,
                    raw_count=metrics.source_raw_counts.get(source_name),
                )
            tracker.prune(active_names=set(thresholds) | {"Eurostat"})
            metrics.silent_sources = tracker.silent_sources(thresholds=thresholds)
            tracker.save()
            if metrics.silent_sources:
                dead = [s for s in metrics.silent_sources if s["status"] == "DEAD"]
                logger.warning(
                    f"⚠️  {len(metrics.silent_sources)} source(s) past their "
                    f"silence threshold ({len(dead)} DEAD): "
                    f"{', '.join(s['name'] for s in metrics.silent_sources)}"
                )
        else:
            # In dry-run, compute silent sources without writing state
            metrics.silent_sources = tracker.silent_sources(thresholds=thresholds)

        # ── Step 10: Admin report ──
        metrics.end_time = datetime.now(timezone.utc)

        if args.test or args.dry_run:
            _print_admin_report(metrics, success=True)
        else:
            send_admin_report(metrics, success=True)

        logger.info(f"Pipeline complete in {metrics.runtime_display}")

    except Exception as e:
        metrics.end_time = datetime.now(timezone.utc)
        error_msg = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        logger.error(f"Pipeline failed: {error_msg}")

        if args.test or args.dry_run:
            _print_admin_report(metrics, success=False, error_message=str(e))
        else:
            send_admin_report(metrics, success=False, error_message=error_msg)

        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily Agri-News Digest v5")
    parser.add_argument("--test", action="store_true", help="Test mode: send only to admin")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + classify only, no email send")
    parser.add_argument("--save-html", type=str, help="Save rendered HTML to file")
    args = parser.parse_args()

    if args.test:
        logger.info("Running in TEST mode")
    if args.dry_run:
        logger.info("Running in DRY RUN mode")

    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
