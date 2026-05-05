# Daily Agri-News Digest — Upgrade Strategy v2

**Date:** 2026-03-23
**Author:** Rémi + Claude
**Status:** Draft for review

---

## Current State Summary

| Dimension | Today | Target |
|-----------|-------|--------|
| Codebase | Single 2,400-line monolithic script | Modular, config-driven |
| LLM | Mistral AI (free via Pro sub) | Claude Haiku 4.5 (~$15/mo) |
| Countries | 11 (FR, ES, HU, DE, CA, RO, TR, UK, IT, UA, EU) | 36 countries across 5 continents |
| Sources | ~15 RSS + 10 Selenium scrapers, hardcoded | Notion-managed, expandable |
| Recipients | Comma-separated env var | Notion-managed |
| Company tracking | None | 9 clients + 14 prospects |
| Email delivery | Gmail SMTP (BCC, generic sender) | Lemlist API from `remi@hyperplan.fr` |
| Geolocation | None | Lat/long per article for future map integration |
| Design | Basic HTML, white bg, green accents | Hyperplan-branded, dark navy cards |
| Admin | Requires GitHub access | Notion databases — no code needed |
| Runtime | GitHub Actions (daily 06:30 UTC) | GitHub Actions (unchanged) |
| Repo | `RemiBanquet/newsletter` (private, 645 commits) | Same repo, modular structure |

---

## 1 · LLM Migration: Mistral → Claude

### Cost reality

Going from $0/month (Mistral Pro) to paid API. Non-negotiable trade-off for getting Claude's better instruction-following and structured output.

| Scenario | Monthly cost |
|----------|-------------|
| Naive (no optimization) | ~$23 |
| **With optimizations (recommended)** | **$8–15** (estimate — validate after Week 1) |

**Key optimizations:** Prompt caching (system prompt reused across all calls in a run, ~30% savings), keyword pre-filter before LLM (~40% fewer calls), compressed prompt with structured output (~50% smaller), concurrent processing for company signals.

**Caveat:** The $8-15 estimate is based on the current 11-country scope (~80-200 articles/day). With 36 countries + 23 company signal feeds, raw volume could reach 300-400 articles/day. The keyword pre-filter and dedup should keep LLM calls manageable, but the real cost won't be known until the first production week. Plan to review after Week 1 and adjust pre-filter aggressiveness if needed.

### Model choice

| Use case | Model | Why |
|----------|-------|-----|
| Article classify + summarize | `claude-haiku-4-5-20251001` | Fast, cheap, accurate for structured extraction |
| Prospect sales angle (Phase 2) | `claude-sonnet-4-6` | Needs reasoning for sales positioning |
| Fallback | `claude-sonnet-4-6` | Only if Haiku unavailable |

### Structured output schema (via tool-use)

Claude returns a structured JSON for every article. This replaces the fragile regex parsing of Mistral's free-text responses.

```json
{
  "relevant": true,
  "category": "crop_production",
  "tags": ["wheat", "drought"],
  "summary": "...",
  "location": {
    "place_name": "Andalusia, Spain",
    "country_iso": "ES"
  }
}
```

Claude returns a `place_name` (most specific location mentioned: city > region > country) and `country_iso`. A separate geocoding step (Nominatim/OpenStreetMap) then resolves the place name to lat/long coordinates. These are stored alongside the article — Claude does NOT return coordinates itself.

**Why geolocation matters:** Every article gets stored with coordinates, enabling future map visualization inside Hyperplan's platform. Publications default to country centroid since they're national-level data.

**Cost impact:** Near-zero. The location extraction is 2-3 extra output tokens per article — Claude is already reading the full text anyway. Nominatim calls are free and fast.

### Language handling

The current script uses `langdetect` + `deep-translator` to detect and translate non-English articles before classification. With 36 countries producing content in 15+ languages, this adds latency, cost, and translation noise.

**New approach:** Feed Claude the original-language text directly. Claude is natively multilingual and can classify, extract location, and summarize in English from French, German, Spanish, Turkish, Hungarian, etc. — without translation artifacts.

**What we drop:** `deep-translator` dependency, Google Translate API calls, the `translate_text()` function. **What we keep:** `langdetect` for metadata tagging (useful to know an article was originally in Turkish).

### Performance: parallelization and batch processing

The current script runs sequentially. With expanded scope (36 countries, 23 companies (9 clients + 14 prospects), geocoding), sequential execution would push runtime past 40 minutes. Strategy:

