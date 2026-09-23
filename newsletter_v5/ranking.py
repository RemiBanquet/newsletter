"""
Selection layer for the v6 digest.

Everything the classifier accepted is still archived to Notion. This module
only decides what goes INTO THE EMAIL:

- select_top_stories: cluster articles on the same story, cap per source,
  keep the most important N.
- select_radar: collapse duplicate signals, keep score >= RADAR_MIN_SCORE,
  show at most RADAR_MAX.
- select_psd_movers: on a USDA PSD release day only, the biggest year-on-year
  production moves.

Plain functions, plain dataclasses, no side effects: easy to unit test.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from models import Article, CompanySignal, CompanyType

# Display label per article category (email eyebrow).
THEME_LABELS = {
    "crop_production": "Production",
    "crop_land_use": "Acreage",
    "yields": "Yields",
    "agtech": "Agtech",
    "climate_weather": "Weather",
    "markets": "Markets",
    "regulation": "Regulation",
    "company_news": "Company news",
    "other": "Other",
}

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def flag_emoji(country_iso: str) -> str:
    """ISO alpha-2 -> flag emoji ('FR' -> 🇫🇷). Empty string if unusable."""
    code = (country_iso or "").strip().upper()
    if code == "EU":
        return "🇪🇺"
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


def _norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _when(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _EPOCH
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Top stories ───────────────────────────────────────────────────

@dataclass
class Story:
    title: str
    url: str
    summary: str
    source_name: str
    theme: str
    flag: str
    importance: int
    cluster_size: int


def select_top_stories(
    articles: list[Article], max_n: int = 12, per_source: int = 2
) -> list[Story]:
    """Rank accepted articles into at most max_n stories.

    1. Cluster by story_key (fallback: normalised title), so 11 regional
       harvest reports become 1 story "+10 similar".
    2. A cluster's representative is its most important, then newest item.
    3. Clusters sort by importance, then cluster size, then recency.
    4. At most per_source stories come from one outlet.
    """
    clusters: dict[str, list[Article]] = {}
    for a in articles:
        key = (a.story_key or "").strip() or "title:" + _norm_title(a.title)
        clusters.setdefault(key, []).append(a)

    ranked = []
    for items in clusters.values():
        items.sort(key=lambda a: (a.importance, _when(a.published_at)), reverse=True)
        rep = items[0]
        ranked.append((rep.importance, len(items), _when(rep.published_at), rep, len(items)))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)

    out: list[Story] = []
    per_source_count: dict[str, int] = {}
    for importance, _, _, rep, size in ranked:
        src = rep.source_name or "?"
        if per_source_count.get(src, 0) >= per_source:
            continue
        per_source_count[src] = per_source_count.get(src, 0) + 1
        out.append(Story(
            title=rep.title,
            url=rep.url,
            summary=rep.summary,
            source_name=rep.source_name,
            theme=THEME_LABELS.get(rep.category.value, "Other"),
            flag=flag_emoji(rep.location.country_iso),
            importance=importance,
            cluster_size=size,
        ))
        if len(out) >= max_n:
            break
    return out


# ── Account radar ─────────────────────────────────────────────────

@dataclass
class RadarItem:
    company_name: str
    company_type: str        # "client" or "prospect"
    signal_label: str
    summary: str
    url: str
    angle: str
    sources: int
    score: int


def select_radar(
    signals: list[CompanySignal],
    company_priority: dict[str, str],
    max_n: int = 5,
    min_score: int = 4,
) -> tuple[list[RadarItem], int]:
    """Collapse duplicate signals and keep the best max_n.

    Returns (shown items, number of other distinct signals not shown).
    Duplicates = same company + same story_key (fallback: normalised title),
    so the OCP-Brazil deal posted 7 times shows once with "7 sources".
    """
    groups: dict[tuple[str, str], list[CompanySignal]] = {}
    for s in signals:
        key = (s.company_name, (s.story_key or "").strip() or "title:" + _norm_title(s.title))
        groups.setdefault(key, []).append(s)

    candidates = []
    for items in groups.values():
        items.sort(key=lambda s: (s.score, _when(s.published_at)), reverse=True)
        rep = items[0]
        candidates.append((rep, len(items)))

    eligible = [
        (rep, n) for rep, n in candidates
        if rep.score >= min_score and rep.company_is_actor
    ]
    eligible.sort(key=lambda c: (
        -c[0].score,
        PRIORITY_RANK.get(company_priority.get(c[0].company_name, "medium"), 1),
        -_when(c[0].published_at).timestamp(),
    ))

    shown = [
        RadarItem(
            company_name=rep.company_name,
            company_type="client" if rep.company_type == CompanyType.CLIENT else "prospect",
            signal_label=(rep.signal_type.value.title() if rep.signal_type else "News"),
            summary=rep.summary or rep.title,
            url=rep.url,
            angle=rep.angle,
            sources=n,
            score=rep.score,
        )
        for rep, n in eligible[:max_n]
    ]
    return shown, max(0, len(candidates) - len(shown))


# ── USDA PSD movers ───────────────────────────────────────────────

@dataclass
class PsdMover:
    flag: str
    country: str
    crop: str
    metric: str
    yoy_pct: float


def select_psd_movers(
    psd_data: dict, max_n: int = 5, min_value: float = 1000.0
) -> list[PsdMover]:
    """Biggest year-on-year production changes, only on a new PSD release.

    min_value is in 1,000 MT (1,000 = 1 Mt) so tiny producers with huge
    percentage swings don't crowd out the moves that matter.
    """
    if not psd_data or not psd_data.get("available") or not psd_data.get("is_new_update"):
        return []
    matrix = psd_data.get("production_all") or {}
    movers = []
    for row in matrix.get("rows", []):
        for crop, cell in (row.get("values") or {}).items():
            value, yoy = (cell or {}).get("value"), (cell or {}).get("yoy_pct")
            if value is None or yoy is None or value < min_value:
                continue
            movers.append(PsdMover(
                flag=row.get("flag", ""), country=row.get("name", ""),
                crop=crop, metric="production", yoy_pct=float(yoy),
            ))
    movers.sort(key=lambda m: abs(m.yoy_pct), reverse=True)
    return movers[:max_n]
