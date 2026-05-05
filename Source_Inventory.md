# Daily Agri-News Digest — Complete Source Inventory

**Date:** 2026-03-23
**Purpose:** Reference document for Notion "Sources" database population and scraper development planning.

---

## A · Current Article RSS Feeds (Media Sources)

These feeds provide crop-relevant news articles that go through the LLM classification pipeline.

### Agri-Tech & Innovation

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 1 | AgFunder News | `http://agfundernews.com/feed` | ✅ Active |
| 2 | TechCrunch AgTech | `https://techcrunch.com/tag/agtech/feed/` | ✅ Active |
| 3 | Precision Ag | `http://www.precisionag.com/feed/` | ✅ Active |
| 4 | AgWired | `http://agwired.com/feed/` | ✅ Active |
| 5 | Agribusiness Global | `https://www.agribusinessglobal.com/feed/` | ✅ Active |
| 6 | Future Farming | `https://www.futurefarming.com/feed/` | ✅ Active |

### Markets & Commodities

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 7 | Barchart Commodities | `https://www.barchart.com/news/rss/commodities` | ✅ Active |
| 8 | World-Grain | `https://www.world-grain.com/rss/articles` | ✅ Active |
| 9 | Grain Central — News | `https://www.graincentral.com/news/feed/` | ✅ Active |
| 10 | Grain Central — Markets | `https://www.graincentral.com/markets/feed/` | ✅ Active |
| 11 | Grain Central — Trade | `https://www.graincentral.com/trade/feed/` | ✅ Active |
| 12 | Investopedia Agriculture | `https://feeds-api.dotdashmeredith.com/v1/rss/google/77cb61c1-0387-45bf-80e6-3f2976e90672` | ✅ Active |

### Official / Institutional

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 13 | FAO Newsroom | `https://www.fao.org/feeds/fao-newsroom-rss` | ✅ Active |
| 14 | USDA NASS — News | `http://www.nass.usda.gov/rss/news.xml` | ✅ Active |
| 15 | USDA NASS — Reports | `http://www.nass.usda.gov/rss/reports.xml` | ✅ Active |
| 16 | JRC Agriculture News | `https://joint-research-centre.ec.europa.eu/node/2/rss_en?f%5B0%5D=oe_news_title%3Aagriculture&...` | ✅ Active |

### Regional — France

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 17 | Min. Agriculture FR | `http://agriculture.gouv.fr/rss.xml` | ✅ Active |
| 18 | Terre-net | `http://www.terre-net.fr/actualite-agriculture.html` | ✅ Active |
| 19 | La France Agricole | `http://www.lafranceagricole.fr/rss/actualites` | ✅ Active |

### Regional — Canada / North America

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 20 | Western Producer | `http://feeds.feedburner.com/westernproducer` | ✅ Active |
| 21 | Farms.com — Crop News | `https://www.farms.com/Portals/_default/RSS_Portal/News_Crop.xml` | ✅ Active |
| 22 | Farms.com — Featured Crop | `https://www.farms.com/Portals/_default/RSS_Portal/Featured_Crop.xml` | ✅ Active |

### Regional — Europe (Other)

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 23 | Agroes (Spain) | `https://www.agroes.es/cultivos-agricultura?format=feed&type=rss` | ✅ Active |
| 24 | Romania Insider | `http://www.romania-insider.com/feed/` | ✅ Active |
| 25 | SeedWorld Europe | `https://www.seedworld.com/europe/feed/` | ✅ Active |
| 26 | Farm Chemicals Intl | `http://www.farmchemicalsinternational.com/rss/rssfeed.php?pageid=2` | ✅ Active |

### Climate

| # | Source | RSS URL | Status |
|---|--------|---------|--------|
| 27 | Inside Climate News | `http://insideclimatenews.org/news/rss-teaser.xml` | ✅ Active |

**Total article RSS feeds: 27**

---

## B · Current Publication Sources

These provide official statistical bulletins and crop data publications.

### Publication RSS Feeds

