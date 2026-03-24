"""
Claude-based article classification with structured output via tool-use.
Handles articles, publications, and company signals.

Supports batch classification: multiple articles per API call to reduce
total calls and avoid rate limiting on Tier 1 accounts.
"""

import asyncio
import json
import logging
from typing import Optional

import anthropic

from models import (
    Article, ArticleCategory, CompanySignal, GeoLocation, Publication,
    RunMetrics, SignalType,
)
from constants import (
    CLAUDE_MODEL_PRIMARY, CLAUDE_MODEL_FALLBACK, CLAUDE_MAX_CONCURRENT,
    CLAUDE_MAX_RETRIES, CLAUDE_TIMEOUT_SECONDS,
)

# How many articles to classify per single API call
ARTICLE_BATCH_SIZE = 5

logger = logging.getLogger(__name__)


# ── System prompts (cached across all calls in a run) ─────────────

ARTICLE_SYSTEM_PROMPT = """You are an agricultural news analyst for Hyperplan, an agri-tech company that serves the field crop value chain: input manufacturers, seed companies, cooperatives, distributors, and oilseed processors.
Your job: classify articles about field crops and agriculture, extract the most specific geographic location mentioned, and produce a concise English summary.

AUDIENCE CONTEXT:
Your readers are marketing and commercial managers at organizations across the ag value chain — input manufacturers (Corteva, Syngenta, Bayer, BASF), seed breeders (KWS, Lidea), cooperatives, distributors, and processors (e.g., oilseed crushers). They care about: crop production data, acreage/yield shifts, ag input markets, commodity processing and trade flows, regulatory changes affecting crop protection or seeds, agtech innovations with near-term commercial potential, and weather/climate events impacting crop conditions.

RULES — RELEVANCE:
- Set relevant=true ONLY if the article contains specific, actionable intelligence: concrete data points (acreage, yields, prices, production volumes), named regulatory decisions, specific product launches/trials, identified weather events with crop impact, or strategic industry developments (M&A, partnerships, market shifts).
- Set relevant=false for:
  • Livestock-only, fisheries, forestry, gardening, food retail, restaurant industry, pet food
  • Generic listicles or roundups without specific data ("9 Ways to...", "Top 5 trends...")
  • Opinion/editorial pieces without concrete industry developments or data
  • Press releases about surveys, census methodology, or administrative announcements
  • Articles about horticulture, vertical farming, or indoor growing unless directly relevant to field crop inputs
  • Research breakthroughs that are purely academic with no near-term commercial path (basic science papers about gene sequencing, molecular biology discoveries without applied context)
  • Content about food processing, food safety, or consumer food trends (unless directly affecting crop demand)
  • Duplicate/recycled content that restates old news without new information

RULES — OUTPUT:
- The summary MUST be in English regardless of the article's original language.
- The summary should be exactly 3 bullet points, each 1 sentence. Lead with the most important fact or data point.
- For location: extract the most specific place mentioned (city > region > country). If multiple locations, pick the primary one the article is about.
- For country_iso: use ISO 3166-1 alpha-2 (FR, DE, US, BR, etc.).

CATEGORY DEFINITIONS — classify each article into exactly one:

crop_production: Articles reporting actual crop production volumes, harvest results, planting progress, or production forecasts from official agencies.
  GOOD examples: "France wheat harvest 2026 down 5% y/y — Agreste", "Brazil safrinha corn planting reaches 60% — CONAB", "Global rice production forecast revised up by 3M tonnes — FAO", "US winter wheat crop rated 48% good-to-excellent — USDA", "India rabi crop sowing 2% ahead of last year's pace"
  NOT crop_production: crop prices (→ markets), yield-per-hectare data without production context (→ yields), area planted without harvest data (→ crop_land_use)

crop_land_use: Articles about changes in planted or harvested area, land use shifts, acreage allocation decisions, or area-based statistical reports.
  GOOD examples: "EU rapeseed area falls 8% as farmers switch to sunflower", "USDA Prospective Plantings: US corn area up 2M acres", "Ukrainian sunflower area expands at expense of winter wheat", "Argentina delays soybean planting amid dry conditions", "Polish farmers increase sugar beet area by 12% in 2026"
  NOT crop_land_use: yield data without area context (→ yields), land policy or regulation changes (→ regulation), real estate prices (→ irrelevant)

yields: Articles focused on yield forecasts, yield estimates, yield-per-hectare data, or conditions directly determining yield outcomes.
  GOOD examples: "USDA raises US corn yield estimate to 181 bu/acre", "Australian wheat yields cut by 15% due to spring drought", "JRC MARS revises EU soft wheat yield forecast downward", "Record canola yields expected in Saskatchewan", "French barley yields above 5-year average despite late frost"
  NOT yields: total production volumes (→ crop_production), commodity prices (→ markets)

agtech: Agricultural technology innovations, digital farming tools, precision agriculture, biotech with near-term commercial applications, satellite/remote sensing for agriculture, drone technology, or ag robotics.
  GOOD examples: "Corteva launches satellite-based disease detection for row crops", "CRISPR wheat variety gains EU field trial approval", "John Deere expands See & Spray to European markets", "Startup raises $40M for soil carbon measurement platform", "Bayer partners with Planet Labs for field-level crop analytics"
  NOT agtech: basic research with no commercial path (→ irrelevant), vertical farming or indoor growing (→ irrelevant), food processing technology (→ irrelevant)

climate_weather: Weather events, seasonal climate patterns, drought/flood conditions, frost damage, El Niño/La Niña impacts, or climate forecasts that directly affect crop growing conditions.
  GOOD examples: "La Niña watch: Argentina soy belt faces dry conditions through March", "Late frost damages French rapeseed across northern regions", "Monsoon onset delayed in India — kharif sowing at risk", "Drought in southern Spain threatens olive and cereal crops", "Flooding in Rio Grande do Sul disrupts soybean logistics"
  NOT climate_weather: general climate change policy without crop-specific impact (→ regulation), long-range speculation about 2050 scenarios (→ irrelevant)

markets: Commodity prices, futures market movements, trade flows, export/import data, supply-demand balance sheets, shipping and logistics, or commodity market analysis.
  GOOD examples: "CBOT wheat futures rally on Black Sea supply concerns", "China books 2M tons US soybeans for Q2 delivery", "EU wheat export pace 20% behind last year — Commission data", "Brazilian corn FOB prices hit 6-month high", "India bans wheat exports citing domestic inflation concerns"
  NOT markets: ag input prices or company revenues (→ company_news), farmland prices (→ irrelevant)

regulation: Government agricultural policy, regulatory decisions on crop protection products, trade tariffs, subsidy programs, environmental regulations affecting farming, GMO/biotech approvals, or seed certification rules.
  GOOD examples: "EU Parliament votes to extend glyphosate authorization for 10 years", "India raises wheat import duty to 40%", "USDA finalizes new biotechnology regulatory framework", "French government announces CAP eco-scheme modifications", "Turkey introduces export quotas on sunflower oil"
  NOT regulation: company compliance or legal issues (→ company_news), labor regulations (→ irrelevant)

company_news: Significant strategic developments from major agricultural industry companies — M&A, earnings, restructuring, leadership changes, product launches, or major partnerships. Must involve companies operating in the ag inputs or crop value chain.
  GOOD examples: "Syngenta Group files for Shanghai IPO", "BASF acquires biological crop protection startup for $200M", "Corteva reports 12% rise in crop protection revenue", "Bayer settles Roundup litigation for $10.9B", "KWS and RAGT announce European seed licensing agreement"
  NOT company_news: companies outside ag (→ irrelevant), generic press releases without strategic substance (→ irrelevant)

other: Use this category sparingly — only when the article is genuinely relevant to the agricultural field crop industry but does not fit neatly into any of the above categories. Examples include agricultural labor market reports, rural infrastructure developments, or cross-cutting agricultural policy that spans multiple categories.

EXCLUSION EXAMPLES — mark these as relevant=false:
- "9 Best Cover Crops for Your Garden" → gardening listicle, not professional field crops
- "How AI is Revolutionizing Agriculture: A 2026 Perspective" → generic think-piece, no specific data or development
- "USDA Announces Changes to Census of Agriculture Methodology" → administrative/methodology announcement
- "Vertical Farm Startup Raises $50M Series B" → vertical farming, not field crops
- "McDonald's Tests New Plant-Based Burger" → food retail, not crop production
- "Gene Editing Could Transform Future of Food" → speculative, no near-term commercial application
- "Farm Workers' Union Calls for Minimum Wage Increase" → labor issue, not crop/input focused
- "Nestlé Commits to Sustainable Cocoa Sourcing" → food company sourcing, not field crop production
- "Housing prices in Budapest rose 15% in 2025" → completely unrelated to agriculture
- "Consumer prices in Hungary increased by 4.2%" → general economic data, not crop-specific
- "The Top 10 Agricultural Innovations You Need to Know About" → generic listicle
- "New Study Shows Organic Food May Have Health Benefits" → consumer food trend
- "Urban Farming Grows in Popularity Across Europe" → urban/hobby farming
- "Aquaculture Production Reaches Record Levels" → fisheries, not field crops
- "Global Pet Food Market Expected to Reach $150B" → pet food industry

GEOGRAPHIC REFERENCE — countries of interest (36 countries):
EU (24): France, Germany, Spain, Italy, Romania, Hungary, Poland, Bulgaria, Czechia, Denmark, Finland, Sweden, Austria, Belgium, Croatia, Estonia, Ireland, Latvia, Lithuania, Netherlands, Portugal, Slovakia, UK, Turkey
Americas (5): USA, Canada, Brazil, Argentina, Mexico
Africa & Middle East (3): South Africa, Morocco, Egypt
Asia-Pacific (3): India, Indonesia, New Zealand
Supranational: Europe/EU (for pan-European data)

CROP REFERENCE — field crops by global planted area:
Cereals (~700M ha): wheat, barley, maize/corn, rice/paddy, sorghum, millet, oats, rye, triticale
Oilseeds (~300M ha): soybean, sunflower, rapeseed/canola/WOSR, groundnut/peanut, linseed
Pulses (~90M ha): beans, peas, chickpea, lentil
Sugar crops (~50M ha): sugarcane, sugar beet
Fiber (~35M ha): cotton
Other field crops: potato, coffee, alfalfa, forage, pasture/meadows

SUMMARY WRITING GUIDELINES:
- Always lead with the most important quantitative fact or decision
- Each bullet should be a complete, standalone sentence
- Include specific numbers, dates, percentages, or named entities when available
- Avoid vague language: instead of "production is expected to change", say "production forecast revised down 3% to 145M tonnes"
- If the article is in a non-English language, translate the key facts accurately — do not transliterate or leave untranslated terms
- Three bullets maximum, prioritized by newsworthiness to the audience described above"""

