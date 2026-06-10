"""
Source health tracker — surfaces sources that have been silent for too long,
with per-source cadence thresholds.

v2 (2026-06-10):
- Atomic save (temp file + os.replace) so a crash mid-write can't corrupt state.
- Tracks BOTH the post-filter count (existing `streak`) and the raw fetch
  count (`fetch_streak`). A source with raw entries but zero passed items is
  QUIET (keyword/date filtering); a source with zero raw entries is DEAD
  (fetch failing). The June 2026 audit showed the single counter conflated
  the two and hid 9 healthy-but-blocked feeds for a month.
- `silent_sources()` accepts per-source thresholds (from the Notion
  "Max Silent Days" property) so monthly stat offices aren't flagged at the
  same horizon as daily media feeds.
- `prune()` drops records for sources no longer configured.

Storage: `source_health.json`, committed back to the repo. Schema per source:

    {"streak": 5, "fetch_streak": 0, "last_seen": "...", "last_count": 0,
     "last_raw_count": 41, "last_run": "..."}

Usage:

    tracker = SourceHealthTracker(base_dir)
    tracker.record(name, passed_count, raw_count=raw)   # once per source per run
    silent = tracker.silent_sources(thresholds={"MAPA Spain": 14, ...})
    tracker.prune(active_names={s.name for s in config.sources if s.enabled})
    tracker.save()
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "source_health.json"
SILENT_THRESHOLD = 3  # fallback when no per-source threshold is provided

# Category defaults (in runs; the cron is daily so runs ≈ days) applied by the
# orchestrator when a source has no explicit "Max Silent Days" in Notion.
DEFAULT_THRESHOLD_MEDIA = 3
DEFAULT_THRESHOLD_OFFICIAL = 21


class SourceHealthTracker:
    """Tracks per-source zero-entry streaks across runs."""

    def __init__(self, base_dir: str = ".", filename: str = DEFAULT_FILENAME):
        self.path = Path(base_dir) / filename
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    self.data = json.load(f)
                logger.info(
                    f"Source health: loaded {len(self.data)} source records "
                    f"from {self.path}"
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(
                    f"Source health: failed to load {self.path}: {e}. Starting fresh."
                )
                self.data = {}

    def save(self):
        """Atomic write: dump to a temp file in the same directory, then rename."""
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)
        except OSError as e:
            logger.warning(f"Source health: failed to save {self.path}: {e}")

    def record(self, source_name: str, count: int, raw_count: int | None = None):
        """Record one source's output for this run.

        count      — items that passed date + keyword filtering (existing metric)
        raw_count  — entries the fetch itself returned, before any filtering.
                     None when the caller doesn't track it (scrapers, Eurostat).
        """
        now = datetime.now(timezone.utc).isoformat()
        rec = self.data.get(source_name, {"streak": 0, "last_seen": None})
        rec["last_count"] = count
        rec["last_run"] = now
        if count > 0:
            rec["streak"] = 0
            rec["last_seen"] = now
        else:
            rec["streak"] = rec.get("streak", 0) + 1

        if raw_count is not None:
            rec["last_raw_count"] = raw_count
            if raw_count > 0:
                rec["fetch_streak"] = 0
            else:
                rec["fetch_streak"] = rec.get("fetch_streak", 0) + 1
        self.data[source_name] = rec

    def prune(self, active_names: set[str]):
        """Drop records for sources that are no longer configured/enabled."""
        stale = [n for n in self.data if n not in active_names]
        for n in stale:
            del self.data[n]
        if stale:
            logger.info(f"Source health: pruned {len(stale)} stale records: {stale}")

    def silent_sources(
        self,
        min_streak: int = SILENT_THRESHOLD,
        thresholds: dict[str, int] | None = None,
    ) -> list[dict]:
        """Return sources silent past their cadence threshold.

        thresholds maps source name → max acceptable silent runs. Sources
        without an entry fall back to min_streak. Each result carries a
        `status` field: DEAD (fetch returns nothing — broken URL/block) or
        QUIET (fetch works, nothing passes filters — cadence or keywords).
        """
        out = []
        for name, rec in self.data.items():
            limit = (thresholds or {}).get(name, min_streak)
            streak = rec.get("streak", 0)
            if streak >= limit:
                fetch_streak = rec.get("fetch_streak")
                if fetch_streak is None:
                    status = "SILENT"  # no raw data tracked (scrapers)
                elif fetch_streak >= limit:
                    status = "DEAD"
                else:
                    status = "QUIET"
                out.append({
                    "name": name,
                    "streak": streak,
                    "threshold": limit,
                    "fetch_streak": fetch_streak,
                    "status": status,
                    "last_seen": rec.get("last_seen"),
                    "last_run": rec.get("last_run"),
                })
        # DEAD first, then longest streak
        out.sort(key=lambda r: (r["status"] != "DEAD", -r["streak"]))
        return out
