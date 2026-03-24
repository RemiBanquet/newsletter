"""
Constants for Daily Agri-News Digest v5.
Keyword lists, country mappings, and static config.
"""

# ── Crop keyword pre-filter ────────────────────────────────────────
# Used BEFORE sending to Claude to reduce API calls (~40% savings).
# Claude makes the final relevance decision.

CROP_KEYWORDS = [
    # Cereals (~700M ha)
    "wheat", "barley", "maize", "corn", "rice", "paddy", "sorghum",
    "millet", "oats", "rye", "triticale", "cereals", "grains",
    # Oilseeds (~300M ha)
    "soy", "soybean", "sunflower", "canola", "rapeseed", "wosr",
    "groundnut", "peanut", "oilseeds", "linseed",
    # Sugar (~50M ha)
    "sugarcane", "sugar cane", "sugar beet",
    # Cotton (~35M ha)
    "cotton",
    # Coffee (client-relevant: BASF Kenya)
    "coffee",
    # Pulses (~90M ha)
    "beans", "peas", "chickpea", "lentil", "pulses",
    # Root / tuber
    "potato", "beet",
    # Forage / pasture
    "alfalfa", "meadows", "pasture", "forage",
    # Generic
    "field crops", "crop", "harvest", "planted", "acreage", "yield",
    # Ag inputs — critical for newsletter audience (crop protection + fertilizer companies)
    "pesticide", "herbicide", "fungicide", "insecticide", "nematicide",
    "glyphosate", "dicamba", "glufosinate", "atrazine", "chlorpyrifos",
    "neonicotinoid", "paraquat", "2,4-d", "metolachlor", "acetamiprid",
    "crop protection", "plant protection", "biocontrol", "biopesticide",
    "biostimulant", "seed treatment", "trait", "gmo", "bt corn",
    "biological crop protection", "microbial", "ipm",
    "fertilizer", "fertiliser", "nitrogen", "phosphate", "potash", "urea",
    "ammonia", "nutrient", "npk",
    # Processing / crushing / biofuels / demand-side
    "crushing", "crush margin", "oilseed processing", "biodiesel",
    "ethanol", "feedstock", "milling",
    "sustainable aviation fuel", "saf", "biomethane", "biogas",
    "bioethanol", "biofuel", "hvo", "renewable diesel",
    # Distribution / channel
    "cooperative", "distributor", "farm supply", "ag retail",
    # Weather / climate impact on crops
    "drought", "flood", "frost", "heatwave", "heat wave",
    "el niño", "el nino", "la niña", "la nina",
    # Trade & policy (ag-specific)
    "farm bill", "common agricultural policy", "cap reform",
    "agricultural subsid", "food security", "grain export",
    "export ban", "import tariff",
]

CROP_CONTEXTUAL_KEYWORDS = [
    "arable land", "soil cover", "irrigated area", "crop rotation",
    "soil management", "irrigation", "cultivated area", "plant production",
    "agricultural area", "crop area", "farming", "cropping", "land use",
    "planted", "field crops", "crop acreage", "crop yields", "crop monitoring",
    "growing season", "planting season", "harvest season",
    # Agribusiness context
    "agrochemical", "agribusiness", "precision agriculture", "digital farming",
    "agriculture ministry", "agricultural market", "grain market",
    "oilseed market", "commodity market",
    # Sustainability / regenerative
    "regenerative agriculture", "carbon credit", "soil health",
    "sustainable agriculture", "cover crop",
]

# ── Country flags ──────────────────────────────────────────────────

COUNTRY_FLAGS = {
    "Argentina": "🇦🇷", "Austria": "🇦🇹", "Belgium": "🇧🇪",
    "Brazil": "🇧🇷", "Bulgaria": "🇧🇬", "Canada": "🇨🇦",
    "Croatia": "🇭🇷", "Czechia": "🇨🇿", "Denmark": "🇩🇰",
    "Egypt": "🇪🇬", "Estonia": "🇪🇪", "Europe": "🇪🇺",
    "Finland": "🇫🇮", "France": "🇫🇷", "Germany": "🇩🇪",
    "Hungary": "🇭🇺", "India": "🇮🇳", "Indonesia": "🇮🇩",
    "Ireland": "🇮🇪", "Italy": "🇮🇹", "Latvia": "🇱🇻",
    "Lithuania": "🇱🇹", "Mexico": "🇲🇽", "Morocco": "🇲🇦",
    "Netherlands": "🇳🇱", "New Zealand": "🇳🇿", "Poland": "🇵🇱",
    "Portugal": "🇵🇹", "Romania": "🇷🇴", "Slovakia": "🇸🇰",
    "South Africa": "🇿🇦", "Spain": "🇪🇸", "Sweden": "🇸🇪",
    "Turkey": "🇹🇷", "UK": "🇬🇧", "Ukraine": "🇺🇦",
    "USA": "🇺🇸",
}

# ── Category display config ───────────────────────────────────────

CATEGORY_EMOJI = {
    "crop_production": "🌾",
    "crop_land_use": "🗺️",
    "yields": "📈",
    "agtech": "🚜",
    "climate_weather": "🌦️",
    "markets": "💸",
    "regulation": "⚖️",
    "company_news": "🏢",
    "other": "📰",
}

SIGNAL_TYPE_EMOJI = {
    "market": "💸",
    "agtech": "🚀",
    "regulation": "⚖️",
    "partnership": "🤝",
    "executive": "👤",
    "product": "🧪",
    "financial": "📊",
    "other": "📰",
}

# ── Claude config ──────────────────────────────────────────────────

CLAUDE_MODEL_PRIMARY = "claude-haiku-4-5-20251001"
CLAUDE_MODEL_FALLBACK = "claude-sonnet-4-6"
CLAUDE_MAX_CONCURRENT = 2  # Max parallel API calls (keep low to avoid 429s on Tier 1 accounts)
CLAUDE_MAX_RETRIES = 3
CLAUDE_TIMEOUT_SECONDS = 30

# ── Geocoding config ──────────────────────────────────────────────

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "HyperplanAgriDigest/1.0 (remi@hyperplan.fr)"
NOMINATIM_DELAY_SECONDS = 1.1  # Nominatim policy: max 1 req/sec

# ── Dedup config ──────────────────────────────────────────────────

DEDUP_ARTICLES_FILE = "sent_articles.json"
DEDUP_PUBLICATIONS_FILE = "sent_publications.json"
DEDUP_SIGNALS_FILE = "sent_signals.json"
SIGNAL_LOOKBACK_DAYS = 7  # Company signals: 7-day rolling window
ARTICLE_LOOKBACK_HOURS = 48  # Articles/publications: 48h to catch late entries

# ── Lemlist config ────────────────────────────────────────────────

LEMLIST_API_BASE = "https://api.lemlist.com/api"
LEMLIST_SEND_USER_ID = "usr_FYxnRkvNuRFSixkrP"
LEMLIST_SEND_EMAIL = "remi@hyperplan.fr"
LEMLIST_SEND_MAILBOX_ID = "usm_RjWktLTma7TthAcj8"

# Campaign IDs
LEMLIST_CAMPAIGN_INTERNAL = "cam_iGwKk8nr6czJWRdRY"
LEMLIST_CAMPAIGN_EXTERNAL = "cam_PtmyYFfWuLc28DYz5"
LEMLIST_CAMPAIGN_ADMIN = "cam_gcwijB3Nbor62LQSs"

# Admin lead ID (for --test mode and admin reports)
LEMLIST_ADMIN_LEAD_ID = "lea_3hyxZzRCvsS4RPQ9j"
