"""
USDA FAS PSD data fetcher for the newsletter.

Downloads global crop production data from USDA FAS PSD Online (no API key required).
Extracts the latest estimates for all target countries, computes YoY changes,
and outputs two matrix tables (Area Harvested + Production) for the newsletter.

Data updates monthly (~10th-12th), coinciding with the WASDE report.
The module caches downloaded data locally to avoid re-downloading within the same day.

CONFIGURING CROPS:
    Edit DISPLAY_CROPS to change which crops appear in the newsletter.
    All commodities in COMMODITIES dict are available — just add their names.
"""

import json
import logging
import calendar
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from zipfile import ZipFile

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── PSD download URLs ─────────────────────────────────────────────

PSD_URLS = {
    "grains": "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip",
    "oilseeds": "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip",
    "sugar": "https://apps.fas.usda.gov/psdonline/downloads/psd_sugar_csv.zip",
    "cotton": "https://apps.fas.usda.gov/psdonline/downloads/psd_cotton_csv.zip",
    "eu_disagg": "https://apps.fas.usda.gov/psdonline/downloads/psd_eu_area_prod_csv.zip",
}

# ── All available commodities ─────────────────────────────────────

COMMODITIES = {
    "Corn": {"code": 440000, "dataset": "grains"},
    "Wheat": {"code": 410000, "dataset": "grains"},
    "Soybeans": {"code": 2222000, "dataset": "oilseeds"},
    "Barley": {"code": 430000, "dataset": "grains"},
    "Rapeseed": {"code": 2226000, "dataset": "oilseeds"},
    "Sunflower": {"code": 2224000, "dataset": "oilseeds"},
    "Rice": {"code": 422110, "dataset": "grains"},
    "Sorghum": {"code": 459200, "dataset": "grains"},
    "Cotton": {"code": 2631000, "dataset": "cotton"},
    "Sugar": {"code": 612000, "dataset": "sugar"},
}

# ── CROPS DISPLAYED IN NEWSLETTER (edit here to add/remove) ──────
# All six crops in display order (unified table — no cereals/oilseeds split).
DISPLAY_CROPS_ALL = ["Wheat", "Corn", "Barley", "Soybeans", "Rapeseed", "Sunflower"]

# Legacy split (kept for reference / backward compatibility)
DISPLAY_CROPS_CEREALS = ["Wheat", "Corn", "Barley"]
DISPLAY_CROPS_OILSEEDS = ["Soybeans", "Rapeseed", "Sunflower"]

# ── Country mapping: newsletter name → PSD country code ──────────
# PSD uses non-standard 2-letter codes. This maps all 36 target countries
# plus EU aggregate. Countries appear in the order listed here.