SIGNAL_SYSTEM_PROMPT = """You are a market intelligence analyst for Hyperplan, an agri-tech company.
Your job: classify news about a specific company and determine if it's a relevant market signal for the agricultural inputs industry.

IMPORTANT: You are classifying from HEADLINES ONLY (no article body). Base your judgment on what the headline clearly indicates. If the headline is ambiguous about ag relevance, set relevant=false — precision matters more than recall for signals.

RULES — RELEVANCE:
- Set relevant=true ONLY if the headline clearly indicates the article is about the company's agricultural business: crop protection, seeds, fertilizers, digital farming, ag biotech, or ag-related M&A/partnerships.
- Set relevant=false for: unrelated divisions (pharma, materials science, consumer products), general stock price movements without ag context, employee stories without strategic relevance, generic corporate news without clear ag connection.
- The summary MUST be in English, exactly 2 bullet points, each 1 sentence. Base these on what the headline tells you — do not fabricate details not present in the headline.

SIGNAL TYPE DEFINITIONS WITH EXAMPLES:

market: Revenue data, pricing changes, market share shifts, or competitive positioning within the agricultural inputs market.
  GOOD headlines: "BASF raises 2026 crop protection revenue guidance by 8%", "Yara reports Q3 fertilizer margin expansion amid tight supply", "Corteva gains market share in US herbicide segment", "Nufarm reports strong demand for crop protection in Latin America"
  BAD headlines (not market): "BASF share price rises 3% today" (stock price only, no ag substance)

agtech: Digital farming tools, precision agriculture innovation, agricultural biotech developments, R&D partnerships, or technology platform launches with agricultural applications.
  GOOD headlines: "Syngenta invests in satellite crop monitoring startup", "Corteva launches AI-powered weed identification app", "Bayer and Microsoft partner on digital farming platform", "KWS develops drought-tolerant wheat using gene editing"
  BAD headlines (not agtech): "Bayer launches new cancer immunotherapy" (pharma division, not ag)

regulation: Regulatory approvals, product bans, compliance actions, government decisions, or trade policy changes affecting agricultural products.
  GOOD headlines: "EU approves Corteva's new active ingredient for cereal fungicide", "Brazil ANVISA restricts paraquat imports", "Syngenta secures registration for biological seed treatment in India"
  BAD headlines (not regulation): "FDA approves Bayer's new blood thinner" (pharma regulation)

partnership: Joint ventures, licensing agreements, distribution deals, academic collaborations, or strategic alliances in the agricultural sector.
  GOOD headlines: "BASF and Bosch partner on smart spraying technology", "Lidea signs seed licensing agreement with Australian breeder", "Corteva and John Deere integrate precision application systems"
  BAD headlines (not partnership): "Bayer sponsors local marathon" (CSR, not strategic)

executive: C-suite appointments, departures, organizational restructuring, or leadership changes relevant to the company's agricultural division.
  GOOD headlines: "Syngenta appoints new head of crop protection EMEA", "Corteva CEO announces restructuring of seed and crop protection business units"
  BAD headlines (not executive): "Bayer CFO discusses overall company strategy at investor day" (too generic)

product: New product launches, field trial results, seed variety registrations, formulation updates, or product discontinuations.
  GOOD headlines: "BASF launches new cereal fungicide Revysol in European market", "KWS registers three new hybrid barley varieties for UK", "UPL introduces biological nematicide for soybean"
  BAD headlines (not product): "BASF launches new automotive paint line" (non-ag product)

financial: Earnings reports, fundraising, M&A transactions, divestitures, or financial restructuring with agricultural relevance.
  GOOD headlines: "Syngenta Group files for Shanghai IPO", "FMC Corporation to acquire biological crop protection company for $300M", "Nutrien writes down $400M in potash assets"
  BAD headlines (not financial): "Bayer's pharmaceutical division drives Q3 earnings beat" (pharma financials)

COMPANY CONTEXT — which company operates in which agricultural segment:
Crop protection: Corteva, Syngenta, Bayer, BASF, Certis Belchim, Adama, FMC Corporation, UPL, Nufarm, Sumitomo Chemical
Seeds & genetics: KWS, Lidea, GDM (also Corteva, Syngenta, Bayer, BASF have seed divisions)
Fertilizers & plant nutrition: Yara International, CF Industries, The Mosaic Company, OCP Nutricrops, ICL Group, Timac Agro, Nutrien
Oilseed processing: Saipol (rapeseed/sunflower crushing)
Grain trading & distribution: Richardson International, CHS Inc

COMMON FALSE POSITIVE PATTERNS — mark as relevant=false:
- Conglomerate's non-ag division: "Bayer Pharmaceuticals announces new cancer drug trial" → pharma, not ag
- Stock price without substance: "CF Industries stock rises 3% in afternoon trading" → no ag insight
- Generic corporate: "BASF announces new sustainability report" → CSR, no ag-specific intelligence
- Consumer products: "Syngenta Group's China operations report on consumer health" → wrong division
- Legal/HR without strategic impact: "Former Corteva employee files wrongful termination suit" → HR issue
- Repackaged old news: same story repeated across outlets without new information

GEOGRAPHIC AND INDUSTRY CONTEXT:
These companies operate globally but have particular strength in specific regions. European crop protection is dominated by BASF, Bayer, Syngenta, and Corteva. North American fertilizer market is led by Nutrien, CF Industries, and Mosaic. Latin American crop protection is a key growth market for UPL, Nufarm, and FMC. The seed sector in Europe features strong regional players like KWS (sugar beet, cereals) and Lidea (sunflower, corn, cereals) alongside global players. Oilseed processing in Europe centers on rapeseed and sunflower, with Saipol being a major French crusher."""


