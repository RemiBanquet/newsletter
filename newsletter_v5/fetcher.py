"""
Source fetcher: async RSS + scraper fetching with keyword pre-filter.
Handles articles, publications, and company signal feeds.
"""

import asyncio
import hashlib
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import feedparser
from langdetect import detect

from models import (
    Article, CompanyConfig, CompanySignal, CompanyType, Publication,
    RunMetrics, SourceCategory, SourceConfig, SourceType,
)
from constants import (
    ARTICLE_LOOKBACK_HOURS, CROP_KEYWORDS, CROP_CONTEXTUAL_KEYWORDS,
    SIGNAL_LOOKBACK_DAYS,
)

logger = logging.getLogger(__name__)


def _keyword_match(text: str) -> bool:
    """Check if text matches crop keywords (pre-filter before LLM)."""
    text_lower = text.lower()
    has_crop = any(kw in text_lower for kw in CROP_KEYWORDS)
    has_context = any(kw in text_lower for kw in CROP_CONTEXTUAL_KEYWORDS)
    return has_crop or has_context


def _detect_language(text: str) -> str:
    """Detect language, default to 'en' on failure."""
    try:
        return detect(text)
    except Exception:
        return "en"


def _make_id(url: str, title: str = "") -> str:
    """Stable ID from URL + title (title-aware to avoid SPA collisions)."""
    key = f"{url.strip()}|{title.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _google_news_rss_url(query: str) -> str:
    """Build a Google News RSS URL for a search query."""
    encoded = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"


