#!/usr/bin/env python3
"""
Create and populate 4 Notion databases for the Daily Agri-News Digest v5 pipeline.
Uses the Notion API directly with the provided integration token.
"""

import json
import os
import time
import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
if not NOTION_TOKEN:
    raise RuntimeError("Set NOTION_TOKEN env var before running this script.")
NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def notion_request(method, endpoint, data=None):
    """Make a Notion API request with rate-limit handling."""
    url = f"{NOTION_API}/{endpoint}"
    for attempt in range(3):
        if method == "POST":
            resp = requests.post(url, headers=HEADERS, json=data, timeout=30)
        elif method == "GET":
            resp = requests.get(url, headers=HEADERS, timeout=30)
        elif method == "PATCH":
            resp = requests.patch(url, headers=HEADERS, json=data, timeout=30)
        else:
            raise ValueError(f"Unknown method: {method}")

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if not resp.ok:
            print(f"  ERROR {resp.status_code}: {resp.text[:500]}")
            return None
        return resp.json()
    return None


def find_or_create_parent():
    """Search for a 'Newsletter Admin' page, or find any page we can use as parent."""
    # First, search for an existing page
    result = notion_request("POST", "search", {
        "query": "Newsletter",
        "filter": {"value": "page", "property": "object"},
        "page_size": 5,
    })
    if result and result.get("results"):
        for page in result["results"]:
            title_parts = page.get("properties", {}).get("title", {}).get("title", [])
            if not title_parts:
                # Try to get title from other places
                pass
            page_id = page["id"]
            print(f"  Found existing page: {page_id}")
            return page_id

    # If no page found, create a new one at workspace level
    print("  Creating 'Newsletter Admin' page...")
    result = notion_request("POST", "pages", {
        "parent": {"type": "workspace", "workspace": True},
        "properties": {
            "title": {
                "title": [{"text": {"content": "Newsletter Admin"}}]
            }
        },
    })
    if result:
        page_id = result["id"]
        print(f"  Created parent page: {page_id}")
        return page_id
    return None


def create_sources_db(parent_id):
    """Create the Newsletter Sources database."""
    print("\n📋 Creating 'Newsletter Sources' database...")
    return notion_request("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"text": {"content": "Newsletter Sources"}}],
        "properties": {
            "Name": {"title": {}},
            "URL": {"url": {}},
            "Source Category": {
                "select": {
                    "options": [
                        {"name": "media", "color": "blue"},
                        {"name": "official_publication", "color": "green"},
                        {"name": "company_signal", "color": "orange"},
                    ]
                }
            },
            "Type": {
                "select": {
                    "options": [
                        {"name": "rss", "color": "blue"},
                        {"name": "selenium-scraper", "color": "red"},
                        {"name": "requests-scraper", "color": "yellow"},
                        {"name": "api", "color": "purple"},
                    ]
                }
            },
            "Country": {"rich_text": {}},
            "Enabled": {"checkbox": {}},
            "Keywords Filter": {
                "multi_select": {
                    "options": [
                        {"name": "crop", "color": "green"},
                        {"name": "agriculture", "color": "green"},
                    ]
                }
            },
            "Scraper ID": {"rich_text": {}},
            # Optional per-source lookback. 0 / empty = use global default
            # (48h for articles, 168h for publications, 336h for Google News).
            "Lookback Hours": {"number": {"format": "number"}},
            "Notes": {"rich_text": {}},
        },
    })


def create_recipients_db(parent_id):
    """Create the Newsletter Recipients database."""
    print("\n👥 Creating 'Newsletter Recipients' database...")
    return notion_request("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"text": {"content": "Newsletter Recipients"}}],
        "properties": {
            "Name": {"title": {}},
            "Email": {"email": {}},
            "Active": {"checkbox": {}},
            "Group": {
                "select": {
                    "options": [
                        {"name": "internal", "color": "blue"},
                        {"name": "external", "color": "green"},
                    ]
                }
            },
            "Is Primary": {"checkbox": {}},
            "Lead ID": {"rich_text": {}},
            "Role": {
                "select": {
                    "options": [
                        {"name": "subscriber", "color": "default"},
                        {"name": "admin", "color": "red"},
                    ]
                }
            },
        },
    })