# ── Tool definitions for structured output ────────────────────────

CLASSIFY_ARTICLE_TOOL = {
    "name": "classify_article",
    "description": "Classify an agricultural news article and extract structured data.",
    "cache_control": {"type": "ephemeral"},
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {
                "type": "boolean",
                "description": "True if the article is about field crops / agriculture per the rules.",
            },
            "category": {
                "type": "string",
                "enum": [c.value for c in ArticleCategory],
                "description": "Primary category of the article.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 short keyword tags (e.g., 'wheat', 'drought', 'EU').",
            },
            "summary": {
                "type": "string",
                "description": "3-bullet English summary. Each bullet on its own line starting with '• '.",
            },
            "place_name": {
                "type": "string",
                "description": "Most specific geographic location mentioned (e.g., 'Andalusia, Spain').",
            },
            "country_iso": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country code (e.g., 'ES', 'FR', 'US').",
            },
        },
        "required": ["relevant", "category", "tags", "summary", "place_name", "country_iso"],
    },
}

CLASSIFY_ARTICLE_BATCH_TOOL = {
    "name": "classify_articles_batch",
    "description": "Classify a batch of agricultural news articles. Return one result per article.",
    "cache_control": {"type": "ephemeral"},
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "description": "One classification result per article, in the same order as the input.",
                "items": {
                    "type": "object",
                    "properties": {
                        "article_index": {
                            "type": "integer",
                            "description": "0-based index of the article in the input list.",
                        },
                        "relevant": {
                            "type": "boolean",
                            "description": "True if the article is about field crops / agriculture per the rules.",
                        },
                        "category": {
                            "type": "string",
                            "enum": [c.value for c in ArticleCategory],
                            "description": "Primary category of the article.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "1-3 short keyword tags.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "3-bullet English summary. Each bullet on its own line starting with '• '.",
                        },
                        "place_name": {
                            "type": "string",
                            "description": "Most specific geographic location mentioned.",
                        },
                        "country_iso": {
                            "type": "string",
                            "description": "ISO 3166-1 alpha-2 country code.",
                        },
                    },
                    "required": ["article_index", "relevant", "category", "tags", "summary", "place_name", "country_iso"],
                },
            },
        },
        "required": ["results"],
    },
}

