"""
IBGE PAM — Produção Agrícola Municipal (Municipal Agricultural Production).

Monitors the IBGE SIDRA API for new PAM data releases and generates
Publication entries with headline crop numbers for Brazil.

PAM (Table 5457) is Brazil's definitive annual crop statistics survey
covering area planted, area harvested, quantity produced, average yield,
and production value for 64 agricultural products at the municipality level.

Release cadence: Annual (typically September/October for previous year's data)
- PAM 2024 released: September 2025
- PAM 2025 expected: September/October 2026

API: SIDRA (Sistema IBGE de Recuperação Automática)
- Base URL: https://apisidra.ibge.gov.br/values
- No authentication required
- Table 5457: temporary + permanent crops
- Docs: https://apisidra.ibge.gov.br/

SIDRA API URL format:
  https://apisidra.ibge.gov.br/values/t/{table}/n{geo_level}/{geo_code}/v/{variables}/p/{period}/c{classification}/{categories}

Parameters:
  t = table number (5457)
  n = geographic level: 1=Brazil, 2=Grandes Regiões, 3=UF/State, 6=Municipality
  v = variable codes (comma-separated)
  p = period: year (e.g., "2024"), "last" for latest, "last 2" for last 2
  c = classification with category codes

Variables for table 5457:
  216 = Área plantada ou destinada à colheita (ha)
  214 = Área colhida (ha)
  215 = Quantidade produzida (tonnes)
  112 = Rendimento médio da produção (kg/ha)
  214 = Valor da produção (R$ 1,000)

Crop classification (c782) — key crops for newsletter:
  40476 = Soja (em grão) — Soybean
  40477 = Milho (em grão) — Corn (grain)
  40478 = Trigo (em grão) — Wheat
  40475 = Arroz (em casca) — Rice (paddy)
  40480 = Algodão herbáceo (em caroço) — Cotton
  40479 = Cana-de-açúcar — Sugarcane
  40473 = Café (em grão) Total — Coffee
  40471 = Feijão (em grão) Total — Beans
  109 = Sorgo (em grão) — Sorghum
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from models import GeoLocation, Publication, SourceConfig
from sources.scraper_base import build_publication, make_id

logger = logging.getLogger(__name__)

# ── API config ────────────────────────────────────────────────────

SIDRA_BASE = "https://apisidra.ibge.gov.br/values"

# Table 5457: PAM — temporary + permanent crops
TABLE_ID = "5457"

# Variables we want
VARS = {
    "216": "Área plantada (ha)",       # Planted area
    "214": "Área colhida (ha)",        # Harvested area
    "215": "Quantidade produzida (t)",  # Quantity produced
    "112": "Rendimento médio (kg/ha)", # Average yield
}

# Key crops by classification c782 code
# These are the top field crops relevant to the newsletter audience
KEY_CROPS = {
    "40476": "Soybean",
    "40477": "Corn (grain)",
    "40478": "Wheat",
    "40475": "Rice (paddy)",
    "40480": "Cotton",
    "40479": "Sugarcane",
    "40473": "Coffee",
    "40471": "Beans",
}

# State-level cache file for tracking last known year
PAM_STATE_FILE = "pam_last_year.json"

IBGE_GEOLOCATION = GeoLocation(
    place_name="Brazil",
    country_iso="BR",
    latitude=-15.78,
    longitude=-47.93,
)

SIDRA_HEADERS = {
    "User-Agent": "HyperplanAgriDigest/2.0 (+https://hyperplan.fr; remi@hyperplan.fr)",
    "Accept": "application/json",
}

# Link to SIDRA table for the publication URL
PAM_TABLE_URL = "https://sidra.ibge.gov.br/tabela/5457"


# ── State tracking ────────────────────────────────────────────────

def _get_state_path(base_dir: str) -> str:
    """Get the path to the PAM state file."""
    return os.path.join(base_dir, PAM_STATE_FILE)


def _load_last_known_year(base_dir: str) -> Optional[int]:
    """Load the last known PAM data year from state file."""
    path = _get_state_path(base_dir)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("last_year")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_last_known_year(base_dir: str, year: int) -> None:
    """Save the latest PAM data year to state file."""
    path = _get_state_path(base_dir)
    with open(path, "w") as f:
        json.dump({"last_year": year, "updated_at": datetime.now(timezone.utc).isoformat()}, f)


# ── API calls ─────────────────────────────────────────────────────

def _build_latest_period_url() -> str:
    """
    Build a lightweight API URL to check what the latest available year is.
    Fetches just soybean planted area at Brazil level for the last period.
    This is a tiny request (~1 row) to detect new releases.

    NOTE: SIDRA returns XML by default. Append /f/a to get JSON array format.
    """
    return (
        f"{SIDRA_BASE}"
        f"/t/{TABLE_ID}"
        f"/n1/all"          # Brazil level
        f"/v/216"           # Planted area only
        f"/p/last"          # Latest available period
        f"/c782/40476"      # Soybean only (to keep response tiny)
        f"/f/a"             # JSON array format (default is XML!)
    )


def _build_headline_url(year: int) -> str:
    """
    Build API URL to fetch headline numbers for key crops at Brazil level.
    Gets planted area, harvested area, and production for the top 8 crops.
    """
    crop_codes = ",".join(KEY_CROPS.keys())
    var_codes = "216,214,215"  # Planted area, harvested area, production
    return (
        f"{SIDRA_BASE}"
        f"/t/{TABLE_ID}"
        f"/n1/all"          # Brazil level
        f"/v/{var_codes}"
        f"/p/{year}"
        f"/c782/{crop_codes}"
        f"/f/a"             # JSON array format
    )


async def _fetch_sidra_json(
    url: str,
    session: aiohttp.ClientSession,
    timeout: int = 30,
) -> Optional[list]:
    """Fetch JSON from the SIDRA API."""
    try:
        async with session.get(
            url,
            headers=SIDRA_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            else:
                logger.warning(f"SIDRA API returned status {resp.status}")
                return None
    except Exception as e:
        logger.warning(f"Failed to fetch SIDRA API: {e}")
        return None


def _extract_year_from_response(data: list) -> Optional[int]:
    """
    Extract the data year from a SIDRA API response.

    SIDRA JSON (/f/a) returns rows as dicts. The first row is a header.
    Actual field mapping for table 5457 (observed 2026-03-24):
      D1C/D1N = geography (1 = Brasil)
      D2C/D2N = variable (216 = Área colhida)
      D3C/D3N = year/period (2024)
      D4C/D4N = crop code (40476 = Soja)
      V       = value
    """
    if not data or len(data) < 2:
        return None

    # Skip header row (index 0), look at first data row
    for row in data[1:]:
        # Year is in D3C (period code) or D3N (period name)
        period = row.get("D3C", "") or row.get("D3N", "")
        try:
            year = int(str(period).strip())
            if 2000 <= year <= 2030:
                return year
        except (ValueError, TypeError):
            continue

    return None


def _format_headline_summary(data: list, year: int) -> str:
    """
    Format headline crop numbers into a readable summary.
    Example: "Soybean: 46.1M ha planted, 153.2M t | Corn: 21.5M ha, 115.7M t | ..."
    """
    if not data or len(data) < 2:
        return f"PAM {year} data released — detailed crop statistics now available."

    # Parse the response into a structured dict
    # SIDRA actual field mapping for table 5457:
    #   D1C/D1N = geography, D2C/D2N = variable, D3C/D3N = year,
    #   D4C/D4N = crop code/name, V = value
    crop_data = {}  # crop_name -> {var_name: value}

    for row in data[1:]:  # Skip header
        crop_code = str(row.get("D4C", "")).strip()
        crop_name = KEY_CROPS.get(crop_code, row.get("D4N", "Unknown"))
        var_code = str(row.get("D2C", "")).strip()  # Variable is in D2C
        value_str = str(row.get("V", "")).strip()

        if not value_str or value_str in ("-", "...", "X"):
            continue

        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue

        if crop_name not in crop_data:
            crop_data[crop_name] = {}

        if var_code == "216":
            crop_data[crop_name]["area_planted"] = value
        elif var_code == "215":
            crop_data[crop_name]["production"] = value

    # Build summary lines for top crops
    lines = []
    # Order by production (descending)
    sorted_crops = sorted(
        crop_data.items(),
        key=lambda x: x[1].get("production", 0),
        reverse=True,
    )

    for crop_name, values in sorted_crops[:6]:  # Top 6 crops
        parts = []
        area = values.get("area_planted")
        prod = values.get("production")

        if area:
            if area >= 1_000_000:
                parts.append(f"{area / 1_000_000:.1f}M ha planted")
            else:
                parts.append(f"{area / 1_000:.0f}K ha planted")

        if prod:
            if prod >= 1_000_000:
                parts.append(f"{prod / 1_000_000:.1f}M t produced")
            else:
                parts.append(f"{prod / 1_000:.0f}K t produced")

        if parts:
            lines.append(f"{crop_name}: {', '.join(parts)}")

    if lines:
        return f"IBGE PAM {year} — Brazil crop production highlights: " + " | ".join(lines)
    else:
        return f"IBGE PAM {year} data released — detailed crop statistics now available at SIDRA."


# ── Main fetch function ───────────────────────────────────────────

async def scrape_ibge_pam(
    source: SourceConfig,
    session: aiohttp.ClientSession,
) -> list[Publication]:
    """
    Check IBGE SIDRA for new PAM data releases.

    Strategy:
    1. Query the API for the latest available year (tiny request)
    2. Compare against last known year (from state file)
    3. If new year detected, fetch headline crop numbers
    4. Generate a Publication with the summary
    5. Update state file

    Returns empty list if no new data since last check.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Step 1: Check latest available year
    check_url = _build_latest_period_url()
    data = await _fetch_sidra_json(check_url, session)

    if not data:
        logger.info("IBGE PAM: could not reach SIDRA API, skipping")
        return []

    current_year = _extract_year_from_response(data)
    if not current_year:
        logger.warning("IBGE PAM: could not extract year from API response")
        return []

    logger.info(f"IBGE PAM: latest available year = {current_year}")

    # Step 2: Compare against last known year
    last_year = _load_last_known_year(base_dir)

    if last_year and current_year <= last_year:
        logger.info(f"IBGE PAM: no new data (last known: {last_year}, current: {current_year})")
        return []

    # Step 3: New data detected! Fetch headline numbers
    logger.info(f"IBGE PAM: NEW DATA DETECTED — PAM {current_year} (previous: {last_year})")

    headline_url = _build_headline_url(current_year)
    headline_data = await _fetch_sidra_json(headline_url, session, timeout=45)
    summary = _format_headline_summary(headline_data, current_year)

    # Step 4: Save new state
    _save_last_known_year(base_dir, current_year)

    # Step 5: Generate publication
    pub = build_publication(
        title=f"IBGE PAM {current_year}: Brazil Municipal Agricultural Production",
        url=PAM_TABLE_URL,
        source_name="IBGE",
        country="Brazil",
        flag_emoji="🇧🇷",
        published_at=datetime.now(timezone.utc),
        summary=summary,
        language="pt",
        location=IBGE_GEOLOCATION,
    )

    logger.info(f"IBGE PAM: publication generated — {summary[:100]}...")
    return [pub]