COUNTRIES = [
    # North America
    {"name": "United States", "psd": "US", "flag": "🇺🇸", "region": "North America"},
    {"name": "Canada", "psd": "CA", "flag": "🇨🇦", "region": "North America"},
    {"name": "Mexico", "psd": "MX", "flag": "🇲🇽", "region": "North America"},
    # South America
    {"name": "Brazil", "psd": "BR", "flag": "🇧🇷", "region": "South America"},
    {"name": "Argentina", "psd": "AR", "flag": "🇦🇷", "region": "South America"},
    # Europe (EU member states use eu_disagg dataset for area/production)
    {"name": "France", "psd": "FR", "flag": "🇫🇷", "region": "Europe", "eu_member": True},
    {"name": "Germany", "psd": "GM", "flag": "🇩🇪", "region": "Europe", "eu_member": True},
    {"name": "Spain", "psd": "SP", "flag": "🇪🇸", "region": "Europe", "eu_member": True},
    {"name": "Italy", "psd": "IT", "flag": "🇮🇹", "region": "Europe", "eu_member": True},
    {"name": "Romania", "psd": "RO", "flag": "🇷🇴", "region": "Europe", "eu_member": True},
    {"name": "Hungary", "psd": "HU", "flag": "🇭🇺", "region": "Europe", "eu_member": True},
    {"name": "Poland", "psd": "PL", "flag": "🇵🇱", "region": "Europe", "eu_member": True},
    {"name": "Bulgaria", "psd": "BU", "flag": "🇧🇬", "region": "Europe", "eu_member": True},
    {"name": "Czechia", "psd": "EZ", "flag": "🇨🇿", "region": "Europe", "eu_member": True},
    {"name": "Denmark", "psd": "DA", "flag": "🇩🇰", "region": "Europe", "eu_member": True},
    {"name": "Finland", "psd": "FI", "flag": "🇫🇮", "region": "Europe", "eu_member": True},
    {"name": "Sweden", "psd": "SW", "flag": "🇸🇪", "region": "Europe", "eu_member": True},
    {"name": "Austria", "psd": "AU", "flag": "🇦🇹", "region": "Europe", "eu_member": True},
    {"name": "Belgium", "psd": "BE", "flag": "🇧🇪", "region": "Europe", "eu_member": True},
    {"name": "Croatia", "psd": "HR", "flag": "🇭🇷", "region": "Europe", "eu_member": True},
    {"name": "Estonia", "psd": "EN", "flag": "🇪🇪", "region": "Europe", "eu_member": True},
    {"name": "Ireland", "psd": "EI", "flag": "🇮🇪", "region": "Europe", "eu_member": True},
    {"name": "Latvia", "psd": "LG", "flag": "🇱🇻", "region": "Europe", "eu_member": True},
    {"name": "Lithuania", "psd": "LH", "flag": "🇱🇹", "region": "Europe", "eu_member": True},
    {"name": "Netherlands", "psd": "NL", "flag": "🇳🇱", "region": "Europe", "eu_member": True},
    {"name": "Portugal", "psd": "PO", "flag": "🇵🇹", "region": "Europe", "eu_member": True},
    {"name": "Slovakia", "psd": "LO", "flag": "🇸🇰", "region": "Europe", "eu_member": True},
    {"name": "UK", "psd": "UK", "flag": "🇬🇧", "region": "Europe"},
    {"name": "Russia", "psd": "RS", "flag": "🇷🇺", "region": "Europe"},
    {"name": "Ukraine", "psd": "UP", "flag": "🇺🇦", "region": "Europe"},
    {"name": "Turkey", "psd": "TU", "flag": "🇹🇷", "region": "Europe"},
    # Africa
    {"name": "South Africa", "psd": "SF", "flag": "🇿🇦", "region": "Africa"},
    {"name": "Morocco", "psd": "MO", "flag": "🇲🇦", "region": "Africa"},
    {"name": "Egypt", "psd": "EG", "flag": "🇪🇬", "region": "Africa"},
    # Asia-Pacific
    {"name": "China", "psd": "CH", "flag": "🇨🇳", "region": "Asia-Pacific"},
    {"name": "India", "psd": "IN", "flag": "🇮🇳", "region": "Asia-Pacific"},
    {"name": "Indonesia", "psd": "ID", "flag": "🇮🇩", "region": "Asia-Pacific"},
    {"name": "Australia", "psd": "AS", "flag": "🇦🇺", "region": "Asia-Pacific"},
    {"name": "New Zealand", "psd": "NZ", "flag": "🇳🇿", "region": "Asia-Pacific"},
]

# Ordered list of regions for display (used by template to group rows)
REGION_ORDER = ["North America", "South America", "Europe", "Africa", "Asia-Pacific"]

# ── Cache management ──────────────────────────────────────────────

CACHE_DIR = Path(__file__).parent / ".psd_cache"


def _cache_path(dataset: str) -> Path:
    return CACHE_DIR / f"psd_{dataset}.csv"


def _cache_meta_path() -> Path:
    return CACHE_DIR / "cache_meta.json"


def _is_cache_fresh() -> bool:
    meta_path = _cache_meta_path()
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
        return meta.get("date", "") == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return False


def _save_cache_meta(psd_month: int, psd_year: int):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "psd_month": psd_month,
        "psd_year": psd_year,
    }
    _cache_meta_path().write_text(json.dumps(meta))


def _get_cached_psd_month() -> Optional[int]:
    meta_path = CACHE_DIR / "last_psd_month.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text()).get("month")
    except Exception:
        return None


def _save_last_psd_month(month: int, year: int):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "last_psd_month.json").write_text(json.dumps({"month": month, "year": year}))


# ── Data download & parsing ───────────────────────────────────────