def create_companies_db(parent_id):
    """Create the Tracked Companies database."""
    print("\n🏢 Creating 'Tracked Companies' database...")
    return notion_request("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"text": {"content": "Tracked Companies"}}],
        "properties": {
            "Company Name": {"title": {}},
            "Type": {
                "select": {
                    "options": [
                        {"name": "client", "color": "green"},
                        {"name": "prospect", "color": "orange"},
                    ]
                }
            },
            "Search Keywords": {"rich_text": {}},
            "Enabled": {"checkbox": {}},
            "Priority": {
                "select": {
                    "options": [
                        {"name": "high", "color": "red"},
                        {"name": "medium", "color": "yellow"},
                        {"name": "low", "color": "default"},
                    ]
                }
            },
            "Notes": {"rich_text": {}},
        },
    })


def create_countries_db(parent_id):
    """Create the Countries of Interest database."""
    print("\n🌍 Creating 'Countries of Interest' database...")
    return notion_request("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"text": {"content": "Countries of Interest"}}],
        "properties": {
            "Country": {"title": {}},
            "Region": {
                "select": {
                    "options": [
                        {"name": "EU", "color": "blue"},
                        {"name": "Americas", "color": "green"},
                        {"name": "Africa-ME", "color": "orange"},
                        {"name": "Asia-Pacific", "color": "purple"},
                    ]
                }
            },
            "Flag Emoji": {"rich_text": {}},
            "Enabled": {"checkbox": {}},
            "Has Scraper": {"checkbox": {}},
            "Pan-regional Source": {
                "multi_select": {
                    "options": [
                        {"name": "eurostat", "color": "blue"},
                        {"name": "usda_fas", "color": "green"},
                        {"name": "fao", "color": "orange"},
                        {"name": "jrc_mars", "color": "purple"},
                    ]
                }
            },
            "Notes": {"rich_text": {}},
        },
    })


def add_source(db_id, name, url, category, source_type, country="", enabled=True, scraper_id="", notes=""):
    """Add a source row to the Sources database."""
    props = {
        "Name": {"title": [{"text": {"content": name}}]},
        "URL": {"url": url if url else None},
        "Source Category": {"select": {"name": category}},
        "Type": {"select": {"name": source_type}},
        "Country": {"rich_text": [{"text": {"content": country}}] if country else []},
        "Enabled": {"checkbox": enabled},
    }
    if scraper_id:
        props["Scraper ID"] = {"rich_text": [{"text": {"content": scraper_id}}]}
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    result = notion_request("POST", "pages", {
        "parent": {"database_id": db_id},
        "properties": props,
    })
    if result:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}")
    time.sleep(0.35)  # Rate limit safety


def add_recipient(db_id, name, email, group, is_primary=False, lead_id="", role="subscriber"):
    """Add a recipient row."""
    props = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Email": {"email": email},
        "Active": {"checkbox": True},
        "Group": {"select": {"name": group}},
        "Is Primary": {"checkbox": is_primary},
        "Role": {"select": {"name": role}},
    }
    if lead_id:
        props["Lead ID"] = {"rich_text": [{"text": {"content": lead_id}}]}

    result = notion_request("POST", "pages", {
        "parent": {"database_id": db_id},
        "properties": props,
    })
    if result:
        print(f"  ✅ {name} ({email})")
    else:
        print(f"  ❌ {name}")
    time.sleep(0.35)


def add_company(db_id, name, company_type, keywords="", priority="medium", notes=""):
    """Add a company row."""
    props = {
        "Company Name": {"title": [{"text": {"content": name}}]},
        "Type": {"select": {"name": company_type}},
        "Enabled": {"checkbox": True},
        "Priority": {"select": {"name": priority}},
    }
    if keywords:
        props["Search Keywords"] = {"rich_text": [{"text": {"content": keywords}}]}
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    result = notion_request("POST", "pages", {
        "parent": {"database_id": db_id},
        "properties": props,
    })
    if result:
        print(f"  ✅ {name} ({company_type})")
    else:
        print(f"  ❌ {name}")
    time.sleep(0.35)


