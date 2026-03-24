"""
Eurostat agriculture data monitor.

Checks the Eurostat Catalogue API for recently updated agriculture datasets
and generates Publication objects for the newsletter. This covers all 27 EU
member states + EFTA/candidate countries via a single integration.

Two data paths:
1. Catalogue API — monitors dataset updates for agriculture-relevant codes
2. RSS feed — already handled by the standard publication fetcher in fetcher.py

Eurostat API docs:
  https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access
"""

import asyncio
import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from models import Publication, GeoLocation

logger = logging.getLogger(__name__)

# ── Eurostat agriculture dataset codes ────────────────────────────
# Curated list of dataset code prefixes relevant to crop production,
# land use, and agricultural inputs. Eurostat has ~200 agriculture
# datasets; we track the high-value ones for the newsletter audience.
#
# Full catalogue: https://ec.europa.eu/eurostat/web/agriculture/database

AGRICULTURE_DATASET_PREFIXES = [
    # Crop production
    "apro_cpsh",    # Crop production in EU standard humidity
    "apro_cpnh",    # Crop production in national humidity
    "apro_cpb",     # Crop production — cereals
    "apro_cps",     # Crop statistics (various)
    "apro_cp",      # Catch-all for other crop production datasets
    # Area & land use
    "apro_acs",     # Arable crops — areas and production
    "ef_lus",       # Farm structure — land use
    "ef_ls",        # Farm structure — livestock (contextual)
    "ef_mp",        # Farm structure — machinery/equipment
    "lan_use",      # Land use overview
    # Oilseeds, pulses, roots
    "apro_cpoi",    # Oilseed crops
    "apro_cppul",   # Pulses
    "apro_cprt",    # Root crops
    # Milk (contextual for dairy crop demand)
    "apro_mk",      # Milk and dairy production
    # Agri prices and inputs
    "apri_pi",      # Agricultural price indices — input
    "apri_po",      # Agricultural price indices — output
    "aact_eaa",     # Economic accounts for agriculture
    "aact_ali",     # Agricultural labour input
    # Organic farming
    "org_cropar",   # Organic crop area
    "org_cropro",   # Organic crop production
    # TAG = agriculture indicator shortcodes (Eurostat uses these for summary tables)
    "tag00",        # Agriculture indicators (TAG00xxx codes)
]

# Human-readable labels for dataset codes (used in publication titles).
# We map the most common ones; unknown codes fall back to the raw code.
DATASET_LABELS = {
    "apro_cpsh1":  "Crop production (EU standard humidity)",
    "apro_cpnh1":  "Crop production (national humidity)",
    "apro_cpshr":  "Crop production by NUTS 2 region (EU humidity)",
    "apro_cpnhr":  "Crop production by NUTS 2 region (national humidity)",
    "apro_cpb1":   "Cereal crop production",
    "apro_acs_a":  "Arable crop areas",
    "apro_acs_p":  "Arable crop production",
    "apro_cpoi":   "Oilseed crop production",
    "apro_cppul":  "Pulse crop production",
    "apro_cprt":   "Root crop production",
    "apro_mk_colm": "Cows' milk collection",
    "ef_lus":      "Farm structure — land use",
    "ef_mp":       "Farm structure — machinery/equipment",
    "apri_pi":     "Agricultural price indices (input costs)",
    "apri_po":     "Agricultural price indices (output prices)",
    "aact_eaa":    "Economic accounts for agriculture",
    "aact_ali":    "Agricultural labour input",
    "org_cropar":  "Organic farming — crop area",
    "org_cropro":  "Organic farming — crop production",
    # TAG = agriculture indicator shortcodes
    "tag00054":    "Agricultural output — crop production value",
    "tag00055":    "Agricultural output — animal production value",
    "tag00056":    "Gross value added of agriculture",
    "tag00102":    "Agricultural output value",
}

# ── API URLs ──────────────────────────────────────────────────────

# RSS feed: all dataset updates in last 7 days (XML)
EUROSTAT_RSS_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/catalogue/rss/en/statistics-update.rss"
)

# Data browser link template (for publication URLs)
EUROSTAT_DATABROWSER_URL = (
    "https://ec.europa.eu/eurostat/databrowser/product/view/{dataset_code}"
)

