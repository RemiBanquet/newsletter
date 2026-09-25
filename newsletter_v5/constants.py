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
    # ── Multilingual crop terms (pre-filter runs on original-language text) ──
    # Curated for precision: substring matching, so short/ambiguous words are
    # excluded on purpose (FR "mais"=but, FR "orge" inside "gorge", "korn").
    # A false positive costs one Haiku call; a false negative costs coverage.
    # French
    "blé", "colza", "tournesol", "céréales", "récolte", "moisson", "semis",
    "engrais", "rendement",
    # German ("raps" = rapeseed DE+SV; rare match on English "wraps" accepted)
    "weizen", "gerste", "raps", "getreide", "ernte", "aussaat", "dünger",
    "ackerbau", "landwirtschaft",
    # Spanish
    "trigo", "cebada", "maíz", "girasol", "cosecha", "siembra",
    "fertilizante", "rendimiento",
    # Portuguese (trigo/fertilizante shared with ES)
    "milho", "safra", "colheita", "plantio", "lavoura",
    # Italian
    "frumento", "orzo", "raccolto", "semina", "cereali", "concime",
    # Polish (stems — Slavic nouns inflect: pszenica/pszenicy/pszenicę)
    "pszenic", "kukurydz", "rzepak", "jęczmie", "zbóż", "zboż", "żniwa",
    "nawoz", "nawóz", "plon", "upraw", "zbior", "zbiór",
    # Czech (stems)
    "pšenic", "kukuřic", "řepk", "ječmen", "obilí", "skliz", "hnojiv", "úrod",
    # Swedish
    "vete", "spannmål", "skörd", "sådd", "gödsel", "havre",
    # Dutch
    "tarwe", "koolzaad", "graan", "oogst", "akkerbouw", "kunstmest",
    # Hungarian
    "búza", "kukorica", "repce", "árpa", "aratás", "műtrágya", "gabona",
    # Romanian
    "grâu", "porumb", "rapiță", "recoltă", "îngrășăminte",
    # Finnish
    "vehnä", "ohra", "rypsi", "kylvö",
    # Ukrainian (stems; урожа = RU-style spelling also used in UA media)
    "пшениц", "кукурудз", "ріпак", "ячмен", "ячмін", "соняшник", "зерн",
    "врожа", "урожа", "добрив", "посівн",
    # Turkish
    "buğday", "mısır", "arpa", "ayçiçeği", "hasat", "gübre", "tahıl", "rekolte",
    # Arabic
    "قمح", "ذرة", "شعير", "محصول", "محاصيل", "حصاد", "أسمدة",
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

# Alternate spellings used in the Sources DB (e.g. "United Kingdom" for DEFRA).
_COUNTRY_ALIASES = {
    "united kingdom": "UK", "great britain": "UK", "england": "UK",
    "united states": "USA", "us": "USA", "czech republic": "Czechia",
    "eu": "Europe", "european union": "Europe", "türkiye": "Turkey",
}


def country_flag(country: str) -> str:
    """Country name from config -> flag emoji. 🌍 for Global, '' if unknown."""
    name = (country or "").strip()
    if not name:
        return ""
    if name.lower() == "global":
        return "🌍"
    if name in COUNTRY_FLAGS:
        return COUNTRY_FLAGS[name]
    alias = _COUNTRY_ALIASES.get(name.lower())
    if alias:
        return COUNTRY_FLAGS.get(alias, "")
    for key, flag in COUNTRY_FLAGS.items():
        if key.lower() == name.lower():
            return flag
    return ""

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

# ── Market brief config ────────────────────────────────────────────
# The brief is generated by Haiku from the day's accepted items and
# verified before it ships. See market_brief.py.

BRIEF_MAX_WORDS = 320

# Hard fail if any of these appear in the brief (AI-tell vocabulary).
BRIEF_BANNED_WORDS = [
    "leverage", "landscape", "robust", "seamless", "game-changer", "cutting-edge",
    "unlock", "harness", "pivotal", "crucial", "underscore", "navigate",
    "delve", "realm", "testament", "foster", "empower", "streamline", "elevate",
    "transformative", "dynamic", "optimize",
]

# Soft warning patterns (logged, not blocking): negative-parallelism tells.
BRIEF_NEG_PARALLELISM = [
    "not just", "it's not about", "isn't about", "rather than", "instead of",
    "the question isn't", "x is dead",
]

