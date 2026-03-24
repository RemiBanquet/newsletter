"""
Notion archiver for Daily Agri-News Digest v5.

Writes articles, publications, and company signals to Notion databases
after each pipeline run. Uses URL-based dedup to avoid double-posting.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp

from models import Article, ArticleCategory, CompanySignal, CompanyType, Publication, SignalType

logger = logging.getLogger("notion_archiver")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_CONCURRENT = 5          # Notion rate limit: 3 req/s, so 5 concurrent is safe
MAX_TEXT_LENGTH = 2000       # Notion rich_text limit per block
MAX_TITLE_LENGTH = 200       # Practical title limit


# ── Category mapping (ArticleCategory → Notion select value) ──────

_CATEGORY_MAP: dict[ArticleCategory, str] = {
    ArticleCategory.CROP_PRODUCTION:  "🛡️ Crop Protection |",
    ArticleCategory.CROP_LAND_USE:    "🌾 Crop land use |",
    ArticleCategory.YIELDS:           "📈 Yields |",
    ArticleCategory.AGTECH:           "🚀 AgTech |",
    ArticleCategory.CLIMATE_WEATHER:  "🌍 Climate |",
    ArticleCategory.MARKETS:          "💸 Market |",
    ArticleCategory.REGULATION:       "⚖️ Regulation |",
    ArticleCategory.COMPANY_NEWS:     "💸 Market |",
    ArticleCategory.OTHER:            "🤷 Misc |",
}

# CompanyType → Notion "Company Type" select value
_COMPANY_TYPE_MAP: dict[CompanyType, str] = {
    CompanyType.CLIENT:   "Other",    # We don't expose client/prospect in Notion
    CompanyType.PROSPECT: "Other",
}

# SignalType → Notion "Signal Type" select value
_SIGNAL_TYPE_MAP: dict[SignalType, str] = {
    SignalType.MARKET:      "M&A",
    SignalType.AGTECH:      "Product Launch",
    SignalType.REGULATION:  "Regulation",
    SignalType.PARTNERSHIP: "Partnership",
    SignalType.EXECUTIVE:   "Leadership Change",
    SignalType.PRODUCT:     "Product Launch",
    SignalType.FINANCIAL:   "Earnings",
    SignalType.OTHER:       "Other",
}


# ── Notion property builders ──────────────────────────────────────

def _title_prop(text: str) -> dict:
    """Build a Notion title property."""
    return {"title": [{"text": {"content": text[:MAX_TITLE_LENGTH]}}]}


def _rich_text_prop(text: str) -> dict:
    """Build a Notion rich_text property."""
    return {"rich_text": [{"text": {"content": text[:MAX_TEXT_LENGTH]}}]}


def _url_prop(url: str) -> dict:
    return {"url": url if url else None}


def _date_prop(dt: Optional[datetime]) -> dict:
    if dt is None:
        return {"date": None}
    return {"date": {"start": dt.strftime("%Y-%m-%d")}}


def _select_prop(value: str) -> dict:
    return {"select": {"name": value} if value else None}


def _multi_select_prop(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in values[:25]]}  # Notion max 25


def _number_prop(value: Optional[float]) -> dict:
    return {"number": value}


# ── Page builders ─────────────────────────────────────────────────

def _article_to_properties(article: Article) -> dict:
    """Convert an Article to Notion page properties for 🗞️ Newsletter."""
    props = {
        "Title":       _title_prop(article.title),
        "URL":         _url_prop(article.url),
        "Published":   _date_prop(article.published_at),
        "Source":       _rich_text_prop(article.source_name),
        "Category":     _select_prop(_CATEGORY_MAP.get(article.category, "🤷 Misc |")),
        "Summary":      _rich_text_prop(article.summary),
        "Tags":         _multi_select_prop(article.tags),
        "Language":     _select_prop(article.original_language),
        "Country ISO":  _rich_text_prop(article.location.country_iso),
        "Latitude":     _number_prop(article.location.latitude),
        "Longitude":    _number_prop(article.location.longitude),
    }
    return props


def _publication_to_properties(pub: Publication) -> dict:
    """Convert a Publication to Notion page properties for 💯 Official Publications."""
    props = {
        "Title":        _title_prop(pub.title),
        "URL":          _url_prop(pub.url),
        "Published":    _date_prop(pub.published_at),
        "Country":      _select_prop(pub.flag_emoji) if pub.flag_emoji else _select_prop(pub.country),
        "Source Name":  _rich_text_prop(pub.source_name),
        "Summary":      _rich_text_prop(pub.summary),
        "Language":     _select_prop(pub.original_language),
        "Country ISO":  _rich_text_prop(pub.location.country_iso),
        "Latitude":     _number_prop(pub.location.latitude),
        "Longitude":    _number_prop(pub.location.longitude),
    }
    return props


def _signal_to_properties(signal: CompanySignal) -> dict:
    """Convert a CompanySignal to Notion page properties for 🔔 Company Signals."""
    props = {
        "Title":        _title_prop(signal.title),
        "URL":          _url_prop(signal.url),
        "Company":      _rich_text_prop(signal.company_name),
        "Company Type": _select_prop(_COMPANY_TYPE_MAP.get(signal.company_type, "Other")),
        "Signal Type":  _select_prop(_SIGNAL_TYPE_MAP.get(signal.signal_type, "Other")),
        "Summary":      _rich_text_prop(signal.summary),
        "Source":       _rich_text_prop(signal.source_name),
        "Published":    _date_prop(signal.published_at),
        "Language":     _select_prop(signal.original_language),
        "Country ISO":  _rich_text_prop(signal.location.country_iso),
        "Latitude":     _number_prop(signal.location.latitude),
        "Longitude":    _number_prop(signal.location.longitude),
    }
    return props


# ── Core API calls ────────────────────────────────────────────────

async def _create_page(
    session: aiohttp.ClientSession,
    database_id: str,
    properties: dict,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Create a single Notion page. Returns True on success."""
    url = f"{NOTION_API_BASE}/pages"
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    async with semaphore:
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                # 409 = conflict (duplicate) — treat as success
                if resp.status == 409:
                    logger.debug("Page already exists (409 conflict)")
                    return True
                logger.warning(f"Notion create_page failed ({resp.status}): {body[:300]}")
                return False
        except Exception as e:
            logger.warning(f"Notion create_page error: {e}")
            return False