# Cache trimming: the full PSD CSVs are 40-65 MB (history to 1960, all
# attributes, all commodities). The newsletter needs only the configured
# commodities and recent years. Trimming the cached copy to ~1-2 MB makes
# it small enough to commit to the repo, which is what gives the
# stale-cache fallback something to fall back on in CI.
_CACHE_KEEP_CODES = {c["code"] for c in COMMODITIES.values()}


def _trim_for_cache(df: pd.DataFrame) -> pd.DataFrame:
    try:
        out = df
        if "Market_Year" in out.columns:
            out = out[out["Market_Year"] >= datetime.now(timezone.utc).year - 4]
        if "Commodity_Code" in out.columns:
            trimmed = out[out["Commodity_Code"].isin(_CACHE_KEEP_CODES)]
            if len(trimmed) > 0:  # eu_disagg codes may differ; never trim to zero
                out = trimmed
        return out
    except Exception:
        return df


def _download_dataset(dataset: str) -> pd.DataFrame:
    url = PSD_URLS[dataset]
    cache_path = _cache_path(dataset)

    if _is_cache_fresh() and cache_path.exists():
        logger.debug(f"Using cached PSD data for {dataset}")
        return pd.read_csv(cache_path)

    logger.info(f"Downloading PSD {dataset} data...")
    try:
        # (connect, read) timeout: fail fast on an unreachable host instead of
        # burning 2 minutes per dataset. On 2026-06-11 three 120s connect
        # timeouts to apps.fas.usda.gov ate 6 of 9.2 min of runtime and
        # dropped the crop snapshot from the edition entirely.
        resp = requests.get(url, timeout=(15, 90))
        resp.raise_for_status()
    except Exception as e:
        if cache_path.exists():
            logger.warning(
                f"PSD {dataset}: download failed ({e}); using stale cache "
                f"(PSD is monthly — days-old cache is acceptable)"
            )
            return pd.read_csv(cache_path)
        raise

    with ZipFile(BytesIO(resp.content)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _trim_for_cache(df)
    cached.to_csv(cache_path, index=False)
    logger.info(f"PSD {dataset}: {len(df)} rows ({len(cached)} cached)")
    return df


def _download_needed_datasets(crop_names: list[str]) -> dict[str, pd.DataFrame]:
    datasets_needed = set()
    for crop in crop_names:
        if crop in COMMODITIES:
            datasets_needed.add(COMMODITIES[crop]["dataset"])
    # Always need eu_disagg for EU member state breakdowns
    datasets_needed.add("eu_disagg")

    dfs = {}
    for ds in datasets_needed:
        try:
            dfs[ds] = _download_dataset(ds)
        except Exception as e:
            logger.error(f"Failed to download PSD {ds}: {e}")
    return dfs


# ── Data extraction ───────────────────────────────────────────────

def _get_metric(
    df: pd.DataFrame,
    commodity_code: int,
    country_code: str,
    attribute: str,
    market_year: int,
) -> Optional[float]:
    mask = (
        (df["Commodity_Code"] == commodity_code)
        & (df["Country_Code"] == country_code)
        & (df["Attribute_Description"] == attribute)
        & (df["Market_Year"] == market_year)
    )
    rows = df[mask]
    if rows.empty:
        return None
    latest = rows.loc[rows["Month"].idxmax()]
    return float(latest["Value"]) if pd.notna(latest["Value"]) else None


def _build_matrix(
    dfs: dict[str, pd.DataFrame],
    crop_names: list[str],
    metric: str,
    latest_year: int,
) -> dict:
    """Build a country × crop matrix for a given metric.

    Returns:
    {
        "crops": ["Wheat", "Corn", "Barley"],
        "rows": [
            {
                "name": "EU Total", "flag": "🇪🇺", "region": "Supranational",
                "values": {
                    "Wheat": {"value": 144000.0, "yoy_pct": 17.9},
                    "Corn": {"value": 56950.0, "yoy_pct": -3.5},
                    "Barley": {"value": None, "yoy_pct": None},
                }
            },
            ...
        ]
    }
    """
    rows = []
    for country in COUNTRIES:
        psd_code = country["psd"]
        is_eu = country.get("eu_member", False)
        row_data = {
            "name": country["name"],
            "flag": country["flag"],
            "region": country["region"],
            "values": {},
            "has_data": False,
        }

        for crop_name in crop_names:
            crop_info = COMMODITIES.get(crop_name)
            if not crop_info:
                row_data["values"][crop_name] = {"value": None, "yoy_pct": None}
                continue

            # EU members: use eu_disagg dataset for area/production
            if is_eu and metric in ("Production", "Area Harvested"):
                df = dfs.get("eu_disagg")
            else:
                df = dfs.get(crop_info["dataset"])

            if df is None:
                row_data["values"][crop_name] = {"value": None, "yoy_pct": None}
                continue

            value = _get_metric(df, crop_info["code"], psd_code, metric, latest_year)
            prev = _get_metric(df, crop_info["code"], psd_code, metric, latest_year - 1)

            yoy_pct = None
            if value is not None and prev is not None and prev > 0:
                yoy_pct = round((value - prev) / prev * 100, 1)

            row_data["values"][crop_name] = {"value": value, "yoy_pct": yoy_pct}
            if value is not None:
                row_data["has_data"] = True

        # Only include countries that have at least 1 data point
        if row_data["has_data"]:
            rows.append(row_data)

    return {"crops": crop_names, "rows": rows}


# ── Public API ────────────────────────────────────────────────────

def fetch_psd_data() -> dict:
    """Main entry point. Downloads PSD data and builds matrix tables.

    Returns:
    {
        "available": True/False,
        "psd_month": 3,
        "psd_year": 2025,
        "is_new_update": True/False,
        "area_cereals": { matrix },
        "area_oilseeds": { matrix },
        "production_cereals": { matrix },
        "production_oilseeds": { matrix },
    }
    """
    try:
        all_crops = DISPLAY_CROPS_CEREALS + DISPLAY_CROPS_OILSEEDS
        dfs = _download_needed_datasets(all_crops)
        if not dfs:
            logger.warning("No PSD datasets downloaded")
            return {"available": False}

        # Determine latest marketing year and month
        grains_df = dfs.get("grains")
        if grains_df is None:
            return {"available": False}

        latest_year = int(grains_df["Market_Year"].max())
        latest_month = int(
            grains_df[grains_df["Market_Year"] == latest_year]["Month"].max()
        )

        # Detect new update
        prev_month = _get_cached_psd_month()
        is_new_update = prev_month is not None and prev_month != latest_month
        _save_last_psd_month(latest_month, latest_year)
        _save_cache_meta(latest_month, latest_year)

        # Human-readable labels
        release_label = f"{calendar.month_abbr[latest_month]} {datetime.now(timezone.utc).year} release"
        my_label = f"Harvest {latest_year}"

        logger.info(
            f"PSD data: {my_label}, {release_label}"
            f"{' [NEW UPDATE]' if is_new_update else ''}"
        )

        # Build 2 unified matrices: all 6 crops side-by-side for area + production
        result = {
            "available": True,
            "psd_month": latest_month,
            "psd_year": latest_year,
            "release_label": release_label,
            "my_label": my_label,
            "is_new_update": is_new_update,
            "region_order": REGION_ORDER,
            "area_all": _build_matrix(dfs, DISPLAY_CROPS_ALL, "Area Harvested", latest_year),
            "production_all": _build_matrix(dfs, DISPLAY_CROPS_ALL, "Production", latest_year),
            # Legacy split (kept for backward compat; template uses area_all/production_all)
            "area_cereals": _build_matrix(dfs, DISPLAY_CROPS_CEREALS, "Area Harvested", latest_year),
            "area_oilseeds": _build_matrix(dfs, DISPLAY_CROPS_OILSEEDS, "Area Harvested", latest_year),
            "production_cereals": _build_matrix(dfs, DISPLAY_CROPS_CEREALS, "Production", latest_year),
            "production_oilseeds": _build_matrix(dfs, DISPLAY_CROPS_OILSEEDS, "Production", latest_year),
        }

        total_rows = len(result["area_all"].get("rows", []))
        logger.info(f"PSD matrices ready: {len(all_crops)} crops, {total_rows} country-rows")

        return result
    except Exception as e:
        logger.error(f"PSD data fetch failed (non-fatal): {e}")
        return {"available": False}