| # | Country | Source | RSS URL | Status |
|---|---------|--------|---------|--------|
| 1 | Spain | MAPA | `https://www.mapa.gob.es/es/agricultura/noticiasRss.aspx` | ✅ Active |
| 2 | Hungary | KSH | `https://www.ksh.hu/apps/shop.rss_temakor?p_lang=EN&p_temakor_kod=KSH` | ✅ Active |
| 3 | Germany | Destatis | `http://www.destatis.de/Aktuelles.xml` | ✅ Active |
| 4 | Canada | StatCan | `https://www150.statcan.gc.ca/n1/rss/dai-quo/32-eng.atom` | ✅ Active |
| 5 | Romania | INSSE | `https://insse.ro/cms/files/rss_ins_en.xml` | ✅ Active |
| 6 | Italy | ISTAT | `https://www.istat.it/en/tema/agriculture/feed/` | ✅ Active |
| 7 | Europe | Eurostat — News | `https://ec.europa.eu/eurostat/...collection=CAT_EURNEW` | ✅ Active |
| 8 | Europe | Eurostat — Datasets | `https://ec.europa.eu/eurostat/...collection=dataset` | ✅ Active |

### Publication Scrapers (Selenium)

| # | Country | Source | Function | Target URL | Status |
|---|---------|--------|----------|------------|--------|
| 1 | France | Agreste | `scrape_agreste()` | `agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/...` | ✅ Active |
| 2 | Europe | JRC MARS | `scrape_jrc()` | `publications.jrc.ec.europa.eu/repository/search?...` | ✅ Active |
| 3 | Spain | CAA (Cooperativas) | `scrape_caa()` | `www.agro-alimentarias.coop/documents?cat_sel=33` | ✅ Active |
| 4 | Canada | StatCan | `scrape_statcan()` | `www150.statcan.gc.ca/.../crop_production` | ✅ Active |
| 5 | Turkey | TUIK | `scrape_tuik()` | `data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111` | ✅ Active |
| 6 | UK | DEFRA | `scrape_uk()` | `www.gov.uk/search/all?...` | ✅ Active |
| 7 | Hungary | KSH | `scrape_ksh()` | `www.ksh.hu/apps/shop.lista?p_lang=EN&p_temakor_kod=OM` | ✅ Active |
| 8 | Europe | COCERAL | `scrape_coceral()` | `www.coceral.com/web/coceral%20crop%20forecast/...` | ✅ Active |
| 9 | Germany | Destatis | `scrape_destatis()` | `www.destatis.de/SiteGlobals/Forms/Suche/EN/...` | ✅ Active |
| 10 | Ukraine | Stat Gov UA | `scrape_govua()` | `stat.gov.ua/en/search?...` | ✅ Active |

**Total publication sources: 8 RSS + 10 scrapers = 18 sources**

---

## C · Pending Requests (Colleague Requests)

| # | Country | Source | URL | Assessment |
|---|---------|--------|-----|------------|
| 1 | France | Agreste | `agreste.agriculture.gouv.fr/agreste-web/` | ✅ **Already covered** by `scrape_agreste()` |
| 2 | Hungary | KSH | `ksh.hu/?lang=en` | ✅ **Already covered** by RSS + `scrape_ksh()` |
| 3 | Canada | StatCan | `statcan.gc.ca/.../agriculture_and_food` | ✅ **Already covered** by RSS + `scrape_statcan()` |
| 4 | Germany | Destatis | `destatis.de/EN/Home/` | ✅ **Already covered** by RSS + `scrape_destatis()` |
| 5 | Italy | ISTAT Agriculture | `istat.it/en/statistical-themes/economy/agriculture/` | ⚠️ **Needs scraper** — RSS covers news, not stat publications page |
| 6 | Spain | MAPA Avances | `mapa.gob.es/.../avances-superficies-producciones-agricolas/` | ⚠️ **Needs scraper** — advance crop area/production estimates (high-value) |
| 7 | Turkey | TUIK MEDAS | `biruni.tuik.gov.tr/medas/?kn=92&locale=en` | ⚠️ **Needs scraper** — granular statistical data portal, different from current scraper |

**Action needed: 3 new scrapers** (Italy ISTAT, Spain MAPA Avances, Turkey TUIK MEDAS)

---

## D · New Sources Needed for 36-Country Expansion

### Strategy: Pan-Regional First, Individual Scrapers Only Where Essential

#### Pan-regional sources (cover many countries at once)