def add_country(db_id, name, region, flag, enabled=True, has_scraper=False, pan_sources=None, notes=""):
    """Add a country row."""
    props = {
        "Country": {"title": [{"text": {"content": name}}]},
        "Region": {"select": {"name": region}},
        "Flag Emoji": {"rich_text": [{"text": {"content": flag}}]},
        "Enabled": {"checkbox": enabled},
        "Has Scraper": {"checkbox": has_scraper},
    }
    if pan_sources:
        props["Pan-regional Source"] = {"multi_select": [{"name": s} for s in pan_sources]}
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    result = notion_request("POST", "pages", {
        "parent": {"database_id": db_id},
        "properties": props,
    })
    if result:
        print(f"  ✅ {flag} {name}")
    else:
        print(f"  ❌ {name}")
    time.sleep(0.35)


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("Creating Notion databases for Daily Agri-News Digest v5")
    print("=" * 60)

    # Step 0: Find or create parent page
    print("\n🔍 Finding parent page...")
    parent_id = find_or_create_parent()
    if not parent_id:
        print("FATAL: Could not find or create a parent page.")
        return

    # Step 1: Create all 4 databases
    sources_db = create_sources_db(parent_id)
    recipients_db = create_recipients_db(parent_id)
    companies_db = create_companies_db(parent_id)
    countries_db = create_countries_db(parent_id)

    if not all([sources_db, recipients_db, companies_db, countries_db]):
        print("\nFATAL: Failed to create one or more databases.")
        # Print what we got
        print(f"  Sources:    {'✅' if sources_db else '❌'}")
        print(f"  Recipients: {'✅' if recipients_db else '❌'}")
        print(f"  Companies:  {'✅' if companies_db else '❌'}")
        print(f"  Countries:  {'✅' if countries_db else '❌'}")
        return

    sources_id = sources_db["id"]
    recipients_id = recipients_db["id"]
    companies_id = companies_db["id"]
    countries_id = countries_db["id"]

    print(f"\n✅ All databases created:")
    print(f"  Sources:    {sources_id}")
    print(f"  Recipients: {recipients_id}")
    print(f"  Companies:  {companies_id}")
    print(f"  Countries:  {countries_id}")

    # ── Populate Sources ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Populating Newsletter Sources...")
    print("=" * 60)

    # Article RSS feeds (27)
    article_feeds = [
        ("AgFunder News", "http://agfundernews.com/feed", "Global"),
        ("TechCrunch AgTech", "https://techcrunch.com/tag/agtech/feed/", "Global"),
        ("Precision Ag", "http://www.precisionag.com/feed/", "Global"),
        ("AgWired", "http://agwired.com/feed/", "Global"),
        ("Agribusiness Global", "https://www.agribusinessglobal.com/feed/", "Global"),
        ("Future Farming", "https://www.futurefarming.com/feed/", "Global"),
        ("Barchart Commodities", "https://www.barchart.com/news/rss/commodities", "Global"),
        ("World-Grain", "https://www.world-grain.com/rss/articles", "Global"),
        ("Grain Central — News", "https://www.graincentral.com/news/feed/", "Global"),
        ("Grain Central — Markets", "https://www.graincentral.com/markets/feed/", "Global"),
        ("Grain Central — Trade", "https://www.graincentral.com/trade/feed/", "Global"),
        ("Investopedia Agriculture", "https://feeds-api.dotdashmeredith.com/v1/rss/google/77cb61c1-0387-45bf-80e6-3f2976e90672", "Global"),
        ("FAO Newsroom", "https://www.fao.org/feeds/fao-newsroom-rss", "Global"),
        ("USDA NASS — News", "http://www.nass.usda.gov/rss/news.xml", "USA"),
        ("USDA NASS — Reports", "http://www.nass.usda.gov/rss/reports.xml", "USA"),
        ("JRC Agriculture News", "https://joint-research-centre.ec.europa.eu/node/2/rss_en", "Europe"),
        ("Min. Agriculture FR", "http://agriculture.gouv.fr/rss.xml", "France"),
        ("Terre-net", "http://www.terre-net.fr/actualite-agriculture.html", "France"),
        ("La France Agricole", "http://www.lafranceagricole.fr/rss/actualites", "France"),
        ("Western Producer", "http://feeds.feedburner.com/westernproducer", "Canada"),
        ("Farms.com — Crop News", "https://www.farms.com/Portals/_default/RSS_Portal/News_Crop.xml", "Canada"),
        ("Farms.com — Featured Crop", "https://www.farms.com/Portals/_default/RSS_Portal/Featured_Crop.xml", "Canada"),
        ("Agroes (Spain)", "https://www.agroes.es/cultivos-agricultura?format=feed&type=rss", "Spain"),
        ("Romania Insider", "http://www.romania-insider.com/feed/", "Romania"),
        ("SeedWorld Europe", "https://www.seedworld.com/europe/feed/", "Europe"),
        ("Farm Chemicals Intl", "http://www.farmchemicalsinternational.com/rss/rssfeed.php?pageid=2", "Global"),
        ("Inside Climate News", "http://insideclimatenews.org/news/rss-teaser.xml", "Global"),
    ]
    for name, url, country in article_feeds:
        add_source(sources_id, name, url, "media", "rss", country)

    # Publication RSS feeds (8)
    pub_feeds = [
        ("MAPA Spain", "https://www.mapa.gob.es/es/agricultura/noticiasRss.aspx", "Spain"),
        ("KSH Hungary", "https://www.ksh.hu/apps/shop.rss_temakor?p_lang=EN&p_temakor_kod=KSH", "Hungary"),
        ("Destatis Germany", "http://www.destatis.de/Aktuelles.xml", "Germany"),
        ("StatCan Canada", "https://www150.statcan.gc.ca/n1/rss/dai-quo/32-eng.atom", "Canada"),
        ("INSSE Romania", "https://insse.ro/cms/files/rss_ins_en.xml", "Romania"),
        ("ISTAT Italy", "https://www.istat.it/en/tema/agriculture/feed/", "Italy"),
        ("Eurostat — News", "https://ec.europa.eu/eurostat/web/main/home/rss", "Europe"),
        ("Eurostat — Datasets", "https://ec.europa.eu/eurostat/web/main/home/rss", "Europe"),
    ]
    for name, url, country in pub_feeds:
        add_source(sources_id, name, url, "official_publication", "rss", country)

    # Publication scrapers (10)
    pub_scrapers = [
        ("Agreste France", "https://agreste.agriculture.gouv.fr/agreste-web/", "France", "selenium-scraper", "scrape_agreste"),
        ("JRC MARS Europe", "https://publications.jrc.ec.europa.eu/repository/search", "Europe", "selenium-scraper", "scrape_jrc"),
        ("CAA Spain (Cooperativas)", "https://www.agro-alimentarias.coop/documents?cat_sel=33", "Spain", "selenium-scraper", "scrape_caa"),
        ("StatCan Scraper", "https://www150.statcan.gc.ca/", "Canada", "selenium-scraper", "scrape_statcan"),
        ("TUIK Turkey", "https://data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111", "Turkey", "selenium-scraper", "scrape_tuik"),
        ("DEFRA UK", "https://www.gov.uk/search/all", "UK", "selenium-scraper", "scrape_uk"),
        ("KSH Hungary Scraper", "https://www.ksh.hu/apps/shop.lista?p_lang=EN&p_temakor_kod=OM", "Hungary", "selenium-scraper", "scrape_ksh"),
        ("COCERAL Europe", "https://www.coceral.com/web/coceral%20crop%20forecast/", "Europe", "selenium-scraper", "scrape_coceral"),
        ("Destatis Scraper", "https://www.destatis.de/SiteGlobals/Forms/Suche/EN/", "Germany", "selenium-scraper", "scrape_destatis"),
        ("Stat Gov UA Ukraine", "https://stat.gov.ua/en/search", "Ukraine", "selenium-scraper", "scrape_govua"),
    ]
    for name, url, country, stype, sid in pub_scrapers:
        add_source(sources_id, name, url, "official_publication", stype, country, scraper_id=sid)

    print(f"\n  Total sources added: {len(article_feeds) + len(pub_feeds) + len(pub_scrapers)}")

    # ── Populate Recipients ────────────────────────────────────
    print("\n" + "=" * 60)
    print("Populating Newsletter Recipients...")
    print("=" * 60)

    # Internal team (9 people)
    internal = [
        ("Rémi Banquet", "remi@hyperplan.fr", True, "admin"),
        ("Victoria", "victoria@hyperplan.fr", False, "subscriber"),
        ("Clémence", "clemence@hyperplan.fr", False, "subscriber"),
        ("Thibault", "thibault@hyperplan.fr", False, "subscriber"),
        ("Victor", "victor@hyperplan.fr", False, "subscriber"),
        ("Ruben", "ruben@hyperplan.fr", False, "subscriber"),
        ("JB", "jb@hyperplan.fr", False, "subscriber"),
        ("Matthieu", "matthieu@hyperplan.fr", False, "subscriber"),
        ("Guillaume", "guillaume@hyperplan.fr", False, "subscriber"),
    ]
    for name, email, is_primary, role in internal:
        add_recipient(recipients_id, name, email, "internal", is_primary=is_primary, role=role)

    # External (2 people — Demeter IM)
    external = [
        ("Thomas Beaugendre", "thomas.beaugendre@demeter-im.com", True, "subscriber"),
        ("Geoffroy Dubus", "geoffroy.dubus@demeter-im.com", False, "subscriber"),
    ]
    for name, email, is_primary, role in external:
        add_recipient(recipients_id, name, email, "external", is_primary=is_primary, role=role)

    print(f"\n  Total recipients added: {len(internal) + len(external)}")

    # ── Populate Companies ────────────────────────────────────
    print("\n" + "=" * 60)
    print("Populating Tracked Companies...")
    print("=" * 60)

    # Clients (9) — per validated strategy doc
    clients = [
        ("Corteva", "Corteva Agriscience agriculture", "high"),
        ("Syngenta", "Syngenta agriculture crop", "high"),
        ("Bayer", "Bayer CropScience agriculture", "high"),
        ("BASF", "BASF agriculture crop protection", "high"),
        ("Certis Belchim", "Certis Belchim crop protection biocontrol", "high"),
        ("Adama", "ADAMA agriculture crop protection", "medium"),
        ("KWS", "KWS seeds agriculture crop", "high"),
        ("Lidea", "Lidea seeds agriculture", "high"),
        ("Saipol", "Saipol oilseed crushing", "medium"),
    ]
    for name, keywords, priority in clients:
        add_company(companies_id, name, "client", keywords, priority)

    # Prospects (14) — per validated strategy doc
    prospects = [
        ("Sumitomo Chemical", "Sumitomo Chemical agriculture crop protection", "medium"),
        ("Nutrien", "Nutrien agriculture fertilizer", "medium"),
        ("Nufarm", "Nufarm agriculture crop protection", "medium"),
        ("GDM", "GDM seeds genetics agriculture", "medium"),
        ("CF Industries", "CF Industries nitrogen fertilizer agriculture", "medium"),
        ("CHS Inc", "CHS Inc agriculture cooperative grain", "medium"),
        ("Yara International", "Yara International fertilizer agriculture", "medium"),
        ("OCP Nutricrops", "OCP Nutricrops phosphate fertilizer agriculture", "low"),
        ("The Mosaic Company", "Mosaic Company fertilizer agriculture", "medium"),
        ("UPL", "UPL agriculture crop protection", "medium"),
        ("FMC Corporation", "FMC Corporation agriculture crop protection", "medium"),
        ("ICL Group", "ICL Group fertilizer specialty agriculture", "low"),
        ("Timac Agro", "Timac Agro fertilizer plant nutrition", "medium"),
        ("Richardson International", "Richardson International agriculture grain oilseed", "medium"),
    ]
    for name, keywords, priority in prospects:
        add_company(companies_id, name, "prospect", keywords, priority)

    print(f"\n  Total companies added: {len(clients) + len(prospects)}")

    # ── Populate Countries ────────────────────────────────────
    print("\n" + "=" * 60)
    print("Populating Countries of Interest...")
    print("=" * 60)

    countries = [
        # EU countries with dedicated scrapers/RSS
        ("France", "EU", "🇫🇷", True, True, ["eurostat", "jrc_mars"]),
        ("Germany", "EU", "🇩🇪", True, True, ["eurostat", "jrc_mars"]),
        ("Spain", "EU", "🇪🇸", True, True, ["eurostat", "jrc_mars"]),
        ("Italy", "EU", "🇮🇹", True, False, ["eurostat", "jrc_mars"]),
        ("Romania", "EU", "🇷🇴", True, False, ["eurostat", "jrc_mars"]),
        ("Hungary", "EU", "🇭🇺", True, True, ["eurostat", "jrc_mars"]),
        # EU countries covered by pan-regional only
        ("Poland", "EU", "🇵🇱", True, False, ["eurostat", "jrc_mars"]),
        ("Bulgaria", "EU", "🇧🇬", True, False, ["eurostat", "jrc_mars"]),
        ("Czechia", "EU", "🇨🇿", True, False, ["eurostat", "jrc_mars"]),
        ("Denmark", "EU", "🇩🇰", True, False, ["eurostat", "jrc_mars"]),
        ("Finland", "EU", "🇫🇮", True, False, ["eurostat", "jrc_mars"]),
        ("Sweden", "EU", "🇸🇪", True, False, ["eurostat", "jrc_mars"]),
        ("Austria", "EU", "🇦🇹", True, False, ["eurostat", "jrc_mars"]),
        ("Belgium", "EU", "🇧🇪", True, False, ["eurostat", "jrc_mars"]),
        ("Croatia", "EU", "🇭🇷", True, False, ["eurostat", "jrc_mars"]),
        ("Estonia", "EU", "🇪🇪", True, False, ["eurostat", "jrc_mars"]),
        ("Ireland", "EU", "🇮🇪", True, False, ["eurostat", "jrc_mars"]),
        ("Latvia", "EU", "🇱🇻", True, False, ["eurostat", "jrc_mars"]),
        ("Lithuania", "EU", "🇱🇹", True, False, ["eurostat", "jrc_mars"]),
        ("Netherlands", "EU", "🇳🇱", True, False, ["eurostat", "jrc_mars"]),
        ("Portugal", "EU", "🇵🇹", True, False, ["eurostat", "jrc_mars"]),
        ("Slovakia", "EU", "🇸🇰", True, False, ["eurostat", "jrc_mars"]),
        # Non-EU Europe
        ("UK", "EU", "🇬🇧", True, True, ["jrc_mars", "usda_fas"]),
        ("Ukraine", "EU", "🇺🇦", True, True, ["jrc_mars", "usda_fas"]),
        ("Turkey", "EU", "🇹🇷", True, True, ["jrc_mars", "usda_fas"]),
        # Americas
        ("USA", "Americas", "🇺🇸", True, False, ["usda_fas"]),
        ("Canada", "Americas", "🇨🇦", True, True, ["usda_fas"]),
        ("Brazil", "Americas", "🇧🇷", True, False, ["usda_fas", "fao"]),
        ("Argentina", "Americas", "🇦🇷", True, False, ["usda_fas", "fao"]),
        ("Mexico", "Americas", "🇲🇽", True, False, ["usda_fas", "fao"]),
        # Africa & Middle East
        ("South Africa", "Africa-ME", "🇿🇦", True, False, ["usda_fas", "fao"]),
        ("Morocco", "Africa-ME", "🇲🇦", True, False, ["usda_fas", "fao"]),
        ("Egypt", "Africa-ME", "🇪🇬", True, False, ["usda_fas", "fao"]),
        # Asia-Pacific
        ("India", "Asia-Pacific", "🇮🇳", True, False, ["usda_fas", "fao"]),
        ("Indonesia", "Asia-Pacific", "🇮🇩", True, False, ["usda_fas", "fao"]),
        ("New Zealand", "Asia-Pacific", "🇳🇿", True, False, ["usda_fas", "fao"]),
        # Supranational
        ("Europe", "EU", "🇪🇺", True, True, ["eurostat", "jrc_mars", "usda_fas"]),
    ]
    for name, region, flag, enabled, has_scraper, pan_sources in countries:
        add_country(countries_id, name, region, flag, enabled, has_scraper, pan_sources)

    print(f"\n  Total countries added: {len(countries)}")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🎉 DONE — All databases created and populated!")
    print("=" * 60)
    print(f"\nNotion Database IDs (save these for .env):")
    print(f"  NOTION_SOURCES_DB_ID    = {sources_id}")
    print(f"  NOTION_RECIPIENTS_DB_ID = {recipients_id}")
    print(f"  NOTION_COMPANIES_DB_ID  = {companies_id}")
    print(f"  NOTION_COUNTRIES_DB_ID  = {countries_id}")

    # Write IDs to a file for easy reference
    env_lines = {
        "NOTION_SOURCES_DB_ID": sources_id,
        "NOTION_RECIPIENTS_DB_ID": recipients_id,
        "NOTION_COMPANIES_DB_ID": companies_id,
        "NOTION_COUNTRIES_DB_ID": countries_id,
    }
    with open("/sessions/epic-friendly-ride/mnt/Daily Agri-News Digest/newsletter_v5/notion_db_ids.env", "w") as f:
        for k, v in env_lines.items():
            f.write(f"{k}={v}\n")
    print("\n  IDs saved to newsletter_v5/notion_db_ids.env")


if __name__ == "__main__":
    main()