# Section labels the brief may use, in the order they should appear.
BRIEF_SECTION_ORDER = [
    "Markets",
    "Fertilizer and energy",
    "Weather and crop stress",
    "Industry players",
    "Regulation and agtech",
    "Acreage and crop shifts",
]

# ── Claude config ──────────────────────────────────────────────────

CLAUDE_MODEL_PRIMARY = "claude-haiku-4-5-20251001"
CLAUDE_MODEL_FALLBACK = "claude-sonnet-4-6"
CLAUDE_MAX_CONCURRENT = 2  # Max parallel API calls (keep low to avoid 429s on Tier 1 accounts)
CLAUDE_MAX_RETRIES = 3
CLAUDE_TIMEOUT_SECONDS = 30

# ── Claude pricing (USD per million tokens) ────────────────────────
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-07-05). Cache write = 1.25x input (5-min TTL), cache
# read = 0.10x input, Batch API = 50% off all token types. Unknown
# models fall back to Sonnet pricing on purpose: better to over-report
# spend than to hide it.
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
}
MODEL_PRICING_DEFAULT = {"input": 3.00, "output": 15.00}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10
BATCH_API_DISCOUNT = 0.50

# ── Batch API (bulk classification) ────────────────────────────────
# The digest is a daily cron, so latency does not matter: bulk
# classification (articles, signals, publications) goes through the
# Message Batches API at 50% of streaming price. If the batch has not
# finished within the timeout, it is cancelled and the run falls back
# to the streaming path so the digest always ships.
USE_BATCH_API = True
BATCH_POLL_SECONDS = 15
BATCH_TIMEOUT_MINUTES = 20

# Items per request (multi-item tool calls).
ARTICLE_BATCH_SIZE = 5
SIGNAL_BATCH_SIZE = 8
PUBLICATION_BATCH_SIZE = 8

# Max chars of article body sent to the classifier. 1,200 chars
# (~300 tokens) carries the lede and first paragraphs, which is what
# the category decision and 1-sentence summary are based on.
ARTICLE_CONTENT_MAX_CHARS = 1200

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

# ── LinkedIn signals (via Google News site: query) ────────────────
# Second signal query per company: '"{name}" site:linkedin.com'.
# Google News indexes LinkedIn sparsely, so yield may be low — check
# the "LinkedIn signals" raw count in source health after a week and
# disable here if it stays at zero.
SIGNAL_LINKEDIN_ENABLED = True
SIGNAL_LINKEDIN_MAX_PER_COMPANY = 10  # Cap: these skip the ag-keyword pre-filter

# ── Email sender config ──────────────────────────────────────────

SENDER_EMAIL = "remi.banquet@gmail.com"


# ── v6 selection layer ─────────────────────────────────────────────
# Rule filters applied to signal headlines BEFORE classification (free,
# deterministic). Case-insensitive regexes. Job ads and market-report spam
# made up about 1 in 3 LinkedIn signals in the 20-22 Sep 2026 sample.
SIGNAL_NOISE_PATTERNS = [
    r"\bhiring\b", r"\bintern(ship)?\b", r"linkedin jobs", r"#hiring",
    r"\bwe'?re hiring\b", r"\bjob (opening|offer)\b", r"\bapply now\b",
    r"request (for )?(a )?sample", r"\bcagr\b", r"market (size|report|research|forecast)",
    r"\b20\d\d\s*[-–]\s*20\d\d\b.*\bmarket\b",
]

# A hiring post for a senior commercial or technical role is a signal (a new
# buyer persona at the account), so it is kept and left to the classifier.
SIGNAL_SENIOR_ROLE_PATTERN = (
    r"\b(head|director|vp|vice president|chief|cmo|cco|general manager|country manager)\b"
)

# Email content caps (v6 template).
TOP_STORIES_MAX = 12            # ranked stories shown in the email
TOP_STORIES_PER_SOURCE = 2      # max stories from one source
RADAR_MAX = 5                   # account signals shown in the email
RADAR_MIN_SCORE = 4             # 4 = strategic move, 5 = buying trigger
PSD_MOVERS_MAX = 5              # rows in the USDA PSD block (release days only)

# Links shown in the email for "see everything" (Notion archives).
ARCHIVE_URL = "https://www.notion.so/1f45e97ecd7d809cad9ff048ce70d972"
SIGNALS_URL = "https://www.notion.so/1f7cb90adabd46c8a573b2516e45d5ee"
FEEDBACK_URL = "mailto:remi@hyperplan.fr?subject=Digest%20feedback"