| Source | New countries covered | Method | Priority |
|--------|----------------------|--------|----------|
| **Eurostat (enhanced)** | Bulgaria, Czechia, Denmark, Finland, Latvia, Lithuania, Poland, Slovakia, Sweden, Austria, Belgium, Croatia, Estonia, Ireland, Netherlands, Portugal | Already have 2 RSS feeds; need to verify they capture country-level publications | Phase 1 |
| **USDA FAS PSD API** | USA, Argentina, Brazil, Mexico, India, Indonesia, New Zealand, Morocco, Egypt, South Africa (+ all others) | Free API, no key needed. Single integration covers all countries. | Phase 1 |
| **FAO (enhanced)** | Global coverage supplement | Already have RSS; could add FAOSTAT API for crop data | Phase 2 |

#### Individual scrapers needed (Tier 2 — high-value national data)

| Country | Source | URL | Method | Why worth it |
|---------|--------|-----|--------|-------------|
| USA | USDA NASS | `nass.usda.gov` | RSS (already have 2 feeds) | ✅ Already covered via RSS — just ensure in publication source list |
| South Africa | Stats SA | `statssa.gov.za` | requests+bs4 | Key Hyperplan market, unique national data |
| Brazil | CONAB / IBGE | `conab.gov.br` / `ibge.gov.br` | requests+bs4 | Major producer, national data richer than USDA FAS |
| Argentina | MinAgri / INDEC | `magyp.gob.ar` | requests+bs4 | Major producer |
| India | Min. of Agriculture | `eands.dacnet.nic.in` | requests+bs4 | Huge market |

#### Individual scrapers (Tier 3 — nice to have, lower priority)

| Country | Source | URL | Method | Notes |
|---------|--------|-----|--------|-------|
| Morocco | HCP | `hcp.ma` | Selenium (JS) | Growing market, French-language site |
| Egypt | CAPMAS | `capmas.gov.eg` | requests+bs4 | Limited English content |
| Mexico | INEGI / SIAP | `gob.mx/siap` | requests+bs4 | SIAP has crop-specific data |
| Indonesia | BPS | `bps.go.id` | requests+bs4 | Limited English |
| New Zealand | Stats NZ | `stats.govt.nz` | RSS available | Small market but clean data |

#### Countries with NO individual scraper needed (fully covered by pan-regional sources)

All of these are EU members covered by Eurostat + JRC MARS + COCERAL:

Austria, Belgium, Bulgaria, Croatia, Czechia, Denmark, Estonia, Finland, Ireland, Latvia, Lithuania, Netherlands, Poland, Portugal, Slovakia, Sweden

---

## E · Complete Source Count (Post-Upgrade)

| Category | Current | After Phase 1 | After Phase 3 |
|----------|---------|---------------|---------------|
| Article RSS feeds | 27 | 27+ (add more as discovered) | 30+ |
| Publication RSS feeds | 8 | 8 (unchanged) | 10+ |
| Publication scrapers (existing) | 10 | 10 | 10 |
| Publication scrapers (pending colleague requests) | 0 | 3 (IT, ES avances, TR MEDAS) | 3 |
| Pan-regional APIs (new) | 0 | 1 (USDA FAS PSD) | 2 (+ FAO API) |
| Individual new country scrapers | 0 | 0 | 3-5 (ZA, BR, AR, IN) |
| Company signal feeds (new) | 0 | 23 (Google News RSS) | 23+ |
| **Total sources** | **45** | **~72** | **~80+** |

---

## F · Source Migration Plan to Notion "Sources" Database

When populating the Notion Sources database, each entry gets:

**For article RSS feeds:**
- Source Category: `media`
- Type: `rss`
- Country: relevant country or `Global`

**For publication RSS feeds:**
- Source Category: `official_publication`
- Type: `rss`
- Country: the specific country

**For publication scrapers:**
- Source Category: `official_publication`
- Type: `selenium-scraper` or `requests-scraper`
- Country: the specific country
- Scraper ID: the Python function name (e.g., `scrape_agreste`)

**For company signal feeds:**
- These are auto-generated from the "Tracked Companies" database, not stored in Sources.

**For pan-regional APIs:**
- Source Category: `official_publication`
- Type: `api`
- Country: `Global` or `Europe`
- Scraper ID: the API module name (e.g., `usda_fas_psd`)
