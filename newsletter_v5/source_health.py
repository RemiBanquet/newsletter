"""
Source health tracker — surfaces sources that have been silent for N
consecutive runs.

Why: in the original v5 launch, 14 of 15 publication sources were
producing zero entries for 6 weeks before anyone noticed, because the
admin report didn't distinguish "fetched 0 because there genuinely was
no news" from "this source has been silent every single day for a
month." This module closes that gap.

Storage: a JSON file (`source_health.json`) committed back to the repo
alongside the dedup state files. Schema:

    {
        "MAPA Spain": {"streak": 5, "last_seen": "2026-04-12T07:31:00Z",
                       "last_count": 0, "last_run": "2026-05-05T07:31:00Z"},
        "Eurostat":   {"streak": 0, "last_seen": "2026-05-05T07:31:00Z",
                       "last_count": 4, "last_run": "2026-05-05T07:31:00Z"},
        ...
    }

Usage:

    tracker = SourceHealthTracker(base_dir)
    tracker.record(source_name, count_returned)  # call once per source per run
    silent = tracker.silent_sources(min_streak=3)  # for admin report
    tracker.save()
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "source_health.json"
SILENT_THRESHOLD = 3  # 3 consecutive zero-entry runs → flag in admin report


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
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
        except IOError as e:
            logger.warning(f"Source health: failed to save {self.path}: {e}")

    def record(self, source_name: str, count: int):
        """Record one source's output count for this run."""
        now = datetime.now(timezone.utc).isoformat()
        rec = self.data.get(source_name, {"streak": 0, "last_seen": None})
        rec["last_count"] = count
        rec["last_run"] = now
        if count > 0:
            rec["streak"] = 0
            rec["last_seen"] = now
        else:
            rec["streak"] = rec.get("streak", 0) + 1
        self.data[source_name] = rec

    def silent_sources(self, min_streak: int = SILENT_THRESHOLD) -> list[dict]:
        """Return sources that have been silent for ≥ min_streak runs."""
        out = []
        for name, rec in self.data.items():
            streak = rec.get("streak", 0)
            if streak >= min_streak:
                out.append({
                    "name": name,
                    "streak": streak,
                    "last_seen": rec.get("last_seen"),
                    "last_run": rec.get("last_run"),
                })
        # Sort: longest streak first
        out.sort(key=lambda r: r["streak"], reverse=True)
        return out