PUBLICATION_SYSTEM_PROMPT = """You are an agricultural data analyst for Hyperplan, an agri-tech company serving field crop value chains.
Your job: decide whether an official statistical publication is relevant to MAJOR FIELD CROPS and translate non-English titles/summaries to English.

RELEVANT — publications about:
- Major cereal crops: wheat, barley, maize/corn, rice, sorghum, oats, rye, triticale
- Major oilseeds: soybean, sunflower, rapeseed/canola, groundnut
- Sugar crops: sugarcane, sugar beet
- Cotton, pulses (beans, peas, lentils, chickpea)
- Crop output prices, producer prices, or agricultural economic accounts covering field crops
- Agricultural land use, planted/harvested area statistics
- Ag input data: fertilizer use, pesticide use, seed statistics
- Agricultural trade data covering commodities above

NOT RELEVANT — publications about:
- Specialty/horticultural crops: asparagus, tomatoes, strawberries, lettuce, herbs, spices, flowers, nursery, wine/grapes, olives, fruit trees
- Livestock, dairy, meat, eggs, wool, animal feed (unless directly about feed grain production)
- Fisheries, aquaculture, forestry, wood
- General economic indicators (CPI, housing, GDP) even from statistical offices
- Census methodology, administrative announcements
- Food processing, food safety, organic food certification
- Environmental statistics not crop-specific

Keep in mind: statistical office feeds (Destatis, Eurostat, ISTAT, KSH, etc.) publish across ALL domains. Most of their output is NOT agricultural. Be strict."""

