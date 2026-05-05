"""
Configuration loader: reads pipeline config from Notion databases.
Falls back to cached YAML if Notion is unavailable.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from notion_client import Client as NotionClient

from models import (
    CompanyConfig, CompanyType, CountryConfig, PipelineConfig,
    RecipientConfig, RecipientGroup, SourceCategory, SourceConfig, SourceType,
)

logger = logging.getLogger(__name__)

YAML_CACHE_PATH = Path(__file__).parent / "config_cache.yaml"


def _get_text(props: dict, key: str) -> str:
    """Extract text from a Notion property."""
    prop = props.get(key, {})
    ptype = prop.get("type", "")
    if ptype == "title":
        return prop["title"][0]["plain_text"] if prop.get("title") else ""
    if ptype == "rich_text":
        return prop["rich_text"][0]["plain_text"] if prop.get("rich_text") else ""
    if ptype == "email":
        return prop.get("email", "") or ""
    if ptype == "url":
        return prop.get("url", "") or ""
    if ptype == "select":
        return prop["select"]["name"] if prop.get("select") else ""
    return ""


def _get_bool(props: dict, key: str) -> bool:
    """Extract checkbox value from a Notion property."""
    prop = props.get(key, {})
    return prop.get("checkbox", False)


def _get_multi_select(props: dict, key: str) -> list[str]:
    """Extract multi-select values from a Notion property."""
    prop = props.get(key, {})
    return [item["name"] for item in prop.get("multi_select", [])]


def _get_number(props: dict, key: str, default: int = 0) -> int:
    """Extract numeric value from a Notion number property; default if empty."""
    prop = props.get(key, {})
    val = prop.get("number")
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def load_config_from_notion(
    notion_token: str,
    sources_db_id: str,
    recipients_db_id: str,
    companies_db_id: str,
    countries_db_id: str,
) -> PipelineConfig:
    """Load full pipeline config from 4 Notion databases."""
    notion = NotionClient(auth=notion_token)
    config = PipelineConfig()

    # ── Sources ──
    try:
        results = _query_all_pages(notion, sources_db_id)
        for page in results:
            props = page["properties"]
            cat_str = _get_text(props, "Source Category").lower()
            type_str = _get_text(props, "Type").lower()
            config.sources.append(SourceConfig(
                name=_get_text(props, "Name"),
                url=_get_text(props, "URL"),
                category=SourceCategory(cat_str) if cat_str in [e.value for e in SourceCategory] else SourceCategory.MEDIA,
                source_type=SourceType(type_str) if type_str in [e.value for e in SourceType] else SourceType.RSS,
                country=_get_text(props, "Country"),
                enabled=_get_bool(props, "Enabled"),
                keywords_filter=_get_multi_select(props, "Keywords Filter"),
                scraper_id=_get_text(props, "Scraper ID"),
                lookback_hours=_get_number(props, "Lookback Hours", default=0),
                notion_id=page["id"],
            ))
        logger.info(f"Loaded {len(config.sources)} sources from Notion")
    except Exception as e:
        logger.error(f"Failed to load sources from Notion: {e}")

    # ── Recipients ──
    try:
        results = _query_all_pages(notion, recipients_db_id)
        for page in results:
            props = page["properties"]
            group_str = _get_text(props, "Group").lower()
            config.recipients.append(RecipientConfig(
                name=_get_text(props, "Name"),
                email=_get_text(props, "Email"),
                active=_get_bool(props, "Active"),
                group=RecipientGroup(group_str) if group_str in [e.value for e in RecipientGroup] else RecipientGroup.INTERNAL,
                is_primary=_get_bool(props, "Is Primary"),
                lead_id=_get_text(props, "Lead ID"),
                notion_id=page["id"],
            ))
        logger.info(f"Loaded {len(config.recipients)} recipients from Notion")
    except Exception as e:
        logger.error(f"Failed to load recipients from Notion: {e}")

    # ── Companies ──
    try:
        results = _query_all_pages(notion, companies_db_id)
        for page in results:
            props = page["properties"]
            type_str = _get_text(props, "Type").lower()
            config.companies.append(CompanyConfig(
                name=_get_text(props, "Company Name"),
                company_type=CompanyType(type_str) if type_str in [e.value for e in CompanyType] else CompanyType.PROSPECT,
                search_keywords=_get_text(props, "Search Keywords"),
                enabled=_get_bool(props, "Enabled"),
                priority=_get_text(props, "Priority") or "medium",
                notion_id=page["id"],
            ))
        logger.info(f"Loaded {len(config.companies)} companies from Notion")
    except Exception as e:
        logger.error(f"Failed to load companies from Notion: {e}")

    # ── Countries ──
    try:
        results = _query_all_pages(notion, countries_db_id)
        for page in results:
            props = page["properties"]
            config.countries.append(CountryConfig(
                name=_get_text(props, "Country"),
                region=_get_text(props, "Region"),
                flag_emoji=_get_text(props, "Flag Emoji"),
                enabled=_get_bool(props, "Enabled"),
                has_scraper=_get_bool(props, "Has Scraper"),
                pan_regional_sources=_get_multi_select(props, "Pan-regional Source"),
                notion_id=page["id"],
            ))
        logger.info(f"Loaded {len(config.countries)} countries from Notion")
    except Exception as e:
        logger.error(f"Failed to load countries from Notion: {e}")

    # Cache the config for fallback
    _save_config_cache(config)

    return config


def load_config_from_cache() -> Optional[PipelineConfig]:
    """Load config from YAML cache (fallback if Notion is down)."""
    if not Path(YAML_CACHE_PATH).exists():
        logger.warning("No cached config found")
        return None

    try:
        with open(YAML_CACHE_PATH, "r") as f:
            data = yaml.safe_load(f)
        logger.info("Loaded config from YAML cache (Notion fallback)")
        return _deserialize_config(data)
    except Exception as e:
        logger.error(f"Failed to load cached config: {e}")
        return None


def load_config(
    notion_token: str,
    sources_db_id: str,
    recipients_db_id: str,
    companies_db_id: str,
    countries_db_id: str,
) -> PipelineConfig:
    """Load config from Notion, falling back to YAML cache."""
    try:
        return load_config_from_notion(
            notion_token, sources_db_id, recipients_db_id,
            companies_db_id, countries_db_id,
        )
    except Exception as e:
        logger.warning(f"Notion unavailable: {e}. Trying YAML cache...")
        cached = load_config_from_cache()
        if cached:
            return cached
        raise RuntimeError("Cannot load config from Notion or cache") from e


def _query_all_pages(notion: NotionClient, database_id: str) -> list[dict]:
    """Query all pages from a Notion database (handles pagination)."""
    pages = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {"database_id": database_id, "page_size": 100}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = notion.databases.query(**kwargs)
        pages.extend(response["results"])
        has_more = response.get("has_more", False)
        start_cursor = response.get("next_cursor")

    return pages


def _save_config_cache(config: PipelineConfig):
    """Save config to YAML for fallback."""
    try:
        data = _serialize_config(config)
        with open(YAML_CACHE_PATH, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        logger.debug("Config cached to YAML")
    except Exception as e:
        logger.warning(f"Failed to cache config: {e}")


def _serialize_config(config: PipelineConfig) -> dict:
    """Serialize PipelineConfig to a dict for YAML."""
    return {
        "sources": [
            {
                "name": s.name, "url": s.url, "category": s.category.value,
                "source_type": s.source_type.value, "country": s.country,
                "enabled": s.enabled, "keywords_filter": s.keywords_filter,
                "scraper_id": s.scraper_id,
                "lookback_hours": s.lookback_hours,
            }
            for s in config.sources
        ],
        "recipients": [
            {
                "name": r.name, "email": r.email, "active": r.active,
                "group": r.group.value, "is_primary": r.is_primary,
                "lead_id": r.lead_id,
            }
            for r in config.recipients
        ],
        "companies": [
            {
                "name": c.name, "company_type": c.company_type.value,
                "search_keywords": c.search_keywords, "enabled": c.enabled,
                "priority": c.priority,
            }
            for c in config.companies
        ],
        "countries": [
            {
                "name": c.name, "region": c.region, "flag_emoji": c.flag_emoji,
                "enabled": c.enabled, "has_scraper": c.has_scraper,
                "pan_regional_sources": c.pan_regional_sources,
            }
            for c in config.countries
        ],
    }


def _deserialize_config(data: dict) -> PipelineConfig:
    """Deserialize a dict from YAML to PipelineConfig."""
    config = PipelineConfig()
    for s in data.get("sources", []):
        config.sources.append(SourceConfig(
            name=s["name"], url=s["url"],
            category=SourceCategory(s["category"]),
            source_type=SourceType(s["source_type"]),
            country=s.get("country", ""), enabled=s.get("enabled", True),
            keywords_filter=s.get("keywords_filter", []),
            scraper_id=s.get("scraper_id", ""),
            lookback_hours=int(s.get("lookback_hours") or 0),
        ))
    for r in data.get("recipients", []):
        config.recipients.append(RecipientConfig(
            name=r["name"], email=r["email"], active=r.get("active", True),
            group=RecipientGroup(r.get("group", "internal")),
            is_primary=r.get("is_primary", False),
            lead_id=r.get("lead_id", ""),
        ))
    for c in data.get("companies", []):
        config.companies.append(CompanyConfig(
            name=c["name"], company_type=CompanyType(c["company_type"]),
            search_keywords=c.get("search_keywords", ""),
            enabled=c.get("enabled", True), priority=c.get("priority", "medium"),
        ))
    for c in data.get("countries", []):
        config.countries.append(CountryConfig(
            name=c["name"], region=c.get("region", ""),
            flag_emoji=c.get("flag_emoji", ""), enabled=c.get("enabled", True),
            has_scraper=c.get("has_scraper", False),
            pan_regional_sources=c.get("pan_regional_sources", []),
        ))
    return config