# ── Headers ───────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "HyperplanAgriDigest/2.0 (+https://hyperplan.fr; remi@hyperplan.fr)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def _make_id(text: str) -> str:
    """Generate a short hash ID from text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_agriculture_dataset(code: str) -> bool:
    """Check if a dataset code matches our agriculture prefix list."""
    code_lower = code.lower().strip()
    return any(code_lower.startswith(prefix.lower()) for prefix in AGRICULTURE_DATASET_PREFIXES)


def _dataset_label(code: str) -> str:
    """Get human-readable label for a dataset code."""
    return DATASET_LABELS.get(code.lower().strip(), code)


def _dataset_url(code: str) -> str:
    """Build a data browser URL for a dataset code."""
    return EUROSTAT_DATABROWSER_URL.format(dataset_code=code.strip())


# ── RSS-based approach ────────────────────────────────────────────
# Parse the Eurostat statistics-update RSS feed and extract entries
# that correspond to agriculture dataset updates. This is more
# reliable than keyword matching on titles because we check the
# actual dataset code embedded in the link or description.

async def _fetch_eurostat_rss(
    session: aiohttp.ClientSession,
    timeout: int = 30,
) -> Optional[str]:
    """Fetch the raw RSS XML from Eurostat catalogue API."""
    try:
        async with session.get(
            EUROSTAT_RSS_URL,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.warning(f"Eurostat RSS returned status {resp.status}")
                return None
    except Exception as e:
        logger.warning(f"Failed to fetch Eurostat RSS: {e}")
        return None


def _parse_rss_for_agriculture(
    xml_text: str,
    cutoff: datetime,
) -> list[dict]:
    """
    Parse the Eurostat RSS feed XML and extract agriculture-relevant entries.

    Returns a list of dicts with: title, link, pub_date, dataset_code.
    """
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"Failed to parse Eurostat RSS XML: {e}")
        return results

    # RSS 2.0 structure: <rss><channel><item>...</item></channel></rss>
    channel = root.find("channel")
    if channel is None:
        logger.warning("No <channel> element in Eurostat RSS")
        return results

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_str = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()

        if not title or not link:
            continue

        # Parse publication date (RFC 822 format typical for RSS)
        pub_date = _parse_rss_date(pub_date_str)
        if pub_date and pub_date < cutoff:
            continue

        # Extract dataset code from the link or title.
        # Eurostat RSS links often look like:
        #   https://ec.europa.eu/eurostat/databrowser/product/view/apro_cpsh1
        # Or the title/description may contain the code in brackets: [APRO_CPSH1]
        dataset_code = _extract_dataset_code(link, title, description)

        if dataset_code and _is_agriculture_dataset(dataset_code):
            results.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "dataset_code": dataset_code,
                "description": description,
            })

    logger.info(
        f"Eurostat RSS: parsed {len(channel.findall('item'))} items, "
        f"{len(results)} agriculture-relevant"
    )
    return results


def _extract_dataset_code(link: str, title: str, description: str) -> Optional[str]:
    """
    Extract a Eurostat dataset code from the RSS item.

    Real RSS format (observed 2026-03-24):
      title: 'TAG00056 - "Dataset: updated data"'
      link:  'https://ec.europa.eu/eurostat/databrowser/product/page/TAG00056'

    The code is always the first token in the title (before " - ").
    It's also the last path segment in the link URL.
    Codes can be: APRO_CPSH1, TAG00056, EF_LUS_MAIN, AACT_EAA01, etc.
    """
    import re

    # Strategy 1 (most reliable): extract code from title before " - "
    # Example: "TAG00056 - \"Dataset: updated data\"" → TAG00056
    title_match = re.match(r'^([A-Za-z0-9_]+)\s*-\s*', title)
    if title_match:
        return title_match.group(1).lower()

    # Strategy 2: last non-empty path segment of the link URL
    # Example: .../product/page/TAG00056 → TAG00056
    if link:
        segments = [s.strip() for s in link.rstrip("/").split("/") if s.strip()]
        if segments:
            candidate = segments[-1].lower()
            # Sanity check: looks like an alphanumeric code (2+ chars)
            if re.match(r'^[a-z][a-z0-9_]{1,30}$', candidate):
                return candidate

    # Strategy 3: bracketed code in title or description
    bracket_match = re.search(r'\[([A-Za-z0-9_]{3,25})\]', f"{title} {description}")
    if bracket_match:
        return bracket_match.group(1).lower()

    return None


def _parse_rss_date(date_str: str) -> Optional[datetime]:
    """
    Parse date from Eurostat RSS pubDate field.

    Real format observed: "2026-03-24 15:39:00.0" (not standard RFC 822).
    Also handles RFC 822 and ISO formats as fallbacks.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # Eurostat's actual format: "2026-03-24 15:39:00.0"
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",   # Eurostat actual format
        "%Y-%m-%d %H:%M:%S",       # Without fractional seconds
        "%Y-%m-%dT%H:%M:%SZ",      # ISO with Z
        "%Y-%m-%dT%H:%M:%S%z",     # ISO with timezone
        "%Y-%m-%d",                 # Date only
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Fallback: RFC 2822 (standard RSS)
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass

    return None


# ── Main fetch function ───────────────────────────────────────────

async def fetch_eurostat_publications(
    lookback_hours: int = 48,
) -> list[Publication]:
    """
    Fetch recently updated Eurostat agriculture datasets and return
    them as Publication objects for the newsletter.

    This monitors the Eurostat Catalogue RSS feed for dataset updates
    matching agriculture-relevant codes. Each update becomes a
    Publication entry in the "Official Publications" section.

    Args:
        lookback_hours: How far back to look for updates (default 48h).

    Returns:
        List of Publication objects for agriculture dataset updates.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    publications = []

    async with aiohttp.ClientSession() as session:
        xml_text = await _fetch_eurostat_rss(session)

    if not xml_text:
        logger.warning("Eurostat: no RSS data retrieved, skipping")
        return publications

    ag_entries = _parse_rss_for_agriculture(xml_text, cutoff)

    # Deduplicate by dataset code (same dataset can appear multiple times
    # in RSS if updated more than once in the window)
    seen_codes = set()
    for entry in ag_entries:
        code = entry["dataset_code"]
        if code in seen_codes:
            continue
        seen_codes.add(code)

        label = _dataset_label(code)
        title = f"Eurostat: {label} — data updated"
        url = entry["link"] or _dataset_url(code)

        publications.append(Publication(
            id=_make_id(f"eurostat-{code}-{entry['pub_date'].date() if entry['pub_date'] else 'unknown'}"),
            title=title,
            url=url,
            source_name="Eurostat",
            country="Europe",
            flag_emoji="🇪🇺",
            published_at=entry["pub_date"],
            original_language="en",
            summary=(
                f"Eurostat updated the '{label}' dataset ({code.upper()}) "
                f"covering EU member states. "
                f"View in data browser: {_dataset_url(code)}"
            ),
            relevant=True,
            location=GeoLocation(
                place_name="European Union",
                country_iso="EU",
                latitude=50.85,   # Brussels (EU institutions centroid)
                longitude=4.35,
            ),
        ))

    logger.info(f"Eurostat: {len(publications)} agriculture dataset updates found")
    return publications