async def _archive_items(
    session: aiohttp.ClientSession,
    database_id: str,
    items: list,
    to_properties_fn,
    label: str,
    semaphore: asyncio.Semaphore,
) -> tuple[int, int]:
    """Archive a list of items to a Notion database.

    Returns (success_count, failure_count).
    """
    if not items:
        return 0, 0

    tasks = []
    for item in items:
        try:
            props = to_properties_fn(item)
        except Exception as e:
            logger.warning(f"Failed to build properties for {label}: {e}")
            continue
        tasks.append(_create_page(session, database_id, props, semaphore))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if r is True)
    failed = len(results) - success
    logger.info(f"Notion archive {label}: {success}/{len(results)} succeeded")
    return success, failed


# ── Public API ────────────────────────────────────────────────────

async def archive_to_notion(
    articles: list[Article],
    publications: list[Publication],
    signals: list[CompanySignal],
) -> dict:
    """Archive pipeline results to Notion databases.

    Reads database IDs from environment variables:
        NOTION_ARTICLES_DB_ID
        NOTION_PUBLICATIONS_DB_ID
        NOTION_SIGNALS_DB_ID

    Returns a dict with counts: {articles_ok, articles_fail, pubs_ok, pubs_fail, signals_ok, signals_fail}
    """
    token = os.environ.get("NOTION_TOKEN", "")
    articles_db = os.environ.get("NOTION_ARTICLES_DB_ID", "")
    pubs_db = os.environ.get("NOTION_PUBLICATIONS_DB_ID", "")
    signals_db = os.environ.get("NOTION_SIGNALS_DB_ID", "")

    if not token:
        logger.warning("NOTION_TOKEN not set — skipping Notion archive")
        return {"skipped": True}

    missing = []
    if not articles_db:
        missing.append("NOTION_ARTICLES_DB_ID")
    if not pubs_db:
        missing.append("NOTION_PUBLICATIONS_DB_ID")
    if not signals_db:
        missing.append("NOTION_SIGNALS_DB_ID")
    if missing:
        logger.warning(f"Missing env vars for Notion archive: {', '.join(missing)} — skipping")
        return {"skipped": True}

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with aiohttp.ClientSession(headers=headers) as session:
        # Run all three archives concurrently
        (art_ok, art_fail), (pub_ok, pub_fail), (sig_ok, sig_fail) = await asyncio.gather(
            _archive_items(session, articles_db, articles, _article_to_properties, "articles", semaphore),
            _archive_items(session, pubs_db, publications, _publication_to_properties, "publications", semaphore),
            _archive_items(session, signals_db, signals, _signal_to_properties, "signals", semaphore),
        )

    total_ok = art_ok + pub_ok + sig_ok
    total_fail = art_fail + pub_fail + sig_fail
    logger.info(f"Notion archive complete: {total_ok} created, {total_fail} failed")

    return {
        "articles_ok": art_ok, "articles_fail": art_fail,
        "pubs_ok": pub_ok, "pubs_fail": pub_fail,
        "signals_ok": sig_ok, "signals_fail": sig_fail,
    }