1. **Parallel source fetching:** Use `asyncio` + `aiohttp` for RSS feeds and API calls. Selenium scrapers remain sequential (browser constraint) but run in a separate thread pool.
2. **Concurrent LLM calls:** Fire 5-10 Claude classification requests in parallel using asyncio (Anthropic's API supports concurrent requests within rate limits). Note: the Anthropic Batch API is async/delayed and doesn't fit a real-time pipeline — we use standard concurrent Messages API calls instead.
3. **Geocoding cache:** SQLite lookup table for previously resolved locations. "France" → `(46.23, 2.21)` is resolved once, cached forever. Only novel locations hit Nominatim.
4. **Parallel Notion writes:** Archive articles to Notion in batches using async calls (Notion API supports 3 req/sec).
5. **Target runtime:** Under 20 minutes for the full pipeline, down from an estimated 40+ sequential.

### Migration steps

1. Replace `mistralai` SDK with `anthropic` SDK
2. Drop `deep-translator` — feed Claude original-language text, keep `langdetect` for metadata
3. Restructure prompt: `system` message (persona + rules, cached) + `user` message (article text only)
4. Use tool-use for structured output (guaranteed schema with geolocation, no regex parsing)
5. Add geocoding step: Claude extracts place name → Nominatim resolves to lat/long → cached in SQLite
6. Implement async pipeline: parallel fetching, concurrent LLM calls, cached geocoding, batched Notion writes
7. Adapt existing `RateLimiter` class for Anthropic rate limits
8. Parallel test for a few days, then cut over

---

## 2 · Email Delivery: Gmail SMTP → Lemlist API

### Why switch

- **Professional sender:** `remi@hyperplan.fr` instead of a Gmail address
- **Better deliverability:** Lemlist handles DKIM, SPF, warming through Microsoft 365
- **Simpler secrets:** Replace 4 Gmail secrets with 1 Lemlist API key
- **Consistent with other Hyperplan automations:** Same pattern as the Claap Daily Brief

### Lemlist account details

| Parameter | Value |
|-----------|-------|
| User ID | `usr_FYxnRkvNuRFSixkrP` |
| Email | `remi@hyperplan.fr` |
| Mailbox ID | `usm_RjWktLTma7TthAcj8` |
| Provider | Microsoft 365 |
| Team | Hyperplan (`tea_t7fEmvxvuKvejfaeF`) |

### How it works from GitHub Actions

Same pattern as the Claap Daily Brief: a dummy Lemlist campaign holds leads, the script sends via the inbox API using `leadId`. The campaign sequence is a placeholder — never activated.

**One-time setup (before first run):**

1. Create two Lemlist campaigns: `"Internal – Daily Agri-News Digest"` and `"External – Daily Agri-News Digest"`
2. Add each recipient as a lead in the appropriate campaign
3. Store the primary lead IDs in the Notion Recipients database

**Reference — Claap Daily Brief pattern:**

| Item | Value |
|------|-------|
| Campaign | `cam_vesvxSiWGGsppevqZ` ("Internal – Claap Daily Brief") |
| Lead | `lea_SdYLyJQJfFNhiev9u` (remi@hyperplan.fr) |
| Sequence step | Placeholder — `"do not activate"` |

```python
def send_newsletter_via_lemlist(subject: str, html_body: str, recipient_groups: list[dict]):
    """
    Send newsletter via Lemlist API (inbox send).
    Two separate emails: internal (Hyperplan) and external (Demeter IM).
    Each group: {"lead_id": "lea_xxx", "cc": ["email", ...]}
    """
    api_key = os.getenv("LEMLIST_API_KEY")
    results = []

    for group in recipient_groups:
        response = requests.post(
            "https://api.lemlist.com/api/inbox/send",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "sendUserId": "usr_FYxnRkvNuRFSixkrP",
                "sendUserEmail": "remi@hyperplan.fr",
                "sendUserMailboxId": "usm_RjWktLTma7TthAcj8",
                "leadId": group["lead_id"],
                "subject": subject,
                "message": html_body,
                "cc": group.get("cc", [])
            }
        )
        results.append(response.ok)
        logging.info(f"Lemlist send to {group['name']}: {response.status_code}")

    return all(results)
```

### Recipient groups

Two separate sends to keep internal and external recipients isolated:

| Group | Primary lead (To:) | CC | Lemlist campaign |
|-------|-------------------|-----|-----------------|
| **Internal** (Hyperplan) | `remi@hyperplan.fr` | `victoria@`, `clemence@`, `thibault@`, `victor@`, `ruben@`, `jb@`, `matthieu@`, `guillaume@` (all `@hyperplan.fr`) | `Internal – Daily Agri-News Digest` |
| **External** (Demeter IM) | `thomas.beaugendre@demeter-im.com` | `geoffroy.dubus@demeter-im.com` | `External – Daily Agri-News Digest` |

### Notion "Recipients" database schema

Each recipient row includes a **Group** select property (`internal` / `external`) and a **Lead ID** field (for the primary recipient of each group). The script reads these at run start to build the two sends automatically. Adding a new CC recipient = add a Notion row. Changing the primary recipient = update the Lead ID.

### GitHub Secrets change

| Remove | Add |
|--------|-----|
| `GMAIL_SMTP_SERVER` | `LEMLIST_API_KEY` |
| `GMAIL_SMTP_PORT` | |
| `GMAIL_USERNAME` | |
| `GMAIL_PASSWORD` | |

---

## 3 · Admin Interface via Notion

All evolving configuration lives in Notion databases. The script reads config at the start of each run.

### Database 1: "Newsletter Sources"

Replaces `FEED_URLS` env var + hardcoded `OFFICIAL_SOURCES` dict.

| Property | Type | Purpose |
|----------|------|---------|
| Name | Title | Human-readable source name (e.g., "USDA NASS News") |
| URL | URL | RSS feed URL or scraper target |
| Source Category | Select | `media` / `official_publication` |
| Type | Select | `rss` / `selenium-scraper` / `requests-scraper` / `api` |
| Country | Select | Country or region for grouping |
| Enabled | Checkbox | Toggle on/off |
| Keywords Filter | Multi-select | Override global crop keywords |
| Scraper ID | Rich text | Python function name for custom scrapers |
| Notes | Rich text | Admin context |

**Source types explained:**

| Type | How it works | Speed | When to use |
|------|-------------|-------|-------------|
| `rss` | Fetches and parses RSS/Atom feeds via `feedparser` | ~0.3s | Any site with an RSS feed |
| `requests-scraper` | Downloads raw HTML via `requests`, parses with BeautifulSoup | ~0.5s | Static sites where data is in the initial HTML |
| `selenium-scraper` | Launches Chrome, waits for JS to render, then scrapes | ~5-15s | Sites where content is loaded dynamically by JavaScript (React, Vue, etc.) |
| `api` | Calls a structured API endpoint (e.g., USDA FAS PSD) | ~1-2s | Data sources with public APIs |

**Selection rule:** Always try `requests-scraper` first (view page source — if the data is there, use requests). Only use `selenium-scraper` when the content doesn't exist in the raw HTML. Some of the current 10 Selenium scrapers may be migrateable to requests for speed gains.

### Database 2: "Newsletter Recipients"

Replaces `EMAIL_RECIPIENTS` env var.

| Property | Type | Purpose |
|----------|------|---------|
| Name | Title | Recipient name |
| Email | Email | Delivery address |
| Active | Checkbox | Toggle on/off |
| Group | Select | `internal` / `external` — determines which send batch |
| Is Primary | Checkbox | If checked, this recipient's Lead ID is used as the `To:` address for the group |
| Lead ID | Rich text | Lemlist lead ID (`lea_xxx`) — only needed for primary recipients |
| Role | Select | `subscriber` / `admin` |

### Database 3: "Tracked Companies"

New — powers client + prospect signal sections.

| Property | Type | Purpose |
|----------|------|---------|
| Company Name | Title | Official name |
| Type | Select | `client` / `prospect` |
| Search Keywords | Rich text | Search terms (e.g., "BASF crop science, BASF agriculture") |
| Enabled | Checkbox | Toggle tracking |
| Priority | Select | `high` / `medium` / `low` |
| Notes | Rich text | Context for sales team |

### Database 4: "Countries of Interest"

New — drives which publication scrapers and regional sources are active.

| Property | Type | Purpose |
|----------|------|---------|
| Country | Title | Country name |
| Region | Select | `EU` / `Americas` / `Africa-ME` / `Asia-Pacific` |
| Flag Emoji | Rich text | 🇫🇷, 🇩🇪, etc. |
| Enabled | Checkbox | Toggle on/off |
| Has Scraper | Checkbox | Whether a custom scraper exists |
| Pan-regional Source | Multi-select | `eurostat` / `usda_fas` / `fao` / `jrc_mars` |
| Notes | Rich text | Data availability notes |

### Fallback strategy

Last-good config cached as YAML in the repo. If Notion API is down, script falls back to cached config and logs a warning.

---

## 4 · Country Expansion: 11 → 36

### The smart approach: pan-regional sources first

Building 26 new individual scrapers is unrealistic and fragile. Instead, lean on pan-regional sources that cover many countries at once, and only build individual scrapers where national publications add unique value.

### Pan-regional coverage matrix

| Source | Countries covered | Method | Already have? |
|--------|-------------------|--------|---------------|
| **Eurostat** | All 27 EU members + candidates | RSS + requests/bs4 | Partial (2 RSS feeds) |
| **JRC MARS** | All Europe (crop monitoring bulletins) | Selenium scraper | ✅ Yes |
| **COCERAL** | EU (crop forecasts) | Selenium scraper | ✅ Yes |
| **USDA FAS PSD** | Global major producers (US, BR, AR, MX, IN, ID, NZ, EG, MA, ZA...) | API (no key needed) | ❌ No |
| **FAO** | Global | RSS + API | Partial (RSS) |

### What this means for the 26 new countries

**Covered by Eurostat + JRC MARS (no new scraper needed):**
Bulgaria, Czechia, Denmark, Finland, Latvia, Lithuania, Poland, Slovakia, Sweden, Austria, Belgium, Croatia, Estonia, Ireland, Netherlands, Portugal

→ That's **16 out of 26** new countries handled by sources we already have or can easily extend. Just need to ensure Eurostat RSS captures their publications.

**Covered by USDA FAS PSD API (new, but one integration covers many):**
USA, Argentina, Brazil, Mexico, India, Indonesia, New Zealand, Morocco, Egypt, South Africa

→ That's the remaining **10 countries** via a single API integration.

**Individual scrapers worth building (high-value national data):**

| Country | Source | Why worth it | Method |
|---------|--------|-------------|--------|
| USA | USDA NASS | Most detailed crop stats globally; has RSS | RSS (easy) |
| Brazil | IBGE / CONAB | Major producer, national data richer than USDA FAS | requests+bs4 |
| Argentina | INDEC / MinAgri | Major producer | requests+bs4 |
| India | Min. of Agriculture | Huge market, unique crop data | requests+bs4 |
| South Africa | Stats SA | Key Hyperplan market | requests+bs4 |
| Morocco | HCP | Growing market | Selenium (JS-rendered) |

**Priority tiers for scraper development:**
- **Tier 1 (Phase 1):** USDA FAS PSD API + enhanced Eurostat. Covers all 36 countries.
- **Tier 2 (Phase 2):** USA NASS RSS, Brazil CONAB, South Africa Stats SA.
- **Tier 3 (Phase 3+):** Argentina, India, Morocco, others as needed.

### Crop scope expansion

The current keyword filter covers European/North American field crops. With 36 countries spanning tropical, subtropical, and temperate zones, the crop list must expand to match.

**Current keywords (11 countries):**
`alfalfa, beans, beet, hemp, linen, maize/corn, cereals, peas, potato, soy, sorghum, barley, wheat, sunflower, triticale, canola/WOSR, meadows, grains, oilseeds`

**Expanded keywords (36 countries) — biggest-acreage field crops only:**

| Category | Added | Global acreage |
|----------|-------|----------------|
| **Rice** | rice, paddy | ~165M ha (India, Indonesia, Egypt, EU Med) |
| **Cotton** | cotton | ~35M ha (India, Turkey, Brazil, USA) |
| **Sugarcane** | sugarcane, sugar cane | ~26M ha (Brazil, India) |
| **Millet** | millet | ~30M ha (India, Africa) |
| **Pulses (expanded)** | chickpea, lentil | ~15M ha combined (India, Turkey, Canada) |
| **Groundnut** | groundnut, peanut | ~25M ha (India, Argentina) |

**Full CROP_KEYWORDS list for v5:**
```python
CROP_KEYWORDS = [
    # Cereals (~700M ha)
    "wheat", "barley", "maize", "corn", "rice", "paddy", "sorghum",
    "millet", "oats", "rye", "triticale", "cereals", "grains",
    # Oilseeds (~300M ha)
    "soy", "soybean", "sunflower", "canola", "rapeseed", "WOSR",
    "groundnut", "peanut", "oilseeds", "linseed",
    # Sugar (~50M ha)
    "sugarcane", "sugar cane", "sugar beet",
    # Cotton (~35M ha)
    "cotton",
    # Pulses (~90M ha)
    "beans", "peas", "chickpea", "lentil", "pulses",
    # Root / tuber
    "potato", "beet",
    # Beverages (client-relevant)
    "coffee",
    # Forage / pasture
    "alfalfa", "meadows", "pasture", "forage",
    # Generic
    "field crops", "crop", "harvest", "planted", "acreage", "yield",
]
```

**Note:** This list is stored in the Notion "Newsletter Sources" config as a global default. Individual sources can override with narrower keywords via the `Keywords Filter` property. The keyword filter is a pre-screen — Claude makes the final relevance call.

---

## 5 · Client & Prospect Market Signals

### Companies to track

**Clients (9):**
Corteva, Syngenta, Bayer, BASF, Certis Belchim, Adama, KWS, Lidea, Saipol

**Prospects (14):**
Sumitomo Chemical, Nutrien, Nufarm, GDM, CF Industries, CHS Inc, Yara International, OCP Nutricrops, The Mosaic Company, UPL, FMC Corporation, ICL Group, Timac Agro, Richardson International

### Signal pipeline

```
For each tracked company:
  1. Generate Google News RSS URL from search keywords
  2. Fetch RSS → filter to last 7 days (vs 24h for articles/publications)
  3. Keyword pre-filter (must mention ag/crop/input terms)
  4. Claude Haiku: classify relevance + signal type + 2-bullet summary
  5. Dedupe against sent_signals.json
  6. Group by company → render in email section
```

**Why 7-day lookback:** Company signals are less frequent than general ag news. A 24h window would miss important developments that happen over weekends or slow news days. A 7-day rolling window ensures we catch everything, with dedup preventing repeats across daily digests.

### Volume estimate

23 companies (9 clients + 14 prospects) × ~10-15 results/week = ~250-350 raw items per run (7-day window) → after dedup against previously sent signals, only new items remain (~20-40/day). After keyword filter + LLM classification → ~15-30 relevant signals per digest. Manageable.

### Email section format

```
🏢 Client Intelligence

  BASF
  • [💸 Market] BASF raises 2026 crop protection revenue guidance by 8%
    after strong European herbicide sales. (Reuters, 22 Mar)
  • [🚀 AgTech] BASF partners with Xarvio for satellite-based disease
    prediction in digital farming suite. (AgFunder, 21 Mar)

🎯 Prospect Intelligence

  Yara International
  • [⚖️ Regulation] Yara secures EU approval for new biostimulant range
    targeting cereal crops. (Euractiv, 22 Mar)
```

---

## 6 · Newsletter Design Overhaul

### Design system (from Hyperplan Gamma theme)

| Element | Current | New |
|---------|---------|-----|
| Primary accent | `#69BE82` (Hyperplan green) | `#69BE82` (Hyperplan green — kept) |
| Text color | `#14213D` (navy) on white bg | `#E5E0DF` (warm gray) on navy cards |
| Card backgrounds | None (flat white) | `#14213D` (dark navy) |
| Page background | White | Light warm gray or subtle map texture |
| Headings font | Aptos / Segoe UI | **Montserrat Bold** (white on navy) |
| Body font | Aptos / Segoe UI | **Source Sans 3** Regular |
| Links | `#69BE82` | `#69BE82` |
| Buttons/CTAs | None | `#69BE82` with white text |

### Email client constraints

Web fonts (Montserrat, Source Sans 3) only render in Apple Mail, iOS Mail, and some Outlook versions. Fallback chain required:

```css
/* Headings */
font-family: 'Montserrat', 'Segoe UI', Arial, sans-serif;
/* Body */
font-family: 'Source Sans 3', 'Source Sans Pro', 'Segoe UI', Tahoma, sans-serif;
```

Import via `@import url()` in `<style>` block for clients that support it; degrades gracefully.

### New email structure

```
┌──────────────────────────────────────────┐
│  [Hyperplan banner - green on navy]       │
│  🛰️ Daily Agri-News Digest 🌱             │
│  23 Mar 2026                              │
├──────────────────────────────────────────┤
│                                           │
│  📌 AT A GLANCE (structured summary)      │
│  ┌─────────────────────────────────────┐  │
│  │ Today: 6 publications · 42 articles │  │
│  │        · 34 company signals         │  │
│  │                                     │  │
│  │ Jump to:                            │  │
│  │  📋 Publications (6) ─────── ↓      │  │
│  │  🌍 News (42) ───────────── ↓      │  │
│  │    📈 Yields (8)                    │  │
│  │    🌾 Crop land use (12)            │  │
│  │    🚜 AgTech (6)                    │  │
│  │    ⚖️ Regulation (5)                │  │
│  │    🌦️ Climate & weather (7)         │  │
│  │    💸 Markets (4)                    │  │
│  │  🏢 Client Intel (18) ──── ↓       │  │
│  │    BASF (3) · Corteva (4) · ...     │  │
│  │  🎯 Prospect Intel (16) ── ↓       │  │
│  │    Yara (2) · UPL (3) · ...         │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  📋 SECTION 1: Official Publications      │
│  ┌─────────────────────────────────────┐  │
│  │ Navy card: 🇫🇷 France                │  │
│  │  • Agreste: Crop acreage update...  │  │
│  │ 🇪🇺 Europe                           │  │
│  │  • JRC MARS Bulletin - Crop mon...  │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  🌍 SECTION 2: Today's News               │
│  ┌─────────────────────────────────────┐  │
│  │ Navy card per article:              │  │
│  │ 📈 Yields | Source | 22 Mar 2026   │  │
│  │ USDA raises corn yield forecast...  │  │
│  │ • Bullet 1                          │  │
│  │ • Bullet 2                          │  │
│  │ • Bullet 3                          │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  🏢 SECTION 3: Client Intelligence        │
│  ┌─────────────────────────────────────┐  │
│  │ Grouped by company, signal cards    │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  🎯 SECTION 4: Prospect Intelligence      │
│  ┌─────────────────────────────────────┐  │
│  │ Grouped by company, signal cards    │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  Footer: Notion archive links             │
└──────────────────────────────────────────┘
```

### "At a Glance" navigation block

The digest opens with a structured summary that serves three purposes:

1. **Instant snapshot** — total counts per section so the reader knows the day's volume in 2 seconds
2. **Jump links** — anchor links (`#publications`, `#news`, `#client-intel`, `#prospect-intel`) for one-click navigation to any section. News sub-links jump to specific tag groups (`#tag-yields`, `#tag-agtech`, etc.)
3. **Company quick-nav** — within Client and Prospect Intelligence, company names are clickable anchors. A reader tracking BASF can jump directly to BASF's signals without scrolling past 40 news articles.

**Email client compatibility:** HTML anchor links (`<a href="#section-id">`) work in Gmail (web + mobile), Apple Mail, Outlook 365 web, and iOS Mail. They don't work in Outlook desktop (Windows) — readers on Outlook desktop will need to scroll, but the section headers are visually prominent enough for fast scanning.

### Design principles for the email

- **Dark navy cards** (`#14213D`) for each content block, with light text — creates the Gamma look
- **Green accent** (`#69BE82`) for links, tags, section headers, and navigation links
- **White section headers** on navy background
- **Card-based layout** — each article/signal is a self-contained card with rounded corners
- **Left green border** on cards for visual hierarchy (keep this from current design, it works)
- **Responsive** — single column, max-width 700px, scales to mobile
- **Sticky-friendly navigation** — "At a Glance" block uses a lighter background to visually separate it from content cards

---

## 7 · Notion Page Update

The existing Notion page (https://www.notion.so/Daily-Agri-News-Digest-21f5e97ecd7d8043be3ecdf661614b43) will be reworked to document the upgraded system:

### Proposed new structure

1. **Purpose** — (keep, update scope to include company signals)
2. **High-Level Workflow** — update mermaid diagram to show 4-section pipeline
3. **Configuration** — NEW: link to the 4 Notion config databases, explain how to add sources/recipients/companies/countries
4. **Data Inputs** — update table: pan-regional sources, RSS feeds, scrapers, company signal feeds
5. **Processing Pipeline** — update to reflect Claude, pre-filtering, signal pipeline
6. **Newsletter Sections** — NEW: describe all 4 sections with examples
7. **Countries of Interest** — NEW: full 36-country list with coverage source
8. **Tracked Companies** — NEW: client + prospect lists with signal types
9. **Error Handling** — update for Claude-specific handling
10. **Cost & Usage** — NEW: expected monthly API cost, how to monitor
11. **How to: Add a source / Add a recipient / Add a company / Add a country** — step-by-step guides
12. **Runtime & Scheduling** — keep, update
13. **Dependencies** — update (anthropic replaces mistralai)
14. **Glossary** — update with new terms (signal, prospect, client, etc.)

---

## 8 · Implementation Approach

### Upgrade strategy: build alongside, then swap

The existing `newsletterv4-6.py` continues running daily on GitHub Actions throughout development. The new version is built as a separate file (`newsletter_v5.py`) in the same repo. Once validated, we swap the workflow to point at the new file and retire the old one. Zero downtime, easy rollback.

### Phase 1: Foundation + Design (Week 1-4)
**Goal:** Claude migration + Notion config + Lemlist delivery + new email design + admin reporting. Keep existing country coverage.

**Note on scope:** This phase includes an async rewrite of the pipeline (moving from synchronous to asyncio). This is the single largest engineering effort — it affects fetching, LLM calls, Notion writes, error handling, and logging. Expect it to account for ~35% of Phase 1 time. The alternative (keep it synchronous, accept 40+ min runtime) is viable if timeline pressure requires it.

1. Create 4 Notion config databases, populate with current data (sources, recipients, companies, countries)
2. Build `config_from_notion.py`: reads all config from Notion at run start
3. Replace Mistral SDK → Anthropic SDK with prompt caching + structured output
4. Drop `deep-translator` — feed Claude original-language text, keep `langdetect` for metadata
5. Rewrite classification prompt for Claude (shorter, tool-use schema with geolocation extraction)
6. Add geocoding step (Nominatim/OSM) with SQLite cache for resolved locations
7. Implement async pipeline: parallel source fetching, concurrent LLM calls, batched Notion writes
8. Create Lemlist campaigns + leads for newsletter delivery (internal + external + admin)
9. Replace Gmail SMTP → Lemlist API for email delivery (from `remi@hyperplan.fr`)
10. Build new HTML email template with Hyperplan design system (dark navy cards, Montserrat, green accents)
11. Build admin reporting: success email with KPIs + failure alert with traceback
12. Build 3 pending colleague scrapers (Italy ISTAT agriculture, Spain MAPA avances, Turkey TUIK MEDAS)
13. Add USDA FAS PSD API integration (covers 10+ new countries in one shot)
14. Enhance Eurostat coverage (covers 16 new EU countries)
15. Add lat/long fields to Notion article archive database
16. Validate end-to-end → swap workflow to `newsletter_v5.py` → retire v4-6
17. Update Notion documentation page
18. Update GitHub Secrets: remove 4 Gmail secrets, add `ANTHROPIC_API_KEY` + `LEMLIST_API_KEY`
19. Update GitHub Actions workflow (`daily_run.yml`)

### Phase 2: Company Signals (Week 5-6)
**Goal:** Client + prospect intelligence in the daily email.

1. Populate "Tracked Companies" DB with 9 clients + 14 prospects
2. Build company signal pipeline (Google News RSS → pre-filter → Claude → dedupe)
3. Add `sent_signals.json` for deduplication
4. Add Client Intelligence + Prospect Intelligence sections to email
5. Add signal archiving to new Notion "Market Signals" database
6. Test with admin-only delivery → validate signal quality → roll out

### Phase 3: Country Depth + Hardening (Week 7-8)
**Goal:** Add priority individual scrapers, reliability, cost control.

1. Build Tier 2 scrapers: USA NASS RSS, Brazil CONAB, South Africa Stats SA
2. Add dry-run mode (`--dry-run`)
3. Add cost tracking: log tokens per run, alert on threshold
4. Add Notion config caching (YAML fallback)
5. Improve admin report: per-source health, per-country coverage
6. Keyword pre-filter optimization

### Phase 4: Refinements (Ongoing)
- Tier 3 scrapers (Argentina, India, Morocco) as Hyperplan expands
- Sales angle enrichment for prospect signals (Claude Sonnet)
- Website change detection for key prospects
- Slack delivery option
- Weekly digest mode for lower-priority signals
- **Map integration:** Expose geolocated articles via API or Notion for Hyperplan's map layer. All articles already have lat/long from Phase 1 — this is about building the consumption endpoint.

---

## 9 · Admin Reporting

Every run sends an admin email to `remi@hyperplan.fr` — on success AND on failure. This replaces the current silent-failure mode.

### On success — daily run report

| KPI | Example |
|-----|---------|
| Articles fetched | 187 |
| Articles accepted (relevant) | 42 |
| Articles rejected (irrelevant / duplicate) | 145 |
| Publications fetched | 18 |
| Publications accepted (new) | 6 |
| Publications rejected (duplicate) | 12 |
| Company signals fetched | 161 |
| Company signals accepted | 34 |
| Geocoding success rate | 95% (40/42) |
| Source errors (non-fatal) | 2 (Italy ISTAT timeout, Brazil CONAB 403) |
| Sources healthy / total | 43/45 |
| Claude API tokens used | 128k input / 12k output |
| Claude API cost (estimated) | $0.42 |
| Lemlist sends | 2/2 successful |
| Total runtime | 28 min |

### On failure — alert email

Sent immediately when the run crashes or when zero articles are delivered. Contains: error traceback, last successful step, which sources failed, and a direct link to the GitHub Actions run log.

### Implementation

The admin email uses a separate Lemlist send (or falls back to direct SMTP if Lemlist itself is the failure point). The admin lead in Lemlist is `remi@hyperplan.fr` in a dedicated `"Admin – Daily Agri-News Digest"` campaign.

---

## 10 · Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude API cost higher than estimated | Budget overrun | Pre-filter, prompt caching, Haiku, daily cost alerts |
| Notion API outage | No config = no run | YAML fallback cached in repo |
| Google News RSS rate limiting | Missing signals | Stagger requests, cache, user-agent rotation |
| Selenium scrapers breaking | Missing publications | Migrate to requests+bs4 where possible; health checks |
| Quality regression (Claude vs Mistral) | Misclassified articles | Parallel test period |
| 36-country scraper maintenance | High maintenance burden | Pan-regional sources first; individual scrapers only where essential |
| Email design breaks in Outlook | Ugly rendering | Test with Litmus/Email on Acid; inline all CSS; font fallbacks |
| GitHub Actions runtime | Exceeds timeout or burns free minutes | Async pipeline, geocoding cache, target <20 min |
| Lemlist API failure | Newsletter not delivered | Fallback to direct SMTP for admin alert; retry logic |
| Bus factor (Rémi only) | Digest dies if unavailable | **Deferred** — address after v5 is stable (move to org repo, document runbook) |

---

## 11 · Evolving Config Parameters (all in Notion)

| Parameter | Notion DB | Who can edit | Change frequency |
|-----------|-----------|-------------|-----------------|
| Email recipients | Recipients | Anyone with Notion access | Monthly |
| Media sources (RSS) | Sources | Rémi or product team | Monthly |
| Publication sources (scrapers) | Sources | Rémi (requires code for new scrapers) | Quarterly |
| Countries of interest | Countries | Anyone with Notion access | As Hyperplan expands |
| Clients | Tracked Companies | Sales team | Quarterly |
| Prospects | Tracked Companies | Sales team | Monthly |

**Key insight:** Most config changes (add recipient, toggle country, add prospect) require zero code. Only adding a new scraper for a specific national statistics office requires Python work — and even that is minimized by the pan-regional source strategy.

---

## Decisions Confirmed

| Decision | Answer |
|----------|--------|
| Budget for Claude API | ✅ Approved (~$15-25/month) |
| Company lists | ✅ 9 clients + 14 prospects provided |
| Notion workspace | ✅ Same workspace, update existing Notion page |
| Scraper health | ✅ All working, need to add more for new countries |
| Design reference | ✅ Hyperplan Gamma theme (dark navy + green) |
| Countries | ✅ 36 countries across EU, Americas, Africa-ME, Asia-Pacific |
| Email delivery | ✅ Lemlist API from `remi@hyperplan.fr`, lead/campaign pattern (like Claap Daily Brief) |
| Recipient groups | ✅ Two sends: internal (9 Hyperplan) + external (2 Demeter IM), both get full digest |
| External content | ✅ Investors see all sections including prospect intelligence |
| Runtime | ✅ Keep GitHub Actions (daily cron at 06:30 UTC) |
| Repo | ✅ `RemiBanquet/newsletter` on GitHub |
| Geolocation | ✅ Lat/long per article from Day 1 (Nominatim geocoding), for future Hyperplan map |
| Language | ✅ Feed Claude original text, drop `deep-translator`, keep `langdetect` for metadata |
| Performance | ✅ Async pipeline: parallel fetching, concurrent LLM, geocoding cache, target <20 min |
| Upgrade approach | ✅ Build `newsletter_v5.py` alongside existing script, swap when validated |
| Admin reporting | ✅ Success + failure emails to `remi@hyperplan.fr` with full KPIs |
| Bus factor | ⏸️ Deferred — address after v5 is stable |