# Government and stat-office RSS endpoints (Destatis, MAPA, KSH, INSSE) often
# 403 unrecognised UAs. Use a real-browser UA. Switch back to a polite custom
# UA only if a publisher's robots.txt requires it.
_FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _fetch_feed(url: str, session: aiohttp.ClientSession, timeout: int = 30) -> Optional[feedparser.FeedParserDict]:
    """Fetch and parse an RSS feed."""
    try:
        async with session.get(url, headers=_FEED_HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                # Pass raw bytes to feedparser so it can read the XML
                # encoding declaration (Hungarian KSH feed mis-labels charset)
                data = await resp.read()
                return feedparser.parse(data)
            else:
                logger.warning(f"Feed {url} returned status {resp.status}")
                return None
    except Exception as e:
        logger.warning(f"Failed to fetch feed {url}: {e}")
        return None


def _parse_feed_date(entry: dict) -> Optional[datetime]:
    """Parse published date from a feed entry.

    feedparser usually fills `published_parsed`/`updated_parsed` automatically.
    When it can't (non-standard date formats from MAPA/Agroes/Destatis), fall
    back to the raw `published`/`updated`/`pubDate` string fields and try
    RFC 2822 parsing. Logs at debug when nothing parses, so the next admin
    report exposes silent date-filtering.
    """
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            return datetime(*published[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    # Fallback: try the raw string fields
    from email.utils import parsedate_to_datetime
    for field in ("published", "updated", "pubDate", "date"):
        raw = entry.get(field, "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    title = entry.get("title", "")[:60]
    logger.debug(f"_parse_feed_date: no parseable date in entry: {title}")
    return None


# ── Article fetching ──────────────────────────────────────────────

async def fetch_articles_from_source(
    source: SourceConfig,
    session: aiohttp.ClientSession,
    metrics: RunMetrics,
) -> list[Article]:
    """Fetch articles from a single RSS source with keyword pre-filter."""
    if source.source_type != SourceType.RSS:
        return []  # Scrapers handled separately

    feed = await _fetch_feed(source.url, session)
    if not feed or not feed.entries:
        metrics.source_errors.append(f"{source.name}: no entries or fetch failed")
        return []

    # Google News RSS dates reflect original article publish time, not indexing time.
    # Use a wider window (14 days) to avoid filtering out recently indexed older articles.
    # Per-source override (source.lookback_hours) wins if set.
    is_google_news = "news.google.com" in source.url
    if source.lookback_hours and source.lookback_hours > 0:
        lookback_hours = source.lookback_hours
    elif is_google_news:
        lookback_hours = 336
    else:
        lookback_hours = ARTICLE_LOOKBACK_HOURS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []
    total_entries = len(feed.entries)
    skipped_date = 0
    skipped_keyword = 0
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        url = entry.get("link", "").strip()
        if not title or not url:
            continue

        # Date filter: skip articles older than lookback window
        pub_date = _parse_feed_date(entry)
        if pub_date and pub_date < cutoff:
            skipped_date += 1
            if is_google_news and skipped_date <= 3:  # Log first 3 skips for debugging
                age_days = (datetime.now(timezone.utc) - pub_date).total_seconds() / 86400
                logger.debug(f"[{source.name}] date-skipped (age={age_days:.1f}d): {title[:80]}")
            continue
        # Skip entries with no parseable date — almost always archival content
        if pub_date is None:
            skipped_date += 1
            if is_google_news:
                logger.debug(f"[{source.name}] no-date-skipped: {title[:80]}")
            continue

        # Get content for keyword matching
        content = entry.get("summary", "") or entry.get("description", "") or ""

        # Keyword pre-filter
        combined_text = f"{title} {content}"
        keywords = source.keywords_filter or None  # Source-specific override
        if keywords:
            if not any(kw.lower() in combined_text.lower() for kw in keywords):
                skipped_keyword += 1
                logger.debug(f"[{source.name}] keyword-filtered: {title[:80]}")
                continue
        else:
            if not _keyword_match(combined_text):
                skipped_keyword += 1
                logger.debug(f"[{source.name}] keyword-filtered: {title[:80]}")
                continue

        metrics.articles_fetched += 1

        articles.append(Article(
            id=_make_id(url, title),
            title=title,
            url=url,
            source_name=source.name,
            published_at=pub_date,
            original_language=_detect_language(title),
            raw_content=f"{title}\n\n{content[:2500]}",
        ))

    # Cap Google News sources to avoid flooding the pipeline (most recent first)
    max_per_source = 15 if is_google_news else None
    if max_per_source and len(articles) > max_per_source:
        articles.sort(key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        skipped_cap = len(articles) - max_per_source
        articles = articles[:max_per_source]
        logger.info(f"[{source.name}] capped to {max_per_source} most recent (dropped {skipped_cap})")

    logger.info(
        f"[{source.name}] {total_entries} entries → "
        f"{len(articles)} passed, {skipped_date} date-filtered, "
        f"{skipped_keyword} keyword-filtered"
    )
    return articles


async def fetch_all_articles(
    sources: list[SourceConfig],
    metrics: RunMetrics,
) -> list[Article]:
    """Fetch articles from all RSS sources concurrently."""
    rss_sources = [s for s in sources if s.source_type == SourceType.RSS and s.category == SourceCategory.MEDIA]
    logger.info(f"Fetching articles from {len(rss_sources)} RSS sources")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_articles_from_source(s, session, metrics) for s in rss_sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    articles = []
    for i, result in enumerate(results):
        source_name = rss_sources[i].name if i < len(rss_sources) else "unknown"
        if isinstance(result, Exception):
            logger.error(f"Error fetching {source_name}: {result}")
            metrics.source_errors.append(f"{source_name}: {result}")
            metrics.source_counts[source_name] = 0
        elif isinstance(result, list):
            articles.extend(result)
            metrics.sources_healthy += 1
            metrics.source_counts[source_name] = len(result)

    metrics.sources_total += len(rss_sources)
    logger.info(f"Fetched {len(articles)} articles from {metrics.sources_healthy} healthy sources")
    return articles


# ── Publication fetching ──────────────────────────────────────────

async def fetch_publications_from_source(
    source: SourceConfig,
    session: aiohttp.ClientSession,
    metrics: RunMetrics,
) -> list[Publication]:
    """Fetch official publications from an RSS source.

    Uses source.lookback_hours when set, else 168h (7 days) — stat offices
    typically publish weekly or less, so the article default of 48h is too
    tight for this category and was filtering out everything.
    """
    feed = await _fetch_feed(source.url, session)
    if not feed or not feed.entries:
        metrics.source_errors.append(f"{source.name}: no entries or fetch failed")
        return []

    lookback = source.lookback_hours if source.lookback_hours and source.lookback_hours > 0 else 168
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    pubs = []
    total_entries = len(feed.entries)
    skipped_date = 0
    skipped_keyword = 0
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        url = entry.get("link", "").strip()
        if not title or not url:
            continue

        # Date filter: skip publications older than ARTICLE_LOOKBACK_HOURS
        pub_date = _parse_feed_date(entry)
        if pub_date and pub_date < cutoff:
            skipped_date += 1
            continue
        if pub_date is None:
            skipped_date += 1
            continue

        content = entry.get("summary", "") or ""
        combined = f"{title} {content}"

        # Keyword pre-filter: for publication sources (statistical offices),
        # require BOTH a crop keyword AND a contextual keyword to avoid
        # non-agricultural statistics (housing prices, CPI, etc.)
        text_lower = combined.lower()
        has_crop = any(kw in text_lower for kw in CROP_KEYWORDS)
        has_context = any(kw in text_lower for kw in CROP_CONTEXTUAL_KEYWORDS)
        if not (has_crop and has_context):
            # Fallback: still accept if source has specific keywords configured
            if source.keywords_filter:
                if not any(kw.lower() in text_lower for kw in source.keywords_filter):
                    skipped_keyword += 1
                    logger.debug(f"[{source.name}] keyword-filtered: {title[:80]}")
                    continue
            else:
                skipped_keyword += 1
                logger.debug(f"[{source.name}] keyword-filtered: {title[:80]}")
                continue

        metrics.publications_fetched += 1

        pubs.append(Publication(
            id=_make_id(url, title),
            title=title,
            url=url,
            source_name=source.name,
            country=source.country,
            published_at=pub_date,
            original_language=_detect_language(title),
            summary=content[:500],
        ))

    logger.info(
        f"[{source.name}] {total_entries} entries → "
        f"{len(pubs)} passed, {skipped_date} date-filtered, "
        f"{skipped_keyword} keyword-filtered"
    )
    return pubs


async def fetch_all_publications(
    sources: list[SourceConfig],
    metrics: RunMetrics,
) -> list[Publication]:
    """Fetch publications from all publication RSS sources concurrently."""
    pub_sources = [
        s for s in sources
        if s.source_type == SourceType.RSS and s.category == SourceCategory.OFFICIAL_PUBLICATION
    ]
    logger.info(f"Fetching publications from {len(pub_sources)} sources")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_publications_from_source(s, session, metrics) for s in pub_sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    pubs = []
    for i, result in enumerate(results):
        source_name = pub_sources[i].name if i < len(pub_sources) else "unknown"
        if isinstance(result, Exception):
            logger.error(f"Error fetching {source_name}: {result}")
            metrics.source_errors.append(f"{source_name}: {result}")
            metrics.source_counts[source_name] = 0
        elif isinstance(result, list):
            pubs.extend(result)
            metrics.sources_healthy += 1
            metrics.source_counts[source_name] = len(result)

    metrics.sources_total += len(pub_sources)
    logger.info(f"Fetched {len(pubs)} publications")
    return pubs


# ── Company signal fetching ───────────────────────────────────────

async def fetch_company_signals(
    company: CompanyConfig,
    session: aiohttp.ClientSession,
    metrics: RunMetrics,
) -> list[CompanySignal]:
    """Fetch signals for a single company via Google News RSS (7-day lookback)."""
    query = company.search_keywords or f"{company.name} agriculture"
    url = _google_news_rss_url(query)

    feed = await _fetch_feed(url, session)
    if not feed or not feed.entries:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_LOOKBACK_DAYS)
    signals = []

    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue

        pub_date = _parse_feed_date(entry)
        if pub_date and pub_date < cutoff:
            continue

        # Extract source name from Google News title format: "Title - Source"
        source_name = ""
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0].strip()
            source_name = parts[1].strip()

        # Keyword pre-filter: signal must mention ag/crop/input terms.
        # More lenient than article filter — OR logic (crop OR context keyword),
        # plus ag-input-specific terms (the company's core business domains).
        signal_text = title.lower()
        has_crop = any(kw in signal_text for kw in CROP_KEYWORDS)
        has_context = any(kw in signal_text for kw in CROP_CONTEXTUAL_KEYWORDS)
        has_ag_input = any(kw in signal_text for kw in [
            "crop protection", "pesticide", "herbicide", "fungicide", "insecticide",
            "fertilizer", "fertiliser", "seed", "seeds", "biotech", "biostimulant",
            "ag tech", "agtech", "agrochemical", "plant science", "precision ag",
            "digital farming", "agriculture", "agribusiness", "farm", "farming",
        ])
        if not (has_crop or has_context or has_ag_input):
            continue

        metrics.signals_fetched += 1

        signals.append(CompanySignal(
            id=_make_id(link, title),
            title=title,
            url=link,
            company_name=company.name,
            company_type=company.company_type,
            source_name=source_name,
            published_at=pub_date,
            original_language=_detect_language(title),
        ))

    return signals


async def fetch_all_signals(
    companies: list[CompanyConfig],
    metrics: RunMetrics,
) -> list[CompanySignal]:
    """Fetch signals for all tracked companies concurrently."""
    active = [c for c in companies if c.enabled]
    logger.info(f"Fetching signals for {len(active)} companies")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_company_signals(c, session, metrics) for c in active]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    signals = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            company_name = active[i].name if i < len(active) else "unknown"
            logger.error(f"Error fetching signals for {company_name}: {result}")
        elif isinstance(result, list):
            signals.extend(result)

    logger.info(f"Fetched {len(signals)} raw signals for {len(active)} companies")
    return signals