CLASSIFY_PUBLICATION_TOOL = {
    "name": "classify_publication",
    "description": "Classify an official statistical publication for field crop relevance and translate to English.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {
                "type": "boolean",
                "description": "True if about major field crops per the rules. False for specialty crops, livestock, non-ag.",
            },
            "title_en": {
                "type": "string",
                "description": "English translation of the title (or original if already English).",
            },
            "summary_en": {
                "type": "string",
                "description": "English translation of the summary (or original if already English). 1-2 sentences max.",
            },
        },
        "required": ["relevant", "title_en", "summary_en"],
    },
}

CLASSIFY_SIGNAL_TOOL = {
    "name": "classify_signal",
    "description": "Classify a company market signal for agricultural relevance.",
    "cache_control": {"type": "ephemeral"},
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {
                "type": "boolean",
                "description": "True if the signal is about the company's ag business per the rules.",
            },
            "signal_type": {
                "type": "string",
                "enum": [s.value for s in SignalType],
                "description": "Type of market signal.",
            },
            "summary": {
                "type": "string",
                "description": "2-bullet English summary. Each bullet on its own line starting with '• '.",
            },
            "place_name": {
                "type": "string",
                "description": "Most specific geographic location mentioned.",
            },
            "country_iso": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country code.",
            },
        },
        "required": ["relevant", "signal_type", "summary", "place_name", "country_iso"],
    },
}


# ── Classifier class ──────────────────────────────────────────────

