"""
Data models for Daily Agri-News Digest v5.
Uses dataclasses for simplicity and clear field definitions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Enums ──────────────────────────────────────────────────────────

class SourceCategory(str, Enum):
    MEDIA = "media"
    OFFICIAL_PUBLICATION = "official_publication"
    COMPANY_SIGNAL = "company_signal"


class SourceType(str, Enum):
    RSS = "rss"
    SELENIUM_SCRAPER = "selenium-scraper"
    REQUESTS_SCRAPER = "requests-scraper"
    API = "api"


class CompanyType(str, Enum):
    CLIENT = "client"
    PROSPECT = "prospect"


class RecipientGroup(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ArticleCategory(str, Enum):
    CROP_PRODUCTION = "crop_production"
    CROP_LAND_USE = "crop_land_use"
    YIELDS = "yields"
    AGTECH = "agtech"
    CLIMATE_WEATHER = "climate_weather"
    MARKETS = "markets"
    REGULATION = "regulation"
    COMPANY_NEWS = "company_news"
    OTHER = "other"


class SignalType(str, Enum):
    MARKET = "market"
    AGTECH = "agtech"
    REGULATION = "regulation"
    PARTNERSHIP = "partnership"
    EXECUTIVE = "executive"
    PRODUCT = "product"
    FINANCIAL = "financial"
    OTHER = "other"


# ── Config models ──────────────────────────────────────────────────

@dataclass
class SourceConfig:
    """A news or publication source from Notion."""
    name: str
    url: str
    category: SourceCategory
    source_type: SourceType
    country: str = ""
    enabled: bool = True
    keywords_filter: list[str] = field(default_factory=list)
    scraper_id: str = ""  # Python function name for custom scrapers
    notion_id: str = ""   # Notion page ID for reference


@dataclass
class RecipientConfig:
    """An email recipient from Notion."""
    name: str
    email: str
    active: bool = True
    group: RecipientGroup = RecipientGroup.INTERNAL
    is_primary: bool = False
    lead_id: str = ""     # Lemlist lead ID (lea_xxx) — only for primary
    notion_id: str = ""


@dataclass
class CompanyConfig:
    """A tracked company (client or prospect) from Notion."""
    name: str
    company_type: CompanyType
    search_keywords: str = ""
    enabled: bool = True
    priority: str = "medium"  # high / medium / low
    notion_id: str = ""


@dataclass
class CountryConfig:
    """A country of interest from Notion."""
    name: str
    region: str = ""  # EU / Americas / Africa-ME / Asia-Pacific
    flag_emoji: str = ""
    enabled: bool = True
    has_scraper: bool = False
    pan_regional_sources: list[str] = field(default_factory=list)
    notion_id: str = ""


@dataclass
class PipelineConfig:
    """Full pipeline configuration assembled from Notion databases."""
    sources: list[SourceConfig] = field(default_factory=list)
    recipients: list[RecipientConfig] = field(default_factory=list)
    companies: list[CompanyConfig] = field(default_factory=list)
    countries: list[CountryConfig] = field(default_factory=list)

    @property
    def active_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    @property
    def active_recipients(self) -> list[RecipientConfig]:
        return [r for r in self.recipients if r.active]

    @property
    def clients(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.enabled and c.company_type == CompanyType.CLIENT]

    @property
    def prospects(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.enabled and c.company_type == CompanyType.PROSPECT]

    @property
    def active_countries(self) -> list[CountryConfig]:
        return [c for c in self.countries if c.enabled]

    def recipient_groups(self) -> dict[RecipientGroup, dict]:
        """Build send groups for Lemlist: {group: {lead_id, cc_emails}}."""
        groups = {}
        for group_type in RecipientGroup:
            members = [r for r in self.active_recipients if r.group == group_type]
            if not members:
                continue
            primary = next((r for r in members if r.is_primary), None)
            if not primary:
                # Fallback: first member is primary
                primary = members[0]
            cc = [r.email for r in members if r.email != primary.email]
            groups[group_type] = {
                "name": group_type.value,
                "lead_id": primary.lead_id,
                "primary_email": primary.email,
                "cc": cc,
            }
        return groups


# ── Article / Publication / Signal models ──────────────────────────

@dataclass
class GeoLocation:
    """Geographic location extracted from an article."""
    place_name: str = ""
    country_iso: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass
class Article:
    """A news article after classification."""
    id: str                          # SHA-256 hash of URL
    title: str
    url: str
    source_name: str
    published_at: Optional[datetime] = None
    original_language: str = "en"
    summary: str = ""                # 3-bullet English summary
    category: ArticleCategory = ArticleCategory.OTHER
    tags: list[str] = field(default_factory=list)
    location: GeoLocation = field(default_factory=GeoLocation)
    relevant: bool = False
    raw_content: str = ""            # Original text fed to Claude


@dataclass
class Publication:
    """An official statistical publication."""
    id: str                          # SHA-256 hash of URL
    title: str
    url: str
    source_name: str
    country: str = ""
    flag_emoji: str = ""
    published_at: Optional[datetime] = None
    original_language: str = "en"
    summary: str = ""
    relevant: bool = True            # Set by LLM classification
    location: GeoLocation = field(default_factory=GeoLocation)


@dataclass
class CompanySignal:
    """A market signal about a tracked company."""
    id: str                          # SHA-256 hash of URL
    title: str
    url: str
    company_name: str
    company_type: CompanyType
    signal_type: SignalType = SignalType.OTHER
    summary: str = ""                # 2-bullet summary
    source_name: str = ""
    published_at: Optional[datetime] = None
    original_language: str = "en"
    location: GeoLocation = field(default_factory=GeoLocation)


# ── Run metrics ────────────────────────────────────────────────────

@dataclass
class RunMetrics:
    """KPIs for the admin report."""
    # Articles
    articles_fetched: int = 0
    articles_accepted: int = 0
    articles_rejected: int = 0
    articles_duplicate: int = 0
    # Publications
    publications_fetched: int = 0
    publications_accepted: int = 0
    publications_rejected: int = 0
    publications_duplicate: int = 0
    # Company signals
    signals_fetched: int = 0
    signals_accepted: int = 0
    signals_rejected: int = 0
    signals_duplicate: int = 0
    # Geocoding
    geocoding_attempted: int = 0
    geocoding_succeeded: int = 0
    geocoding_cached: int = 0
    # Sources
    sources_total: int = 0
    sources_healthy: int = 0
    source_errors: list[str] = field(default_factory=list)
    # LLM
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # Delivery
    emails_sent: int = 0
    emails_failed: int = 0
    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def runtime_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    @property
    def runtime_display(self) -> str:
        s = self.runtime_seconds
        if s < 60:
            return f"{s:.0f}s"
        return f"{s / 60:.1f} min"

    @property
    def geocoding_rate(self) -> str:
        if self.geocoding_attempted == 0:
            return "N/A"
        pct = (self.geocoding_succeeded / self.geocoding_attempted) * 100
        return f"{pct:.0f}% ({self.geocoding_succeeded}/{self.geocoding_attempted})"

    def estimate_cost(self):
        """Estimate cost based on Haiku 4.5 pricing ($0.80/M input, $4/M output, $0.08/M cached)."""
        self.estimated_cost_usd = (
            (self.input_tokens / 1_000_000) * 0.80
            + (self.output_tokens / 1_000_000) * 4.00
            + (self.cache_read_tokens / 1_000_000) * 0.08
        )
