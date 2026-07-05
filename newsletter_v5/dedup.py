"""
Deduplication: tracks sent articles, publications, and signals via JSON files.
JSON files are committed back to the repo for persistence across runs.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from models import Article, CompanySignal, Publication
from constants import (
    DEDUP_ARTICLES_FILE, DEDUP_PUBLICATIONS_FILE, DEDUP_SIGNALS_FILE,
    SIGNAL_LOOKBACK_DAYS, ARTICLE_LOOKBACK_HOURS,
)

logger = logging.getLogger(__name__)


def make_id(url: str, title: str = "") -> str:
    """Generate a stable ID from URL + title.

    Including the title prevents SPA-style scrapers (Agreste, MAPA Avances)
    from collapsing distinct publications onto a single dedup key when
    they share the same base URL.
    """
    key = f"{url.strip()}|{title.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class DedupStore:
    """JSON-backed dedup store. Each entry: {id: iso_timestamp}."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data: dict[str, str] = {}
        self._load()

    def _load(self):
        path = Path(self.filepath)
        if path.exists():
            try:
                with open(path, "r") as f:
                    self.data = json.load(f)
                logger.info(f"Loaded {len(self.data)} entries from {self.filepath}")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load {self.filepath}: {e}. Starting fresh.")
                self.data = {}
        else:
            self.data = {}

    def save(self):
        """Atomic write: temp file in the same directory, then rename."""
        path = Path(self.filepath)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp_path, self.filepath)

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.data

    def mark_seen(self, item_id: str):
        self.data[item_id] = datetime.now(timezone.utc).isoformat()

    def prune(self, max_age_days: int = 30):
        """Remove entries older than max_age_days to prevent unbounded growth."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        before = len(self.data)
        self.data = {
            k: v for k, v in self.data.items()
            if datetime.fromisoformat(v) > cutoff
        }
        pruned = before - len(self.data)
        if pruned > 0:
            logger.info(f"Pruned {pruned} old entries from {self.filepath}")


class DeduplicationManager:
    """Manages dedup for articles, publications, and signals."""

    def __init__(self, base_dir: str = "."):
        self.articles = DedupStore(str(Path(base_dir) / DEDUP_ARTICLES_FILE))
        self.publications = DedupStore(str(Path(base_dir) / DEDUP_PUBLICATIONS_FILE))
        self.signals = DedupStore(str(Path(base_dir) / DEDUP_SIGNALS_FILE))

    def filter_new_articles(self, articles: list[Article]) -> tuple[list[Article], int]:
        """Return only unseen articles. Returns (new_articles, duplicate_count)."""
        new = []
        dupes = 0
        for article in articles:
            article.id = make_id(article.url, article.title)
            if self.articles.is_seen(article.id):
                dupes += 1
            else:
                new.append(article)
        return new, dupes

    def filter_new_publications(self, pubs: list[Publication]) -> tuple[list[Publication], int]:
        """Return only unseen publications."""
        new = []
        dupes = 0
        for pub in pubs:
            pub.id = make_id(pub.url, pub.title)
            if self.publications.is_seen(pub.id):
                dupes += 1
            else:
                new.append(pub)
        return new, dupes

    def filter_new_signals(self, signals: list[CompanySignal]) -> tuple[list[CompanySignal], int]:
        """Return only unseen signals."""
        new = []
        dupes = 0
        for signal in signals:
            signal.id = make_id(signal.url, signal.title)
            if self.signals.is_seen(signal.id):
                dupes += 1
            else:
                new.append(signal)
        return new, dupes

    def mark_sent(self, articles: list[Article], pubs: list[Publication], signals: list[CompanySignal]):
        """Mark all items as sent and save."""
        for a in articles:
            self.articles.mark_seen(a.id)
        for p in pubs:
            self.publications.mark_seen(p.id)
        for s in signals:
            self.signals.mark_seen(s.id)

        # Prune old entries
        self.articles.prune(max_age_days=30)
        self.publications.prune(max_age_days=30)
        self.signals.prune(max_age_days=30)

        # Save
        self.articles.save()
        self.publications.save()
        self.signals.save()

    def mark_processed(self, articles: list[Article], pubs: list[Publication], signals: list[CompanySignal]):
        """Mark every successfully classified item as seen, relevant or not.

        This is the single biggest cost fix in the pipeline: before it, only
        SENT items were marked, so every REJECTED item came back through the
        classifier on later runs for as long as it stayed inside its source's
        lookback window (up to 7 days for signals and publications). Items
        whose classification call failed keep classified=False and are NOT
        marked, so they get retried on the next run.

        Call this together with mark_sent(); saving happens there.
        """
        skipped = 0
        for a in articles:
            if a.classified:
                self.articles.mark_seen(a.id)
            else:
                skipped += 1
        for p in pubs:
            if p.classified:
                self.publications.mark_seen(p.id)
            else:
                skipped += 1
        for s in signals:
            if s.classified:
                self.signals.mark_seen(s.id)
            else:
                skipped += 1
        if skipped:
            logger.info(f"Dedup: {skipped} unclassified item(s) left unmarked for retry next run")