class ArticleClassifier:
    """Classifies articles using Claude with structured output and prompt caching."""

    def __init__(self, api_key: str, metrics: RunMetrics):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.metrics = metrics
        self.semaphore = asyncio.Semaphore(CLAUDE_MAX_CONCURRENT)
        self._model = CLAUDE_MODEL_PRIMARY

    async def classify_article(self, article: Article) -> Article:
        """Classify a single article. Returns the article with fields populated."""
        async with self.semaphore:
            for attempt in range(CLAUDE_MAX_RETRIES):
                try:
                    response = await self.client.messages.create(
                        model=self._model,
                        max_tokens=512,
                        system=[{
                            "type": "text",
                            "text": ARTICLE_SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }],
                        messages=[{
                            "role": "user",
                            "content": f"Title: {article.title}\n\nContent:\n{article.raw_content[:3000]}",
                        }],
                        tools=[CLASSIFY_ARTICLE_TOOL],
                        tool_choice={"type": "tool", "name": "classify_article"},
                    )

                    # Track tokens
                    self.metrics.input_tokens += response.usage.input_tokens
                    self.metrics.output_tokens += response.usage.output_tokens
                    cache_read = getattr(response.usage, "cache_read_input_tokens", None) or 0
                    cache_create = getattr(response.usage, "cache_creation_input_tokens", None) or 0
                    self.metrics.cache_read_tokens += cache_read
                    if cache_read or cache_create:
                        logger.debug(f"Cache tokens — read: {cache_read}, created: {cache_create}")

                    # Parse tool use result
                    tool_block = next(
                        (b for b in response.content if b.type == "tool_use"), None
                    )
                    if tool_block:
                        data = tool_block.input
                        article.relevant = data["relevant"]
                        article.category = ArticleCategory(data.get("category", "other"))
                        article.tags = data.get("tags", [])
                        article.summary = data.get("summary", "")
                        article.location = GeoLocation(
                            place_name=data.get("place_name", ""),
                            country_iso=data.get("country_iso", ""),
                        )
                    return article

                except anthropic.RateLimitError:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited, retrying in {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                except anthropic.APIError as e:
                    if attempt == CLAUDE_MAX_RETRIES - 1:
                        logger.error(f"Claude API error classifying '{article.title}': {e}")
                        # Try fallback model on last attempt
                        if self._model != CLAUDE_MODEL_FALLBACK:
                            self._model = CLAUDE_MODEL_FALLBACK
                            logger.info(f"Switching to fallback model: {CLAUDE_MODEL_FALLBACK}")
                            return await self.classify_article(article)
                        return article
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Unexpected error classifying '{article.title}': {e}")
                    return article

        return article

    async def classify_signal(self, signal: CompanySignal) -> CompanySignal:
        """Classify a single company signal."""
        async with self.semaphore:
            for attempt in range(CLAUDE_MAX_RETRIES):
                try:
                    response = await self.client.messages.create(
                        model=self._model,
                        max_tokens=384,
                        system=[{
                            "type": "text",
                            "text": SIGNAL_SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }],
                        messages=[{
                            "role": "user",
                            "content": (
                                f"Company: {signal.company_name}\n"
                                f"Headline: {signal.title}\n"
                                f"Source: {signal.source_name}"
                            ),
                        }],
                        tools=[CLASSIFY_SIGNAL_TOOL],
                        tool_choice={"type": "tool", "name": "classify_signal"},
                    )

                    self.metrics.input_tokens += response.usage.input_tokens
                    self.metrics.output_tokens += response.usage.output_tokens
                    cache_read = getattr(response.usage, "cache_read_input_tokens", None) or 0
                    cache_create = getattr(response.usage, "cache_creation_input_tokens", None) or 0
                    self.metrics.cache_read_tokens += cache_read
                    if cache_read or cache_create:
                        logger.debug(f"Signal cache tokens — read: {cache_read}, created: {cache_create}")

                    tool_block = next(
                        (b for b in response.content if b.type == "tool_use"), None
                    )
                    if tool_block:
                        data = tool_block.input
                        signal.signal_type = SignalType(data.get("signal_type", "other"))
                        signal.summary = data.get("summary", "")
                        signal.location = GeoLocation(
                            place_name=data.get("place_name", ""),
                            country_iso=data.get("country_iso", ""),
                        )
                        if not data.get("relevant", False):
                            signal.signal_type = None  # Mark as irrelevant
                    return signal

                except anthropic.RateLimitError:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited, retrying in {wait}s")
                    await asyncio.sleep(wait)
                except Exception as e:
                    logger.error(f"Error classifying signal '{signal.title}': {e}")
                    return signal

        return signal

    async def _classify_article_batch_chunk(self, chunk: list[Article]) -> list[Article]:
        """Classify a chunk of articles in a single API call."""
        if len(chunk) == 1:
            return [await self.classify_article(chunk[0])]

        # Build the batched user message
        parts = []
        for i, article in enumerate(chunk):
            parts.append(f"--- ARTICLE {i} ---\nTitle: {article.title}\n\nContent:\n{article.raw_content[:2000]}")
        user_msg = "\n\n".join(parts)

        async with self.semaphore:
            for attempt in range(CLAUDE_MAX_RETRIES):
                try:
                    response = await self.client.messages.create(
                        model=self._model,
                        max_tokens=512 * len(chunk),
                        system=[{
                            "type": "text",
                            "text": ARTICLE_SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }],
                        messages=[{
                            "role": "user",
                            "content": f"Classify each of the following {len(chunk)} articles. Return one result per article.\n\n{user_msg}",
                        }],
                        tools=[CLASSIFY_ARTICLE_BATCH_TOOL],
                        tool_choice={"type": "tool", "name": "classify_articles_batch"},
                    )

                    self.metrics.input_tokens += response.usage.input_tokens
                    self.metrics.output_tokens += response.usage.output_tokens
                    cache_read = getattr(response.usage, "cache_read_input_tokens", None) or 0
                    cache_create = getattr(response.usage, "cache_creation_input_tokens", None) or 0
                    self.metrics.cache_read_tokens += cache_read
                    if cache_read or cache_create:
                        logger.debug(f"Cache tokens — read: {cache_read}, created: {cache_create}")

                    tool_block = next(
                        (b for b in response.content if b.type == "tool_use"), None
                    )
                    if tool_block and "results" in tool_block.input:
                        for result in tool_block.input["results"]:
                            idx = result.get("article_index", -1)
                            if 0 <= idx < len(chunk):
                                article = chunk[idx]
                                article.relevant = result["relevant"]
                                article.category = ArticleCategory(result.get("category", "other"))
                                article.tags = result.get("tags", [])
                                article.summary = result.get("summary", "")
                                article.location = GeoLocation(
                                    place_name=result.get("place_name", ""),
                                    country_iso=result.get("country_iso", ""),
                                )
                    return chunk

                except anthropic.RateLimitError:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited on batch, retrying in {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                except anthropic.APIError as e:
                    if attempt == CLAUDE_MAX_RETRIES - 1:
                        logger.error(f"Claude API error on batch: {e}")
                        # Fallback: classify individually
                        logger.info("Falling back to individual classification for this batch")
                        tasks = [self.classify_article(a) for a in chunk]
                        return list(await asyncio.gather(*tasks))
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Unexpected error on batch: {e}")
                    # Fallback to individual classification
                    tasks = [self.classify_article(a) for a in chunk]
                    return list(await asyncio.gather(*tasks))

        return chunk

    async def classify_articles_batch(self, articles: list[Article]) -> list[Article]:
        """Classify multiple articles using batched API calls.

        Groups articles into chunks of ARTICLE_BATCH_SIZE and sends each
        chunk as a single API call, drastically reducing total calls.
        """
        if not articles:
            return []

        # Split into chunks
        chunks = [
            articles[i:i + ARTICLE_BATCH_SIZE]
            for i in range(0, len(articles), ARTICLE_BATCH_SIZE)
        ]
        logger.info(f"Classifying {len(articles)} articles in {len(chunks)} batches of up to {ARTICLE_BATCH_SIZE}")

        # Process chunks concurrently (limited by semaphore)
        tasks = [self._classify_article_batch_chunk(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)

        # Flatten
        classified = []
        for chunk_result in results:
            classified.extend(chunk_result)
        return classified

    async def classify_signals_batch(self, signals: list[CompanySignal]) -> list[CompanySignal]:
        """Classify multiple signals concurrently."""
        tasks = [self.classify_signal(s) for s in signals]
        return await asyncio.gather(*tasks)

    async def classify_publication(self, pub: Publication) -> Publication:
        """Classify a single publication for field crop relevance + translate."""
        async with self.semaphore:
            for attempt in range(CLAUDE_MAX_RETRIES):
                try:
                    user_content = f"Title: {pub.title}"
                    if pub.summary:
                        user_content += f"\nSummary: {pub.summary}"
                    user_content += f"\nSource: {pub.source_name} ({pub.country})"

                    response = await self.client.messages.create(
                        model=self._model,
                        max_tokens=256,
                        system=[{
                            "type": "text",
                            "text": PUBLICATION_SYSTEM_PROMPT,
                        }],
                        messages=[{"role": "user", "content": user_content}],
                        tools=[CLASSIFY_PUBLICATION_TOOL],
                        tool_choice={"type": "tool", "name": "classify_publication"},
                    )

                    self.metrics.input_tokens += response.usage.input_tokens
                    self.metrics.output_tokens += response.usage.output_tokens

                    tool_block = next(
                        (b for b in response.content if b.type == "tool_use"), None
                    )
                    if tool_block:
                        data = tool_block.input
                        pub.relevant = data.get("relevant", True)
                        title_en = data.get("title_en", "")
                        summary_en = data.get("summary_en", "")
                        if title_en:
                            pub.title = title_en
                        if summary_en:
                            pub.summary = summary_en
                        if not pub.relevant:
                            logger.info(f"Publication filtered: {pub.title[:80]}")
                    return pub

                except anthropic.RateLimitError:
                    if attempt < CLAUDE_MAX_RETRIES - 1:
                        wait = 2 ** attempt
                        logger.warning(f"Rate limited on publication, retrying in {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"Rate limit exhausted for publication: {pub.title[:60]}")
                        return pub
                except Exception as e:
                    logger.error(f"Publication classification error: {e}")
                    return pub
        return pub

    async def classify_publications_batch(self, publications: list[Publication]) -> list[Publication]:
        """Classify multiple publications concurrently."""
        if not publications:
            return publications
        logger.info(f"Classifying {len(publications)} publications")
        tasks = [self.classify_publication(p) for p in publications]
        return await asyncio.gather(*tasks)

    async def translate_non_english(
        self,
        publications: list[Publication],
        articles: Optional[list[Article]] = None,
        signals: Optional[list[CompanySignal]] = None,
    ) -> None:
        """Translate non-English titles and summaries to English in-place.

        Batches all non-English items into a single API call for efficiency.
        Modifies items in-place (title, summary fields).
        """
        # Collect items needing translation
        # NOTE: publications are already translated by classify_publication(), skip them here
        items_to_translate: list[dict] = []
        if articles:
            for i, art in enumerate(articles):
                if art.original_language != "en" and art.title:
                    items_to_translate.append({
                        "index": i,
                        "type": "article",
                        "title": art.title,
                        "language": art.original_language,
                    })
        if signals:
            for i, sig in enumerate(signals):
                if sig.original_language != "en" and sig.title:
                    items_to_translate.append({
                        "index": i,
                        "type": "signal",
                        "title": sig.title,
                        "language": sig.original_language,
                    })

        if not items_to_translate:
            logger.info("No non-English items to translate")
            return

        logger.info(f"Translating {len(items_to_translate)} non-English items to English")

        # Build a single prompt with all items
        lines = []
        for idx, item in enumerate(items_to_translate):
            lines.append(f"--- ITEM {idx} ---")
            lines.append(f"Title: {item['title']}")
            if item.get("summary"):
                lines.append(f"Summary: {item['summary']}")
            lines.append("")

        user_msg = "\n".join(lines)

        translate_tool = {
            "name": "translate_items",
            "description": "Return English translations for non-English agricultural content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "translations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_index": {"type": "integer"},
                                "title_en": {"type": "string", "description": "English translation of the title."},
                                "summary_en": {"type": "string", "description": "English translation of the summary. Empty string if no summary provided."},
                            },
                            "required": ["item_index", "title_en", "summary_en"],
                        },
                    },
                },
                "required": ["translations"],
            },
        }

        async with self.semaphore:
            try:
                # ~60-80 tokens per translated item; pad generously to avoid truncation
                translation_max_tokens = max(2048, len(items_to_translate) * 100)
                response = await self.client.messages.create(
                    model=self._model,
                    max_tokens=translation_max_tokens,
                    system=[{
                        "type": "text",
                        "text": (
                            "You are a professional translator for agricultural news. "
                            "Translate each item's title and summary to clear, concise English. "
                            "Keep agricultural terminology precise (crop names, technical terms). "
                            "Do not add or remove information — translate faithfully."
                        ),
                    }],
                    messages=[{
                        "role": "user",
                        "content": f"Translate the following {len(items_to_translate)} items to English:\n\n{user_msg}",
                    }],
                    tools=[translate_tool],
                    tool_choice={"type": "tool", "name": "translate_items"},
                )

                self.metrics.input_tokens += response.usage.input_tokens
                self.metrics.output_tokens += response.usage.output_tokens

                tool_block = next(
                    (b for b in response.content if b.type == "tool_use"), None
                )
                if tool_block and "translations" in tool_block.input:
                    for t in tool_block.input["translations"]:
                        idx = t.get("item_index", -1)
                        if 0 <= idx < len(items_to_translate):
                            item = items_to_translate[idx]
                            original_idx = item["index"]
                            title_en = t.get("title_en", "")
                            summary_en = t.get("summary_en", "")

                            if item["type"] == "article" and title_en and articles:
                                articles[original_idx].title = title_en
                                logger.debug(f"Translated article title: {item['title'][:50]} → {title_en[:50]}")
                            elif item["type"] == "signal" and title_en and signals:
                                signals[original_idx].title = title_en
                                logger.debug(f"Translated signal title: {item['title'][:50]} → {title_en[:50]}")

                    translated_count = len(tool_block.input['translations'])
                    if translated_count < len(items_to_translate):
                        logger.warning(
                            f"Translation gap: got {translated_count}/{len(items_to_translate)} items. "
                            f"stop_reason={response.stop_reason}"
                        )
                    logger.info(f"Translated {translated_count}/{len(items_to_translate)} items")

            except Exception as e:
                logger.error(f"Translation failed (non-fatal, keeping originals): {e}")
