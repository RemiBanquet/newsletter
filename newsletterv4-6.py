# en tout début de script (avant tout)
import fcntl, sys
_lock_f = open(".newsletter.lock", "w")
try:
    fcntl.flock(_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Another newsletter run is already active. Exiting.")
    sys.exit(0)

#!/usr/bin/env python3
"""Daily Agri‑News Digest – Automated Newsletter System
======================================================
This script powers the **Daily Agri‑News Digest**, an internal Hyperplan newsletter that collects, classifies and distributes the most relevant
crop‑related news stories and official agricultural publications.The file is now **fully documented**. Every public function, class and
constant carries an English docstring that explains *what* it does and *why* it matters, so that newcomers—technical *or* non‑technical—can read
and understand the entire execution flow without diving into the implementation details first.

The **business flow** in a nutshell
-----------------------------------
1. Runtime configuration is loaded from a `.env` and a few hard‑coded
   #constants (crop keywords, map of country flags…).
2. A pool of RSS feeds **and** Selenium scrapers collects raw articles and statistical bulletins.
3. Language detection + keyword filters keep only crop‑relevant items.
4. Each item is sent to **Mistral AI** for an English 3‑bullet summary,a category (Vegetal crops / Agri‑tech / Climate) and a semantic tag.
5. Articles are batched into a responsive HTML newsletter and emailed via Gmail SMTP. Each accepted item is also archived to Notion.
6. An admin report summarises the run.

Structured logging is enabled everywhere—see `newsletter.log` after a run for the full trace.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Imports
# -----------------------------------------------------------------------------

# --- Standard library
from datetime import datetime, timedelta
import hashlib
import json
import logging
import os
import random
import re
import smtplib
import string
import threading
import time
from typing import Optional

# --- Third-party
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
import feedparser
import html2text
from langdetect import detect, LangDetectException
from mistralai import Mistral
from notion_client import Client
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# --- Email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ──────────────────────────────────────────────────────────────────────────────
# Logging configuration
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("newsletter.log"),
        logging.StreamHandler()
    ]
)

# ──────────────────────────────────────────────────────────────────────────────
# Environment & global constants
# -----------------------------------------------------------------------------
load_dotenv()

ZONE_FLAGS = {
    "France": "🇫🇷",
    "Spain": "🇪🇸",
    "Italy": "🇮🇹",
    "Hungary": "🇭🇺",
    "Germany": "🇩🇪",
    "Canada": "🇨🇦",
    "Romania": "🇷🇴",
    "Ukraine": "🇺🇦",
    "Turkey": "🇹🇷",
    "UK": "🇬🇧",
    "Europe": "🇪🇺",
    "South Africa": "🇿🇦"
}

CROP_KEYWORDS = [
    "alfalfa", "beans", "beet", "hemp", "linen", "maize", "corn", "cereals", "peas", "potato",
    "soy", "sorghum", "barley", "wheat", "sunflower", "triticale", "canola", "WOSR", "field crops", "meadows", "grains", "oilseeds"
]

CROP_CONTEXTUAL_KEYWORDS = [
    "arable land", "soil cover", "irrigated area", "crop rotation", "soil management", "irrigation", "cultivated area",
    "plant production", "agricultural area", "crop area", "farming", "cropping", "land use", "planted", "field crops", "crop acreage", "crop yields", "by culture", "crop monitoring"
]

# Configs
raw_feed_urls = os.getenv("FEED_URLS")
if not raw_feed_urls:
    logging.error("FEED_URLS est requis mais non défini dans le .env. Arrêt du script.")
    raise ValueError("FEED_URLS manquant dans le fichier .env")
else:
    FEED_URLS = [url.strip() for url in raw_feed_urls.split("|") if url.strip()]

raw_recipients = os.getenv("EMAIL_RECIPIENTS")
if not raw_recipients:
    logging.error("EMAIL_RECIPIENTS manquant dans le .env. Aucun email ne sera envoyé.")
    EMAIL_TO = []
else:
    EMAIL_TO = [email.strip() for email in raw_recipients.split(",") if email.strip()]

NEWSLETTER_TITLE = "🛰️ Daily Agri-News Digest 🌱"
DAYS_TO_LOOK_BACK = 1
MAX_ARTICLES_PER_EMAIL = 10
HISTORY_FILE = "sent_articles.json"

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL_NAME = os.getenv("MISTRAL_MODEL_NAME")
MISTRAL_FALLBACK_MODEL_NAME = os.getenv("MISTRAL_FALLBACK_MODEL_NAME")
MISTRAL_MODELS = []
if MISTRAL_MODEL_NAME:
    MISTRAL_MODELS.append(MISTRAL_MODEL_NAME)
if MISTRAL_FALLBACK_MODEL_NAME and MISTRAL_FALLBACK_MODEL_NAME != MISTRAL_MODEL_NAME:
    MISTRAL_MODELS.append(MISTRAL_FALLBACK_MODEL_NAME)

if not MISTRAL_MODELS:
    raise RuntimeError("No Mistral model configured. Set MISTRAL_MODEL_NAME (and optionally MISTRAL_FALLBACK_MODEL_NAME).")

BANNER_URL = "https://raw.githubusercontent.com/RemiBanquet/newsletter-assets/main/LinkedIn_banner_green.jpg"

ACCEPTED_CATEGORIES = ["Vegetal crops", "Agri-tech", "Climate"]

if not MISTRAL_API_KEY:
    logging.error("La clé API Mistral (MISTRAL_API_KEY) n'est pas configurée. Veuillez l'ajouter à votre fichier .env")

# --- Category sanitization -----------------------------------------------------

_FIELD_SPLITTERS = ("tag:", "tags:", "label:", "labels:", "summary:", "abstract:", "titre:", "title:")

def _cut_at_next_field(s: str) -> str:
    low = s.lower()
    cut = len(s)
    for sep in _FIELD_SPLITTERS:
        i = low.find(sep)
        if i != -1:
            cut = min(cut, i)
    nl = s.find("\n")
    if nl != -1:
        cut = min(cut, nl)
    return s[:cut]

_MD_TOKENS = "*_`"
def sanitize_label(s: str) -> str:
    if not s:
        return ""
    s = _cut_at_next_field(s.strip())
    s = s.translate(str.maketrans("", "", _MD_TOKENS))          # retire **, _, `
    s = s.translate(str.maketrans("", "", "\"'“”‘’"))           # guillemets
    s = s.translate(str.maketrans("", "", "()[]{}"))            # parenthèses/crochets
    s = re.sub(r"^[\s\-\–\—•·]+", "", s)                        # puces/tirets en tête
    s = re.sub(r"\s+", " ", s).strip()                          # espaces
    s = s.rstrip(string.punctuation + "·–—")                    # ponctuation terminale
    return s.lower()

# MISE À JOUR de is_accepted_category pour utiliser sanitize_label
ACCEPTED_CATEGORIES_NORM = {c.lower() for c in ACCEPTED_CATEGORIES}

def is_accepted_category(category: str) -> bool:
    return sanitize_label(category) in ACCEPTED_CATEGORIES_NORM


# ---- Tuning knobs (start conservative, adjust with your logs) ----
MAX_CONCURRENCY = 1     # 1 requête en vol
MIN_INTERVAL_S  = 1.2   # ≥1 s de marge (RPS ≈ 0.83)
MAX_RETRIES     = 5        # keep your current value if different
BACKOFF_BASE    = 1.6      # exponential base
BACKOFF_CAP_S   = 90       # max sleep between retries
CB_THRESHOLD    = 5        # consecutive 429s before opening the circuit
CB_COOLDOWN_S   = 60       # global cool-down when circuit opens

class RateLimiter:
    """Cap de concurrence + espacement RPS + cooldown + circuit breaker (429) + Retry-After + 3505."""
    def __init__(self, max_conc:int, min_interval_s:float,
                 cb_threshold:int, cb_cooldown_s:float):
        self._sem = threading.Semaphore(max_conc)
        self._lock = threading.Lock()
        self._last_ts = 0.0

        # >>> IMPORTANT : interval initial pour l'espacement RPS
        self._interval = float(min_interval_s)

        # Cooldown horodaté (Retry-After, breaker, 3505…)
        self._cooldown_until = 0.0

        # Circuit breaker 429
        self._consec_429 = 0
        self._cb_threshold = int(cb_threshold)
        self._cb_cooldown = float(cb_cooldown_s)

    def acquire(self):
        """Bloque jusqu'à ce que la fenêtre RPS/cooldown permette un appel."""
        class _Guard:
            def __init__(self, outer:'RateLimiter'):
                self.outer = outer
            def __enter__(self):
                self.outer._sem.acquire()
                now = time.time()
                with self.outer._lock:
                    # Respect d'un éventuel cooldown global
                    if now < self.outer._cooldown_until:
                        time.sleep(self.outer._cooldown_until - now)

                    # Espacement RPS (intervalle fixe)
                    sleep_rps = max(0.0, self.outer._last_ts + self.outer._interval - time.time())
                    if sleep_rps > 0:
                        time.sleep(sleep_rps)

                    # Marque le dernier envoi
                    self.outer._last_ts = time.time()
                return self
            def __exit__(self, exc_type, exc, tb):
                self.outer._sem.release()
        return _Guard(self)

    def record_result(self, status_code: int, headers: Optional[dict] = None, capacity_exceeded: bool = False):
        """Met à jour le cooldown/breaker en fonction du code et des en-têtes."""
        with self._lock:
            # 1) Retry-After → cooldown
            if headers:
                ra = headers.get("Retry-After") or headers.get("retry-after")
                if ra:
                    try:
                        ra_s = float(ra)
                        self._cooldown_until = max(self._cooldown_until, time.time() + ra_s)
                    except Exception:
                        pass

            # 2) Capacité saturée (code 3505) → long cooldown
            if capacity_exceeded:
                # 180s par défaut ; ajuste si besoin
                self._cooldown_until = max(self._cooldown_until, time.time() + 180.0)

            # 3) Circuit breaker sur 429
            if status_code == 429:
                self._consec_429 += 1
                if self._consec_429 >= self._cb_threshold:
                    self._cooldown_until = max(self._cooldown_until, time.time() + self._cb_cooldown)
                    self._consec_429 = 0
            else:
                self._consec_429 = 0

            # 4) Si un header "Remaining" indique quasi zéro, micro-pause
            if headers:
                rem = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
                try:
                    if rem is not None and float(rem) <= 1:
                        self._cooldown_until = max(self._cooldown_until, time.time() + 2.0)
                except Exception:
                    pass



# One global limiter
MISTRAL_LIMITER = RateLimiter(
    max_conc=MAX_CONCURRENCY,
    min_interval_s=MIN_INTERVAL_S,
    cb_threshold=CB_THRESHOLD,
    cb_cooldown_s=CB_COOLDOWN_S,
)

def compute_backoff_sleep(attempt:int, retry_after_header:Optional[str]) -> float:
    """Respect Retry-After if present; else exponential backoff with FULL jitter."""
    if retry_after_header:
        try:
            return float(retry_after_header)
        except Exception:
            pass
    # Exponential backoff with FULL jitter
    base = min(BACKOFF_CAP_S, (BACKOFF_BASE ** attempt))
    return random.uniform(0.0, base)
# ===============================================================================

def mistral_complete_with_resilience(
    call_fn,
    *,
    max_retries:int = MAX_RETRIES,
    logger=None,
    on_capacity_exceeded=None   # NEW
):
    last_exc = None
    for attempt in range(1, max_retries + 1):
        with MISTRAL_LIMITER.acquire():
            try:
                resp = call_fn()
                status  = getattr(resp, "status_code", 200) if hasattr(resp, "status_code") else 200
                headers = getattr(resp, "headers", {}) if hasattr(resp, "headers") else {}
                MISTRAL_LIMITER.record_result(status_code=status, headers=headers)
                return resp

            except Exception as e:
                status = None
                headers = {}
                retry_after = None
                is_capacity_exceeded = False  # NEW

                r = getattr(e, "response", None)
                if r is not None:
                    status = getattr(r, "status_code", None)
                    headers = getattr(r, "headers", {}) or {}
                    retry_after = headers.get("Retry-After")
                    # NEW: détecter 3505
                    try:
                        err_json = r.json() if hasattr(r, "json") else None
                        if not err_json and hasattr(r, "text"):
                            import json as _json
                            err_json = _json.loads(r.text)
                        if isinstance(err_json, dict):
                            code_val = err_json.get("code") or err_json.get("error", {}).get("code")
                            if str(code_val) == "3505":
                                is_capacity_exceeded = True
                    except Exception:
                        pass

                if logger:
                    logger.warning(f"[Mistral] error on attempt {attempt}/{max_retries} "
                                   f"status={status} retry_after={retry_after} err={e}")

                # --- record + decide retry (MAJ) ---
                if status in (429, 500, 502, 503, 504):
                    MISTRAL_LIMITER.record_result(status_code=status, headers=headers, capacity_exceeded=is_capacity_exceeded)

                    if attempt < max_retries:
                        # Si capacité saturée → switch modèle avant retry
                        if is_capacity_exceeded and on_capacity_exceeded:
                            try:
                                on_capacity_exceeded()
                                if logger:
                                    logger.warning("[Mistral] capacity exceeded → switching to fallback model for next attempt")
                            except Exception as _:
                                pass

                        retry_after_hdr = headers.get("Retry-After") if headers else None
                        # Si 3505 sans Retry-After → backoff plus long
                        sleep_s = max(60.0, compute_backoff_sleep(attempt, None)) if (is_capacity_exceeded and not retry_after_hdr) \
                                  else compute_backoff_sleep(attempt, retry_after_hdr)

                        if logger:
                            logger.info(f"[Mistral] backoff sleeping {sleep_s:.1f}s before retry..."
                                        f"{' (capacity_exceeded)' if is_capacity_exceeded else ''}")
                        time.sleep(sleep_s)
                        last_exc = e
                        continue

                # erreurs non retryables
                last_exc = e
                break

    raise last_exc



# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers
# -----------------------------------------------------------------------------

def format_display_date(date_str):
    """
    Convert a publication date coming from heterogeneous sources into a
    single human-readable form.

    Parameters
    ----------
    date_str : str
        Raw date string as found online (either ``YYYY-MM-DD`` or
        ``DD/MM/YYYY``).

    Returns
    -------
    str
        The same date formatted as ``DD Mon YYYY`` (-→ “27 Jun 2025”).
        When parsing fails the original string is returned unchanged.
    """
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return date_str  # fallback brut si parsing impossible

PUBLICATION_MEMORY_PATH = "sent_publications.json"

def detect_language(text: str):
    """
    Detect the language of *text* using ``langdetect``.

    Parameters
    ----------
    text : str
        Any short string (title, sentence …).

    Returns
    -------
    str
        The ISO-639-1 language code, or ``"unknown"`` when detection fails.
    """
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"
        
# ──────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# -----------------------------------------------------------------------------

def load_sent_publications():
    """
    Load the list of official-publication IDs that have already been pushed
    to Notion, so we never upload duplicates.

    Returns
    -------
    set[str]
        Stable identifiers previously stored in *sent_publications.json*.
    """
    if os.path.exists(PUBLICATION_MEMORY_PATH):
        with open(PUBLICATION_MEMORY_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_sent_publications(sent_ids):
    """
    Atomically overwrite *sent_publications.json* with the latest set of
    archived publication IDs.

    Parameters
    ----------
    sent : set[str]
        Unique identifiers (typically URLs) that were accepted this run.
    """
    with open(PUBLICATION_MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(list(sent_ids), f, indent=2)

# ──────────────────────────────────────────────────────────────────────────────
# Filtering logic
# -----------------------------------------------------------------------------

def should_keep_publication(pub):
    """
    Decide whether an official statistical bulletin is crop-relevant.

    Strategy
    --------
    1. Translate the title to English (via DeepL) if it is not already.
    2. Look for *strict* crop keywords or *contextual* crop keywords
       (case-insensitive) in either the original or translated text.

    Parameters
    ----------
    pub : dict
        Must contain at least a ``"title"`` key.

    Returns
    -------
    bool
        ``True`` → keep the bulletin, ``False`` → throw it away.

    Notes
    -----
    The translated title is stored back into ``pub["title_translated"]`` so
    downstream HTML rendering can show it as a tooltip.
    """
    title = pub.get("title", "")
    title_lower = title.lower()

    try:
        lang = detect(title)
        if lang != "en":
            translated = GoogleTranslator(source=lang, target="en").translate(title)
        else:
            translated = title
        pub["title_translated"] = translated  # debug only

        translated_lower = translated.lower()

        # ✅ Ajoute test sur le titre brut AVANT traduction
        has_crop_keyword = any(word in title_lower for word in CROP_KEYWORDS) or \
                           any(word in translated_lower for word in CROP_KEYWORDS)

        has_contextual_keyword = any(word in title_lower for word in CROP_CONTEXTUAL_KEYWORDS) or \
                                 any(word in translated_lower for word in CROP_CONTEXTUAL_KEYWORDS)

        if has_crop_keyword or has_contextual_keyword:
            return True
        else:
            logging.info(f"❌ Publication rejetée : aucun mot-clé végétal détecté : '{translated}'")
            return False

    except Exception as e:
        logging.warning(f"Erreur traduction/détection pour '{title}': {e}")
        return False

# ──────────────────────────────────────────────────────────────────────────────
# Official-publication scrapers
# -----------------------------------------------------------------------------

def scrape_agreste():
    """
    French Ministry of Agriculture – *Agreste* statistical bulletins
    (last 48 h).

    Implementation highlights
    -------------------------
    * JSF front-end—no RSS feed available—so a headless Chrome session is
      started and pointed to a GUID-encoded search URL.
    * For each result row:
        – Title and fake permalink are extracted.  
        – The update date “Mis à jour le dd/mm/yyyy” is parsed.
    * Only publications newer than 48 h **and** passing
      :pyfunc:`should_keep_publication` are returned.

    Returns
    -------
    list[dict]
        Each dict has ``title``, ``date`` (``YYYY-MM-DD``) and ``link`` keys.
    """
    logging.info("Début du scraping des publications Agreste")

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    url = "https://agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/4545f1a9-afe6-4c86-a141-693f2c72d550!1b69a349-ca8f-4353-82bb-4c00c502412c!729f399f-53c3-4952-9971-4753794a7c1b!c6be0c43-70a0-4666-853f-80de38a08ec7!0c593aed-b1d0-476e-9359-12d6347d8243!b125c6dc-13b7-4260-9abd-6e9321b2b963!fec0e278-6655-4c48-ac47-aab6d8847e15/search/"
    driver.get(url)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h4.titreSearch"))
        )
    except Exception as e:
        logging.error(f"Timeout : contenu Agreste non chargé : {e}")
        driver.save_screenshot("agreste_error.png")
        driver.quit()
        return []

    articles = []
    now = datetime.now()
    since = now - timedelta(days=2)

    try:
        rows = driver.find_elements(By.CSS_SELECTOR, "h4.titreSearch")
        date_blocks = driver.find_elements(By.CLASS_NAME, "disar-split-panel-right-table-cell-content-info")

        for idx, row in enumerate(rows):
            try:
                a_tag = row.find_element(By.TAG_NAME, "a")
                title = a_tag.get_attribute("title").strip()
                link = a_tag.get_attribute("onclick")
                full_link = url  # fallback

                # Parsing de la date réelle
                try:
                    raw_date = date_blocks[idx].text.strip()
                    match = re.search(r"Mis à jour le (\d{2}/\d{2}/\d{4})", raw_date)
                    if match:
                        date_article = datetime.strptime(match.group(1), "%d/%m/%Y")
                        date_str = date_article.strftime("%Y-%m-%d")
                    else:
                        raise ValueError("Date non trouvée dans le texte")
                except Exception as e:
                    logging.warning(f"Date non trouvée pour '{title}': {e}")
                    date_article = now
                    date_str = now.strftime("%Y-%m-%d")

                if date_article < since:
                    continue

                article = {
                    "title": title,
                    "date": date_str,
                    "link": full_link
                }
                
                if not should_keep_publication(article):
                    continue
                
                articles.append(article)
                logging.info(f"✅ Publication Agreste acceptée: {title}")

            except Exception as e:
                logging.warning(f"Erreur lors de l'extraction d'une ligne Agreste: {e}")

    finally:
        driver.quit()

    logging.info(f"Scraping Agreste terminé: {len(articles)} publications retenues")
    return articles

def scrape_france():
    return {
        "France": scrape_agreste()
    }

def scrape_jrc():
    """
    Scrape les bulletins JRC MARS (titre + lien + date réelle) avec Selenium.
    Conserve ceux publiés dans les 25 derniers jours dont le titre commence par :
      • « JRC MARS Bulletin - Crop monitoring in Europe … »
      • « JRC MARS Bulletin - Global outlook - Crop monitoring European neighbourhood … »
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping JRC MARS avec Selenium")
    base_url = "https://publications.jrc.ec.europa.eu"
    url = (f"{base_url}/repository/search?sort=date-desc"
           "&filter=SCIENCE_AREA%3AS001%7CGROUP%3AG001&query=JRC+Mars+Bulletin")

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-gpu')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.get(url)
    results = []
    since = datetime.utcnow() - timedelta(days=2)

    # ✅ nouveaux préfixes acceptés
    valid_prefixes = (
        "JRC MARS Bulletin - Crop monitoring in Europe",
        "JRC MARS Bulletin - Global outlook - Crop monitoring European neighbourhood"
    )

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a.search-entry-title"))
        )
        anchors = driver.find_elements(By.CSS_SELECTOR, "a.search-entry-title")
        logging.info(f"🔎 {len(anchors)} publications JRC détectées")

        for idx, a in enumerate(anchors):
            title = a.text.strip()
            href = a.get_attribute("href")
            logging.info(f"[JRC-{idx+1}] Analyse : {title}")

            # 🔄 condition mise à jour
            if not any(title.startswith(p) for p in valid_prefixes):
                logging.info(f"[JRC-{idx+1}] ⏭️ Ignoré : titre non conforme")
                continue

            # Charger la page de détail pour récupérer la date
            try:
                driver.execute_script("window.open();")
                driver.switch_to.window(driver.window_handles[-1])
                driver.get(href)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div[data-title="Date available"]'))
                )
                date_text = driver.find_element(By.CSS_SELECTOR, 'div[data-title="Date available"]').text.strip()
                pub_date = date_parser.parse(date_text)
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
            except Exception as e:
                logging.warning(f"[JRC-{idx+1}] ⚠️ Erreur chargement date : {e}")
                pub_date = datetime.utcnow()
                driver.close()
                driver.switch_to.window(driver.window_handles[0])

            if pub_date < since:
                logging.info(f"[JRC-{idx+1}] 📅 Trop ancien ({pub_date.date()})")
                continue

            pub = {
                "title": title,
                "link": href,
                "date": pub_date.strftime("%Y-%m-%d")
            }
            results.append(pub)
            logging.info(f"[JRC-{idx+1}] ✅ Retenu : {title} | {pub['date']}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping JRC via Selenium : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping JRC terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_caa():
    """
    Scrape les publications Cooperativas Agro-Alimentarias en Espagne sur les céréales :
    https://www.agro-alimentarias.coop/documents?cat_sel=33
    Ne garde que les titres commençant par 'CEREALES.'
    """
    logging.info("🔍 Scraping CAA (Espagne) - Céréales")

    url = "https://www.agro-alimentarias.coop/documents?cat_sel=33"
    base_url = "https://www.agro-alimentarias.coop"

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-gpu')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    results = []
    since = datetime.utcnow() - timedelta(days=2)

    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.card-info"))
        )
        cards = driver.find_elements(By.CSS_SELECTOR, "div.card-info")
        logging.info(f"🔎 {len(cards)} cartes détectées")

        for idx, card in enumerate(cards):
            try:
                # ✅ Date de publication
                date_div = card.find_element(By.CSS_SELECTOR, "div.date")
                date_str_raw = date_div.text.split("|")[0].strip()
                pub_date = datetime.strptime(date_str_raw, "%d/%m/%Y")

                if pub_date < since:
                    logging.info(f"[CAA-{idx+1}] ⏭️ Trop ancien : {pub_date.date()}")
                    continue

                # ✅ Titre et lien
                link_tag = card.find_element(By.CSS_SELECTOR, "div.text a")
                title = link_tag.text.strip()
                href = link_tag.get_attribute("href")
                if not href.startswith("http"):
                    href = base_url + href

                logging.info(f"[CAA-{idx+1}] Analyse : {title}")

                if not title.startswith("CEREALES."):
                    logging.info(f"[CAA-{idx+1}] Ignoré (titre non conforme)")
                    continue

                pub = {
                    "title": title,
                    "link": href,
                    "date": pub_date.strftime("%Y-%m-%d")
                }

                results.append(pub)
                logging.info(f"[CAA-{idx+1}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[CAA-{idx+1}] ⚠️ Erreur parsing : {e}")
                continue

    except Exception as e:
        logging.error(f"❌ Erreur scraping CAA : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping CAA terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_statcan():
    """
    StatCan – Crop production (10 premières publications uniques).
    Conserve celles ≤ 30 jours & should_keep_publication().
    """
    logging.info("🔍 Scraping StatCan – Crop Production")
    url = ("https://www150.statcan.gc.ca/n1/en/subjects/"
           "agriculture_and_food/crop_production?count=10")

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless"); opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=opts)

    since = datetime.utcnow() - timedelta(days=2)   # fenêtre temporelle
    results, skipped = [], 0

    try:
        driver.get(url)

        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "div.ndm-result-container"))
        )
        WebDriverWait(driver, 30).until(
            lambda d: re.search(
                r"\d{4}-\d{2}-\d{2}",
                d.find_element(
                    By.CSS_SELECTOR,
                    "div.ndm-result-date span.ndm-result-date:last-child"
                ).text)
        )

        soup = BeautifulSoup(driver.page_source, "html.parser")
        boxes = soup.select("div.ndm-result-container")
        logging.info(f"🔎 {len(boxes)} publications StatCan détectées")

        seen_links = set()
        idx = 0

        for box in boxes:
            if len(results) == 10:      # ➜ stop dès qu’on a 10 uniques
                break

            title_tag = box.select_one("div.ndm-result-title a")
            date_span = box.select("div.ndm-result-date span.ndm-result-date")[-1]
            if not title_tag or not date_span:
                skipped += 1
                continue

            link = title_tag["href"].strip()
            if link in seen_links:      # ➜ dé-duplication
                continue
            seen_links.add(link)

            idx += 1
            title = title_tag.get_text(strip=True)
            date_str = date_span.get_text(strip=True)
            pub_date = datetime.strptime(date_str, "%Y-%m-%d")
            if pub_date < since:
                continue

            pub = {
                "title": title,
                "link": link,
                "date": pub_date.strftime("%Y-%m-%d")
            }

            if should_keep_publication(pub):
                results.append(pub)
                logging.info(f"[STATCAN-{idx}] ✅ {title} | {pub['date']}")
            else:
                logging.info(f"[STATCAN-{idx}] ❌ Rejeté (mots-clés) : {title}")

    except Exception as e:
        logging.error(f"❌ StatCan error : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ StatCan terminé : {len(results)} retenu(s), {skipped} ignoré(s)")
    return results

# ─── Traduction titre + conversion mois turcs ───────────────────────────────────
TUIK_TITLE_MAP = {
    "Bitkisel Üretim İstatistikleri": "Crop production statistics",
    "Bitkisel Üretim": "Crop production"
}
TURKISH_MONTHS = {
    "Ocak": "January", "Şubat": "February", "Subat": "February", "Mart": "March",
    "Nisan": "April", "Mayıs": "May", "Mayis": "May", "Haziran": "June",
    "Temmuz": "July", "Ağustos": "August", "Agustos": "August",
    "Eylül": "September", "Eylul": "September", "Ekim": "October",
    "Kasım": "November", "Kasim": "November", "Aralık": "December",
    "Aralik": "December"
}

def translate_tuik_title(tr_title: str) -> str:
    for tr, en in TUIK_TITLE_MAP.items():
        if tr_title.startswith(tr):
            suffix = tr_title[len(tr):].lstrip(" ,.-")
            return f"{en} {suffix}".strip()
    return tr_title

def parse_tuik_date(date_str: str) -> Optional[datetime]:
    """
    Remplace le mois turc par son équivalent anglais puis parse la date.
    Retourne None si la date est vide ou illisible.
    """
    if not date_str:
        return None
    for tr, en in TURKISH_MONTHS.items():
        date_str = date_str.replace(tr, en)
    try:
        return date_parser.parse(date_str, dayfirst=True)
    except Exception:
        return None
# ────────────────────────────────────────────────────────────────────────────────


def scrape_tuik():
    """
    Scrape TÜİK crop bulletins :
    - garde 'Bitkisel Üretim…' / 'Bitkisel Üretim İstatistikleri…'
    - traduit le titre en anglais.
    """
    logging.info("🔍 Scraping TÜİK (Turkey) - Crop Bulletins")

    url = "https://data.tuik.gov.tr/Kategori/GetKategori?p=tarim-111&dil=1"
    base_url = "https://data.tuik.gov.tr"

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-gpu")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    results = []
    since = datetime.utcnow() - timedelta(days=2)

    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr[role='row']"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "tr[role='row']")
        logging.info(f"🔎 {len(rows)} lignes détectées")

        for idx, row in enumerate(rows, start=1):
            try:
                # Lien bulletin : si absent → ligne non pertinente (ex. header)
                link_el = row.find_elements(By.CSS_SELECTOR, "div.news-content a")
                if not link_el:
                    continue
                link_el = link_el[0]
                title_tr = link_el.text.strip()
                if not (title_tr.startswith("Bitkisel Üretim") or
                        title_tr.startswith("Bitkisel Üretim İstatistikleri")):
                    continue
                link = link_el.get_attribute("href")
                if not link.startswith("http"):
                    link = base_url + link

                # ---- Date ----
                date_text = ""
                # 1) span flottant
                spans = row.find_elements(By.CSS_SELECTOR, "span.float-right")
                if spans:
                    date_text = spans[0].text.strip()
                # 2) fallback : premier <td>
                if not date_text:
                    tds = row.find_elements(By.TAG_NAME, "td")
                    if tds:
                        date_text = tds[0].get_attribute("innerText").strip()

                pub_date = parse_tuik_date(date_text)
                if not pub_date:
                    logging.info(f"[TUIK-{idx}] ⏭️ Date illisible : '{date_text}'")
                    continue
                if pub_date < since:
                    continue

                title_en = translate_tuik_title(title_tr)

                results.append({
                    "title": title_en,
                    "link": link,
                    "date": pub_date.strftime("%Y-%m-%d")
                })
                logging.info(f"[TUIK-{idx}] ✅ {title_en} | {pub_date.date()}")

            except Exception as e:
                logging.warning(f"[TUIK-{idx}] ⚠️ Erreur : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur principale TÜİK : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping TÜİK terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_uk():
    """
    Scrape DEFRA (UK) – publications statistiques.
    Ne garde que les titres commençant par 'Agricultural land use'.
    Fenêtre : 48 h.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping DEFRA (UK) – Agricultural land use")
    base_url = "https://www.gov.uk"
    url = (
        f"{base_url}/search/all?"
        "content_purpose_supergroup[]=research_and_statistics"
        "&keywords=crops"
        "&order=updated-newest"
        "&organisations[]=department-for-environment-food-rural-affairs"
        "&page=1"
        "&parent=department-for-environment-food-rural-affairs"
    )

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=opts)

    since = datetime.utcnow() - timedelta(days=2)
    results = []

    try:
        driver.get(url)

        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "li.gem-c-document-list__item"))
        )
        items = driver.find_elements(By.CSS_SELECTOR, "li.gem-c-document-list__item")
        logging.info(f"🔎 {len(items)} bulletins DEFRA détectés")

        for idx, li in enumerate(items, 1):
            try:
                a_tag = li.find_element(By.CSS_SELECTOR, "div.gem-c-document-list__item-title a")
                title = a_tag.text.strip()
                if not title.startswith("Agricultural land use"):
                    logging.info(f"[UK-{idx}] ⏭️ Ignoré : titre non conforme")
                    continue

                href = a_tag.get_attribute("href")
                if href.startswith("/"):
                    href = base_url + href

                time_tag = li.find_element(By.CSS_SELECTOR, "time")
                date_str = time_tag.get_attribute("datetime") or time_tag.text
                pub_date = date_parser.parse(date_str)

                if pub_date < since:
                    logging.info(f"[UK-{idx}] 📅 Trop ancien ({pub_date.date()})")
                    continue

                pub = {
                    "title": title,
                    "link": href,
                    "date": pub_date.strftime("%Y-%m-%d")
                }
                results.append(pub)
                logging.info(f"[UK-{idx}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[UK-{idx}] ⚠️ Erreur parsing ligne : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping DEFRA : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping DEFRA terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_ksh():
    """
    Scrape KSH (Hongrie) – publications agricoles.
    Conserve toutes les publications ≤ 2 jours (pas de should_keep_publication).
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping KSH (Hungary) – Agriculture")
    base_url = "https://www.ksh.hu"
    url = f"{base_url}/apps/shop.lista?p_lang=EN&p_temakor_kod=OM"

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=opts)

    since = datetime.utcnow() - timedelta(days=2)
    results = []

    try:
        driver.get(url)

        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "td.descr"))
        )
        cells = driver.find_elements(By.CSS_SELECTOR, "td.descr")
        logging.info(f"🔎 {len(cells)} publications KSH détectées")

        for idx, cell in enumerate(cells, 1):
            try:
                a_tag = cell.find_element(By.CSS_SELECTOR, "div.pub_title a")
                title = a_tag.text.strip()
                href = a_tag.get_attribute("href")
                if href.startswith("/"):
                    href = base_url + href

                date_text = cell.find_element(By.CSS_SELECTOR, "div.pub_start b").text.strip()
                pub_date = datetime.strptime(date_text, "%d/%m/%Y")

                if pub_date < since:
                    logging.info(f"[KSH-{idx}] 📅 Trop ancien ({pub_date.date()})")
                    continue

                pub = {
                    "title": title,
                    "link": href,
                    "date": pub_date.strftime("%Y-%m-%d")
                }
                results.append(pub)
                logging.info(f"[KSH-{idx}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[KSH-{idx}] ⚠️ Erreur parsing ligne : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping KSH : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping KSH terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_coceral():
    """
    Scrape COCERAL crop forecast bulletins.
    Limité aux 10 premières publications de la page.
    Conserve toutes les publications ≤ 2 j, sans should_keep_publication.
    Lien unique = page d'origine.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping COCERAL – Crop Forecasts")
    page_url = ("https://www.coceral.com/web/coceral%20crop%20forecast%7C%20"
                "june%202025/1011306087/list1187970814/f1.html")

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=opts)

    since = datetime.utcnow() - timedelta(days=2)
    results = []

    try:
        driver.get(page_url)

        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.antpar1"))
        )
        blocks = driver.find_elements(By.CSS_SELECTOR, "div.antpar1")
        logging.info(f"🔎 {len(blocks)} bulletins COCERAL détectés")

        # Ne prendre que les 10 premiers bulletins
        for idx, blk in enumerate(blocks[:10], 1):
            try:
                title = blk.find_element(By.TAG_NAME, "h1").text.strip()

                # Date : premier <strong> du paragraphe descriptif
                strong_text = blk.find_element(By.CSS_SELECTOR, "div.par1descr strong").text.strip()
                m = re.search(r'(\d{1,2}\s+\w+\s+\d{4})', strong_text)
                if not m:
                    logging.info(f"[COCERAL-{idx}] ⏭️ Date introuvable")
                    continue
                pub_date = date_parser.parse(m.group(1))

                if pub_date < since:
                    logging.info(f"[COCERAL-{idx}] 📅 Trop ancien ({pub_date.date()})")
                    continue

                pub = {
                    "title": title,
                    "link": page_url,  # lien fixe demandé
                    "date": pub_date.strftime("%Y-%m-%d")
                }
                results.append(pub)
                logging.info(f"[COCERAL-{idx}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[COCERAL-{idx}] ⚠️ Erreur parsing bloc : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping COCERAL : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping COCERAL terminé : {len(results)} publication(s) retenue(s)")
    return results
    
def scrape_destatis():
    """
    Scrape Destatis (Allemagne) – résultats de recherche cultures.
    Conserve les 10 premières publications ≤ 2 j, sans should_keep_publication.
    Corrige le titre et le lien pour qu'ils soient propres.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping Destatis (Germany) – Field Crops")
    base_url = "https://www.destatis.de"
    url = (
        "https://www.destatis.de/SiteGlobals/Forms/Suche/EN/"
        "Expertensuche_Formular.html?templateQueryString=crops"
        "&cl2Taxonomies_Themen_0=land_forstwirtschaft_fischerei#searchresults"
    )

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    since = datetime.utcnow() - timedelta(days=2)
    results = []

    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.c-result"))
        )

        soup = BeautifulSoup(driver.page_source, "html.parser")
        blocks = soup.select("div.c-result")
        logging.info(f"🔎 {len(blocks)} publications Destatis détectées")

        for idx, blk in enumerate(blocks[:10], 1):
            try:
                h3 = blk.select_one("h3.c-result__heading")
                a_tag = h3.find("a")
                # ---- Nettoyage titre ----
                # Supprimer "Date:" s'il existe
                raw_text = a_tag.get_text(" ", strip=True).replace("Date:", "").strip()
                # Retirer la date au début s'il y en a une ("May 19, 2025 ...")
                m = re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}\s*(.*)", raw_text)
                title = m.group(1).strip() if m else raw_text

                # ---- Lien absolu ----
                link = a_tag.get("href")
                if link.startswith("/"):
                    link = base_url + link
                elif link.startswith("EN/"):
                    link = base_url + "/" + link
                # sinon lien déjà absolu

                # ---- Date ----
                date_tag = a_tag.find("span", class_="c-result__date")
                if not date_tag or not date_tag.text.strip():
                    logging.info(f"[DESTATIS-{idx}] ⏭️ Date introuvable")
                    continue
                date_str = date_tag.text.strip()
                pub_date = date_parser.parse(date_str)

                if pub_date < since:
                    logging.info(f"[DESTATIS-{idx}] 📅 Trop ancien ({pub_date.date()})")
                    continue

                pub = {
                    "title": title,
                    "link": link,
                    "date": pub_date.strftime("%Y-%m-%d")
                }
                results.append(pub)
                logging.info(f"[DESTATIS-{idx}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[DESTATIS-{idx}] ⚠️ Erreur parsing bloc : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping Destatis : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping Destatis terminé : {len(results)} publication(s) retenue(s)")
    return results

def scrape_govua():
    """
    Scrape Stat Gov UA – résultats 'crops'.
    Analyse les 10 premières publications, conserve celles dont le titre contient 'crops'.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info("🔍 Scraping Stat Gov UA – Crops")
    base_url = "https://stat.gov.ua"
    url = (
        f"{base_url}/en/search?f%5B0%5D=content_type%3Adataset"
        "&f%5B1%5D=topics%3A178"
        "&search_api_fulltext=crops"
        "&sort_by=created"
    )

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    since = datetime.utcnow() - timedelta(days=2)
    results = []

    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.views-row"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "div.views-row")
        logging.info(f"🔎 {len(rows)} publications Gov UA détectées")

        for idx, row in enumerate(rows[:10], 1):
            try:
                # Titre (dans le <a>)
                a_tag = row.find_element(By.CSS_SELECTOR, "div.node__title h2 a")
                title = a_tag.text.strip()
                if "crops" not in title.lower():
                    logging.info(f"[GOVUA-{idx}] ⏭️ Ignoré : titre sans 'crops'")
                    continue

                link = a_tag.get_attribute("href")
                if link.startswith("/"):
                    link = base_url + link

                # Date (dans <span class="ico-time--status"> + date juste après)
                # Cherche un pattern "DD  Mon,  YYYY" dans le texte du bloc node__title-info
                title_info = row.find_element(By.CSS_SELECTOR, "div.node__title-info").text
                # Regex pour matcher le format "10  Jun,  2025"
                m = re.search(r'(\d{1,2}\s+\w{3},\s+\d{4})', title_info)
                if not m:
                    logging.info(f"[GOVUA-{idx}] ⏭️ Date introuvable")
                    continue
                pub_date = date_parser.parse(m.group(1))

                if pub_date < since:
                    logging.info(f"[GOVUA-{idx}] 📅 Trop ancien ({pub_date.date()})")
                    continue

                pub = {
                    "title": title,
                    "link": link,
                    "date": pub_date.strftime("%Y-%m-%d")
                }
                results.append(pub)
                logging.info(f"[GOVUA-{idx}] ✅ Retenu : {title} | {pub['date']}")

            except Exception as e:
                logging.warning(f"[GOVUA-{idx}] ⚠️ Erreur parsing bloc : {e}")

    except Exception as e:
        logging.error(f"❌ Erreur scraping Gov UA : {e}")
    finally:
        driver.quit()

    logging.info(f"✅ Scraping Gov UA terminé : {len(results)} publication(s) retenue(s)")
    return results
    
OFFICIAL_SOURCES = {
    "France": {
    "rss": [],
    "scrapers": [scrape_agreste]
    },
    "Spain": {
        "rss": ["https://www.mapa.gob.es/es/agricultura/noticiasRss.aspx"],
        "scrapers": [scrape_caa]
    },
    "Hungary": {
        "rss": ["https://www.ksh.hu/apps/shop.rss_temakor?p_lang=EN&p_temakor_kod=KSH"],
        "scrapers": [scrape_ksh]
    },
    "Germany": {
        "rss": ["http://www.destatis.de/Aktuelles.xml"],
        "scrapers": [scrape_destatis]
    },
    "Canada": {
        "rss": ["https://www150.statcan.gc.ca/n1/rss/dai-quo/32-eng.atom"],
        "scrapers": [scrape_statcan]
    },
    "Romania": {
        "rss": ["https://insse.ro/cms/files/rss_ins_en.xml"],
        "scrapers": []
    },
    "Turkey": {
        "rss": [],
        "scrapers": [scrape_tuik]
    },
    "UK": {
        "rss": [],
        "scrapers": [scrape_uk]
    },
    "Italy": {
        "rss": ["https://www.istat.it/en/tema/agriculture/feed/"],
        "scrapers": []
    },
    "Ukraine": {
        "rss": [],
        "scrapers": [scrape_govua]
    },
    "Europe": {
        "rss": [
            "https://ec.europa.eu/eurostat/en/search?p_p_id=estatsearchportlet_WAR_estatsearchportlet&p_p_lifecycle=2&p_p_state=maximized&p_p_mode=view&p_p_resource_id=atom&_estatsearchportlet_WAR_estatsearchportlet_theme=PER_AGRFIS&_estatsearchportlet_WAR_estatsearchportlet_collection=CAT_EURNEW",
            "https://ec.europa.eu/eurostat/en/search?p_p_id=estatsearchportlet_WAR_estatsearchportlet&p_p_lifecycle=2&p_p_state=maximized&p_p_mode=view&p_p_resource_id=atom&_estatsearchportlet_WAR_estatsearchportlet_theme=PER_AGRFIS&_estatsearchportlet_WAR_estatsearchportlet_collection=dataset"
        ],
        "scrapers": [scrape_jrc, scrape_coceral]
    }
}

def fetch_official_publications():
    """
    Récupère les publications officielles des 48 h dernières.
    Retourne ({zone:[pubs]}, total, acceptées, rejetées)
    """
    logging.info("Récupération des publications officielles")
    results, pub_total, pub_accepted, pub_rejected = {}, 0, 0, 0
    now, since = datetime.utcnow(), datetime.utcnow() - timedelta(days=2)

    def extract_date(entry, rss_url):
        """Essaie toutes les variantes de date et retourne un datetime ou None."""
        # tuples struct_time
        if getattr(entry, "published_parsed", None):
            return datetime(*entry.published_parsed[:6])
        if getattr(entry, "updated_parsed", None):
            return datetime(*entry.updated_parsed[:6])

        # chaînes RFC 822 / ISO
        for key in ("published", "updated", "date", "created",
                    "issued", "modified", "pubdate"):
            val = getattr(entry, key, "") or entry.get(key, "") if isinstance(entry, dict) else ""
            if val.strip():
                dayfirst = "mapa.gob.es" in rss_url
                return date_parser.parse(val, dayfirst=dayfirst)
        return None

    for zone, cfg in OFFICIAL_SOURCES.items():
        results[zone] = []

        # ---------------- RSS ----------------
        for rss_url in cfg.get("rss", []):
            logging.info(f"Lecture du flux officiel RSS [{zone}]: {rss_url}")
            try:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries:
                    pub_date = extract_date(entry, rss_url)
                    if not pub_date:
                        # Date manquante ou vide : on ignore simplement l’item
                        continue

                    if pub_date > now or pub_date < since:
                        continue

                    pub = {
                        "title": getattr(entry, "title", "No title"),
                        "link": getattr(entry, "link", "#"),
                        "date": pub_date.strftime("%Y-%m-%d")
                    }

                    pub_total += 1
                    if should_keep_publication(pub):
                        results[zone].append(pub)
                        pub_accepted += 1
                    else:
                        pub_rejected += 1

            except Exception as e:
                logging.error(f"Erreur flux RSS [{zone}]: {e}")

        # ---------------- Scrapers ----------------
        for scraper in cfg.get("scrapers", []):
            try:
                for pub in scraper():
                    pub_total += 1
                    results[zone].append(pub)
                    pub_accepted += 1
            except Exception as e:
                logging.error(f"Erreur scraper [{zone}]: {e}")

    return results, pub_total, pub_accepted, pub_rejected

# ──────────────────────────────────────────────────────────────────────────────
# Article-level utilities
# -----------------------------------------------------------------------------

def truncate_title(title, max_length=200):
    """
    Clip a long title to *max_length* characters without breaking the last
    word, then append an ellipsis “…” if clipping occurred.
    """
    return title if len(title) <= max_length else title[:max_length].rstrip() + "..."

def hash_article(article):
    """
    Create a deterministic MD5 hash of an article URL so that duplicates
    can be detected across runs.
    """
    return hashlib.md5(article["link"].encode()).hexdigest()

def already_sent(article_hash):
    """
    Return *True* if *article_hash* is found in *sent_articles.json*,
    i.e. the story has already been emailed in a previous digest.
    """
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f:
            json.dump([], f)
        return False
    with open(HISTORY_FILE, "r") as f:
        try:
            sent_articles = json.load(f)
        except json.JSONDecodeError:
            sent_articles = []
    return article_hash in sent_articles

def log_article(article_hash):
    """
    Append *article_hash* to *sent_articles.json*, pruning the file so it
    never exceeds 1 000 entries (simple poor-man’s rotation).
    """
    if not os.path.exists(HISTORY_FILE):
        sent_articles = []
    else:
        with open(HISTORY_FILE, "r") as f:
            try:
                sent_articles = json.load(f)
            except json.JSONDecodeError:
                sent_articles = []
    sent_articles.append(article_hash)
    if len(sent_articles) > 1000:
        sent_articles = sent_articles[-1000:]
    with open(HISTORY_FILE, "w") as f:
        json.dump(sent_articles, f)

def get_recent_articles():
    """
    Parse every RSS feed in :pydata:`FEED_URLS`, keep stories no older than
    *DAYS_TO_LOOK_BACK*, drop obvious duplicates, and return both the
    surviving articles and a count of RSS feeds that failed to load.
    """
    all_articles = []
    error_count = 0
    for url in FEED_URLS:
        try:
            logging.info(f"Traitement du flux: {url}")
            feed = feedparser.parse(url)
            if hasattr(feed, 'bozo_exception') and feed.bozo_exception:
                logging.warning(f"Avertissement pour {url}: {feed.bozo_exception}")
            for entry in feed.entries:
                try:
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        published = datetime(*entry.published_parsed[:6])
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        published = datetime(*entry.updated_parsed[:6])
                    else:
                        published = datetime.now()
                    if published > datetime.now() - timedelta(days=DAYS_TO_LOOK_BACK):
                        if hasattr(entry, 'summary'):
                            summary_html = entry.summary
                        elif hasattr(entry, 'content') and entry.content:
                            summary_html = entry.content[0].value
                        else:
                            summary_html = "<p>No summary available.</p>"
                        h = html2text.HTML2Text()
                        h.ignore_links = True
                        h.ignore_images = True
                        summary_text = h.handle(summary_html).strip()

                        # 🔧 Nettoyage : supprimer les balises HTML résiduelles de mise en forme
                        summary_text = re.sub(r'</?(strong|b|em|i)>', '', summary_text)

                        # 🔒 Troncature
                        summary_text = (summary_text[:1500] + '...') if len(summary_text) > 1500 else summary_text

                        all_articles.append({
                            "title": entry.title if hasattr(entry, 'title') else "No title",
                            "link": entry.link if hasattr(entry, 'link') else url,
                            "published": published,
                            "summary_original": summary_text,
                            "source": feed.feed.title if hasattr(feed, 'feed') and hasattr(feed.feed, 'title') else url
                        })
                except (AttributeError, TypeError, IndexError) as e:
                    logging.error(f"Erreur avec l'entrée du flux {url} pour l'article '{getattr(entry, 'title', 'N/A')}': {e}")
                    error_count += 1
        except Exception as e:
            logging.error(f"Erreur lors de la lecture du flux {url}: {e}")
            error_count += 1
    all_articles.sort(key=lambda x: x["published"], reverse=True)
    if len(all_articles) > MAX_ARTICLES_PER_EMAIL:
        all_articles = all_articles[:MAX_ARTICLES_PER_EMAIL]
    logging.info(f"Articles récupérés: {len(all_articles)}, Erreurs de flux: {error_count}")
    return all_articles, error_count

def summarize_and_categorize_mistral(text_to_process, lang="unknown", max_retries=5):
    """
    Translate, summarise, categorise and tag a news article in **one** call
    to the Mistral AI API.

    Workflow
    --------
    1. **Translate** *text* to English if *lang* is not ``"en"``.  
    2. **Summarise** the content in **exactly three** Markdown bullet points.  
    3. **Assign** one of the accepted high-level categories  
       (Vegetal crops / Agri-tech / Climate).  
    4. **Return** a concise semantic *tag* (single noun phrase).

    Reliability
    -----------
    * A shared :pydata:`mistral_rate_limiter` enforces *one request per
      second* across the whole process.
    * Transient API errors trigger exponential back-off with random jitter,
      up to *max_retries* attempts.

    Parameters
    ----------
    text : str
        Raw article body or RSS description to analyse.
    lang : str, optional
        ISO-639-1 language code previously detected; only used to decide
        whether the translation step is required. Defaults to ``"unknown"``.
    max_retries : int, optional
        Maximum number of retries on recoverable Mistral errors
        (HTTP 5xx, rate-limit …). Default is 5.

    Returns
    -------
    dict
        A mapping with four keys:  
        ``translation`` → English version of *text* (or original),  
        ``summary`` → three-line Markdown bullet summary,  
        ``category`` → one of the accepted categories,  
        ``tag`` → short semantic keyword.

    Raises
    ------
    RuntimeError
        If all retry attempts fail to obtain a response from Mistral AI.
    """
    if not MISTRAL_API_KEY:
        logging.error("Clé API Mistral non disponible pour la traduction/résumé.")
        return {"summary": "Error: Mistral API key not configured.", "category": "Erreur"}

    client = Mistral(api_key=MISTRAL_API_KEY)
    
    global mistral_rate_limiter

    prompt = f"""You are a global expert in agriculture specializing in vegetal crop markets, production, and management. Classify agricultural news for experienced industry professionals who need quick, accurate summaries and labels.

Return your response in the exact format below (no extra text before or after):
---
Summary:
- Bullet point 1
- Bullet point 2
- Bullet point 3

Category: <Vegetal crops / Agri-tech / Climate / Rejected>
Tag: <exactly one tag from the list below>
---

Process
1) Language
- If the text is not in English, translate it into English first.

2) Summary (exactly 3 bullets)
- Write exactly three concise, fact-dense bullets (one sentence each).
- Focus on key insights: specific crops, metrics (prices, acreage, yields, % changes), timeframes, geographies, companies/policies.
- Avoid marketing fluff and speculation; do not invent facts.

3) Category (choose one, most precise)
- Vegetal crops → Only if the article specifically relates to one or more of these cultivated plants: alfalfa, beans, beet, hemp, linen, maize/corn, cereals, peas, potato, soy, sorghum, barley, wheat, sunflower, triticale, canola. Topics can include acreage/land use, yields, prices, seeds, crop protection, weather or market trends — but only if directly linked to at least one listed crop.
- Agri-tech → Digital/analytics innovations, companies or tools in agtech: sensors, drones, satellite monitoring, remote sensing, decision support systems, digital platforms, start-up funding/partnerships/buyouts. Exclude heavy machinery, field robotics, genetic engineering, or plant breeding tools unless directly linked to the listed crops.
- Climate → Weather conditions affecting vegetal crops, climate impact on crops, regenerative agriculture practices/programs. Reject general weather/disasters unless crop impact is mentioned.
- Rejected → Use when none of the above fit, or if the article is about: livestock/animal topics; politics/infrastructure/general environment without direct impact on vegetal crops; biotech/genetics/AI with no clear vegetal-crop use case; protests/litigation/public campaigns unrelated to crop production/policy; very vague/promotional content without factual substance.

Tie-breakers and edge cases
- If a listed crop is explicitly named with a direct impact (acreage, yield, price, inputs), prefer Vegetal crops over Climate/Agri-tech when ambiguous.
- If weather/climate or regenerative topics are discussed with clear crop impact but no specific listed crop is named, choose Climate.
- Agri-tech applies to digital tools for crop production even if no specific listed crop is named; if the tool is unrelated to crop production or is heavy machinery/robotics/genetics without crop linkage, use Rejected.
- For multi-topic articles, choose the category that reflects the dominant theme affecting vegetal crops.

4) Tag (pick exactly one; use the exact string including emoji and trailing pipe)
- 🚀 AgTech | → sensors, remote sensing, satellite monitoring, decision support, digital farming, platforms, start-up funding/partnerships/buyouts, AI, generative AI
- 🌍 Climate | → weather, droughts, floods, climate impact, CO2, emissions, regenerative agriculture, agroecology, sustainable agriculture, carbon farming
- 💧 Irrigation | → irrigation, water use, water efficiency
- ⚖️ Regulation | → policies, subsidies, bans, laws, certifications, regulatory restrictions
- 💸 Market | → price trends, trade flows, supply/demand, market forecasts
- 🌾 Crop land use | → crop acreages, YoY acreage evolution
- 📈 Yields | → crop yields, YoY yield evolution, weather impact on yields
- 🌱 Seeds | → seed genetics/varieties/production/certification/multiplication/commercialization
- 🛡️ Crop Protection | → pesticides, biocontrol, insecticides, fungicides, herbicides, nematicides, resistance management
- 🧪 Crop Nutrition | → fertilizers, biostimulants, foliar nutrition, nutrient management, fertilization tools
- 🤷 Misc | → only if no tag above fits precisely

Validation checklist
- Exactly 3 bullet points present.
- Category is one of: Vegetal crops / Agri-tech / Climate / Rejected.
- Exactly one Tag chosen from the list above; format includes emoji and trailing pipe.

Examples (abridged)
Example A (Vegetal crops)
Input: USDA raises 2025 U.S. corn yield forecast to 183 bu/acre; futures fall 2%; Midwest weather improving.
Output:
---
Summary:
- USDA lifted 2025 U.S. corn yield outlook to 183 bu/acre on improved Midwest weather.
- Chicago corn futures fell about 2% following the higher yield projection.
- Traders reassessed supply prospects as better conditions support production.

Category: Vegetal crops
Tag: 📈 Yields |
---

Example B (Agri-tech)
Input: French start-up secures €12m to scale satellite-based crop stress analytics for European arable farms.
Output:
---
Summary:
- A French start-up raised €12m to expand satellite-based crop stress analytics.
- The platform targets European arable farms with field-level recommendations.
- Funds will accelerate product development and regional customer onboarding.

Category: Agri-tech
Tag: 🚀 AgTech |
---

Example C (Climate)
Input: Persistent drought cuts Western Europe cereal output; analysts trim harvest outlook despite limited area changes.
Output:
---
Summary:
- Prolonged drought across Western Europe is reducing cereal production potential.
- Analysts lowered harvest forecasts despite stable sown areas.
- Water stress remains the main risk to grain yields in coming weeks.

Category: Climate
Tag: 🌍 Climate |
---

Example D (Rejected)
Input: Government announces new subsidies for dairy herd expansion to boost milk output.
Output:
---
Summary:
- The government introduced subsidies to expand dairy herds and milk production.
- Measures target livestock producers with incentives for herd growth.
- No direct implications for vegetal crop production were specified.

Category: Rejected
Tag: 🤷 Misc |
---

Text to process: {text_to_process}
"""

    # --- Appel Mistral avec résilience (concurrency cap + pacing + Retry-After + jitter + CB)
    messages_payload = [{"role": "user", "content": prompt}]

    # État courant du modèle (mutable pour la fermeture)
    _current = {"idx": 0}
    _models = MISTRAL_MODELS  # ex: ["mistral-medium-2508", "mistral-small-2407"]

    def make_call():
        model_id = _models[_current["idx"]]
        return client.chat.complete(
            model=model_id,
            messages=messages_payload,
            temperature=0.3
        )

    def rotate_model():
        # passe au modèle suivant (reste sur le dernier si un seul)
        if len(_models) > 1:
            _current["idx"] = (_current["idx"] + 1) % len(_models)
            logging.warning(f"[Mistral] switched model to '{_models[_current['idx']]}'")

    try:
        response = mistral_complete_with_resilience(
            make_call,
            logger=logging,
            on_capacity_exceeded=rotate_model  # fallback auto si 3505
        )

        content = response.choices[0].message.content.strip()

        # Extraction robuste (tolère lignes vides et casse)
        summary_match  = re.search(r"(?is)Summary:\s*(.*?)\n+(?:Category:|$)", content)
        category_match = re.search(r"(?i)Category:\s*(.+)", content)
        tag_match      = re.search(r"(?i)Tag:\s*(.+)", content)

        summary = summary_match.group(1).strip() if summary_match else "Error: summary not found"

        raw_category = category_match.group(1).strip() if category_match else "Erreur"
        try:
            category = sanitize_label(raw_category) if 'sanitize_label' in globals() else raw_category
        except Exception:
            category = raw_category

        tag = tag_match.group(1).strip() if tag_match else "🤷 Misc"

        logging.debug(f"Category raw='{raw_category}' → normalized='{category}'")

        return {"summary": summary, "category": category, "tag": tag}

    except Exception as e:
        logging.error(f"Échec après {MAX_RETRIES} tentatives: {e}")
        return {
            "summary": "Error: Mistral API failed after multiple retries.",
            "category": "Erreur",
            "tag": "🤷 Misc"
        }

# ──────────────────────────────────────────────────────────────────────────────
# HTML builders
# -----------------------------------------------------------------------------

def generate_official_publications_html(publications_by_zone):
    """
    Build the HTML snippet for the “Latest official publications” section.
    Zones are rendered alphabetically and prefixed with an emoji flag when
    available.
    """
    html = """
    <div style="margin: 30px 0; padding: 15px; background-color: #F0F4FA;">
        <h2 style="margin-top: 0; color: #14213D;">💯 Latest Official Publications</h2>
    """

    if not publications_by_zone or all(len(v) == 0 for v in publications_by_zone.values()):
        html += """
        <p style="padding: 10px; color: #14213D; border-left: 3px solid #69BE82;">
            No new official publications over the last 48 hours.
        </p>
        </div>
        """
        return html

    for zone, publications in publications_by_zone.items():
        if not publications:
            continue

        flag = ZONE_FLAGS.get(zone, "📍")
        html += f"""
        <h3 style="color: #14213D;">{flag} {zone} {flag}</h3>
        """

        html += '<ul style="list-style: none; padding-left: 0; margin-top: 5px;">'

        for pub in publications:
            title = truncate_title(pub["title"])
            translated = pub.get("title_translated", pub["title"])
            date = format_display_date(pub["date"])
            link = pub["link"]

            html += f"""
            <li style="margin-bottom: 10px; padding-left: 10px; border-left: 3px solid #69BE82;">
                <a href="{link}" title="{translated}" style="color: #69BE82; text-decoration: none;">
                    {title}
                </a>
                <span style="font-size: 0.9em; color: #555;"> | 🗓️ {date}</span>
            </li>
            """

        html += "</ul>"

    html += "</div>"
    return html

def generate_newsletter_html(articles, publications_by_zone=None):
    """
    Assemble the full responsive-email HTML body.

    Parameters
    ----------
    articles : list[dict]
        News stories that survived all filters and were summarised.
    publications_by_zone : dict[str, list[dict]], optional
        Official bulletins grouped by country/zone.

    Returns
    -------
    str
        A complete HTML page ready to be sent through Gmail SMTP.
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{NEWSLETTER_TITLE}</title>
    <style>
      body {{ font-family: Aptos, 'Segoe UI', Tahoma, sans-serif; line-height: 1.6; max-width: 700px; margin: 0 auto; padding: 20px; color: #14213D; }}
      h1 {{ text-align: center; margin-top: 20px; font-size: 1.6em; }}
      h2 {{ margin-top: 40px; font-size: 1.4em; }}
      h3 {{ margin-bottom: 5px; font-size: 1.2em; }}
      ul {{ margin-top: 5px; padding-left: 20px; }}
      ul li {{ font-size: 0.95em; }}
      .category {{ font-size: 0.95em; color: #555; font-style: italic; }}
      .tag {{ font-weight: bold; color: #333; font-size: 0.95em; }}
      .banner {{ width: 100%; max-width: 700px; display: block; margin: 0 auto; }}
      a {{ color: #69BE82; text-decoration: none; }}
    </style>

</head>
<body>
    <div id="top"></div>
    <img src="{BANNER_URL}" alt="Hyperplan" class="banner">
    <h1>{NEWSLETTER_TITLE}</h1>
"""

    # Section contenant les dernières publications officielles au-dessus du sommaire, même si vide
    html += generate_official_publications_html(publications_by_zone)

    # 👉 Étape 1 : regrouper les articles par tag
    articles_by_tag = {}
    for i, article in enumerate(articles):
        tag = article.get("tag", "🤷 Misc |")
        articles_by_tag.setdefault(tag, []).append((i, article))

    # 👉 Étape 2 : générer le sommaire
    # Encadrement de la section Today's news avec fond vert clair
    html += """
    <div style="margin: 30px 0; padding: 15px; background-color: #F0F9F3;">
    <h2 style="margin-top: 0; color: #69BE82;">🌍 Today's News</h2>
    <ul>
    """
    
    if not articles_by_tag:
        html += """
        <p style="padding: 10px; color: #14213D; border-left: 3px solid #69BE82;">
            No new relevant article over the last 24 hours.
        </p>
        """

    for tag, items in articles_by_tag.items():
        html += f"<li><strong>{tag}</strong>\n<ul>\n"
        for i, article in items:
            html += f'<li><a href="#article-{i}" style="color: #69BE82; text-decoration: none;">{truncate_title(article["title"])}</a></li>\n'
        html += "</ul></li>\n"
    html += """
    </ul>
    </div>
    """

    # 👉 Étape 3 : afficher les articles
    for i, article in enumerate(articles):
        summary_data = article.get("summary_mistral", {})
        summary_text = summary_data["summary"] if isinstance(summary_data, dict) else summary_data
        tag = article.get("tag", "🤷 Misc |")
        source = article.get("source", "Unknown source")
        html += f"""
        <h3 id="article-{i}"><span class="tag">{tag}</span> <a href="{article['link']}" style="color: #69BE82; text-decoration: none;">
            {truncate_title(article['title'])}
        </a>

        <p class="category" style="font-size: 0.9em; color: #555;">
            <em>📎 <a href="{article['link']}" style="color: #69BE82; text-decoration: none;">{source}</a> | 🗓️ {article['published'].strftime('%d %b %Y')}</em>
        </p>

        {format_bullet_summary(summary_text)}
        <p style="text-align: right; margin-top: 10px; font-size: 0.9em;">
            <a href="#top" style="text-decoration: none; color: #69BE82;">🔝 Bring me back up</a>
        </p>
        <hr>
        """

    html += """
    <p style="font-size: 0.9em; color: #777; text-align: center; margin-top: 40px;">
        The Daily Agri-News Digest historical 
        <a href="https://www.notion.so/2015e97ecd7d804a8cb9fc0c6e18e2f5?v=2015e97ecd7d802a86f7000c124695b5" target="_blank" style="color: #69BE82; text-decoration: none;">publications</a> and 
        <a href="https://www.notion.so/1f45e97ecd7d809cad9ff048ce70d972?v=1f45e97ecd7d8081b194000c523ce926" target="_blank" style="color: #69BE82; text-decoration: none;">articles</a> 
        are available on Notion 👀!
    </p>
</body>
</html>"""
    return html

# ──────────────────────────────────────────────────────────────────────────────
# Delivery helpers
# -----------------------------------------------------------------------------

def send_email(subject, html_body, recipients=None):
    """
    Send the newsletter through Gmail’s SMTP server as a multi-part message
    (plain-text fallback + HTML).

    Returns
    -------
    bool
        *True* on successful SMTP transaction, *False* otherwise.
    """
    if recipients is None:
        recipients = EMAIL_TO

    gmail_user = os.getenv("GMAIL_USERNAME")
    gmail_pass = os.getenv("GMAIL_PASSWORD")
    gmail_server = os.getenv("GMAIL_SMTP_SERVER")
    gmail_port = os.getenv("GMAIL_SMTP_PORT")

    if not all([gmail_user, gmail_pass, gmail_server, gmail_port]):
        logging.error("Informations SMTP Gmail manquantes dans le .env. Impossible d'envoyer l'email.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = f"Hyperplan Newsletter <{gmail_user}>"
        msg["Bcc"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(gmail_server, int(gmail_port)) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(msg["From"], recipients, msg.as_string())

        logging.info(f"Email envoyé avec succès à {len(recipients)} destinataires (en Bcc).")
        return True

    except Exception:
        logging.exception("Erreur lors de l'envoi de l'email")
        return False

def send_admin_report(articles_count, filtered_count, error_count_rss, error_count_mistral, email_success,
                      pub_total, pub_accepted, pub_rejected):
    """
    Email a one-page operational report to the first address in
    *EMAIL_RECIPIENTS* (or the dedicated admin address if defined).

    The report summarises:
    * How many RSS items were fetched / rejected.
    * How many official bulletins were accepted / rejected.
    * How many Mistral or HTTP errors occurred.
    * Whether the newsletter email itself went out successfully.
    """
    admin_email_default = EMAIL_TO[0] if EMAIL_TO else "admin@example.com"
    admin_emails = [os.getenv("ADMIN_EMAIL", admin_email_default)]
    report_html = f"""
    <h2>Newsletter System Report</h2>
    <p>Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    <h3>Official Publications</h3>
    <ul>
      <li>Total: {pub_total}</li>
      <li>Accepted: {pub_accepted}</li>
      <li>Rejected: {pub_rejected}</li>
    </ul>
    <p>Articles récupérés: {articles_count}</p>
    <p>Articles acceptés (Vegetal crops, Agri-tech ou Climate): {filtered_count}</p>
    <p>Articles rejetés: {articles_count - filtered_count}</p>
    <p>Erreurs de flux RSS: {error_count_rss}</p>
    <p>Erreurs de résumé Mistral AI: {error_count_mistral}</p>
    <p>Statut d'envoi de la newsletter: {'Succès' if email_success else 'Échec'}</p>
    """
    send_email(f"Newsletter System Report - {datetime.now().strftime('%d/%m/%Y')}", report_html, admin_emails)
    
# ──────────────────────────────────────────────────────────────────────────────
# Misc helpers
# -----------------------------------------------------------------------------

def format_bullet_summary(summary_text):
    """
    Convert a raw triple-newline text block (as returned by Mistral) into an
    HTML unordered list suitable for email rendering.
    """
    lines = summary_text.strip().split("\n")
    return "<ul>" + "".join(f"<li>{line.lstrip('- ').strip()}</li>" for line in lines if line.strip()) + "</ul>"

def is_accepted_category(category):
    """
    Quick helper that matches *category* case-insensitively against the
    whitelist :pydata:`ACCEPTED_CATEGORIES`.
    """
    # Normalisation de la catégorie pour la comparaison (minuscules)
    normalized_category = category.lower().strip()
    return any(accepted.lower() == normalized_category for accepted in ACCEPTED_CATEGORIES)

# ──────────────────────────────────────────────────────────────────────────────
# Notion helpers
# -----------------------------------------------------------------------------

notion = Client(auth=os.getenv("NOTION_TOKEN"))

def push_to_notion(article):
    """
    Archive an accepted news article into the “Articles” database inside
    the Hyperplan Notion workspace.
    """
    notion_token = os.getenv("NOTION_TOKEN")
    database_id = os.getenv("NOTION_DATABASE_ID")
    
    if not notion_token or not database_id:
        logging.error("NOTION_TOKEN ou NOTION_DATABASE_ID manquant dans le .env.")
        return

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    payload = {
    "parent": {"database_id": database_id},
    "properties": {
        "Title": {
            "title": [
                {
                    "text": {
                        "content": article["title"][:2000]
                    }
                }
            ]
        },
        "URL": {
            "url": article["link"]
        },
        "Published": {
            "date": {
                "start": article["published"].isoformat()
            }
        },
        "Source": {
            "rich_text": [
                {
                    "text": {
                        "content": article["source"]
                    }
                }
            ]
        },
        "Category": {
            "select": {
                "name": article.get("tag", "Divers")
            }
        }
    },
    "children": [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": article["summary_mistral"]["summary"] if isinstance(article["summary_mistral"], dict) else article["summary_mistral"]
                        }
                    }
                ]
            }
        }
    ]
}

    try:
        response = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
        if response.status_code == 200 or response.status_code == 201:
            logging.info(f"Article '{article['title']}' archivé dans Notion.")
        else:
            logging.error(f"Erreur lors de l'archivage Notion pour '{article['title']}': {response.status_code} {response.text}")
    except Exception:
        logging.exception(f"Erreur HTTP Notion pour '{article['title']}'")

def push_publication_to_notion(pub, zone):
    """
    Push a single official bulletin to the “Official publications” Notion
    database, tagging it with the geographical *zone* (country or region).
    """
    notion_token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_OFFICIAL_DATABASE_ID")

    if not notion_token or not db_id:
        logging.error("NOTION_TOKEN ou NOTION_OFFICIAL_DATABASE_ID manquant dans le .env.")
        return

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Title": {
                "title": [{
                    "text": {"content": pub["title"][:2000]}
                }]
            },
            "URL": {"url": pub["link"]},
            "Published": {"date": {"start": pub["date"]}},
            "Country": {
                "select": {
                    "name": ZONE_FLAGS.get(zone, "📍")
                }
            }
        }
    }

    try:
        response = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
        if response.status_code in (200, 201):
            logging.info(f"✅ Publication archivée : {pub['title']}")
        else:
            logging.error(f"❌ Erreur Notion pour '{pub['title']}': {response.status_code} {response.text}")
    except Exception:
        logging.exception(f"❌ Erreur HTTP Notion pour '{pub['title']}'")

# ──────────────────────────────────────────────────────────────────────────────
# Pipeline glue
# -----------------------------------------------------------------------------

def process_article_with_summary(article):
    """
    End-to-end pipeline for **one** RSS item:

    1. Check duplicate history.  
    2. Call :pyfunc:`summarize_and_categorize_mistral`.  
    3. Archive to Notion (if successful).  
    4. Return the enriched article dict, or ``None`` if the item should
       be dropped (duplicate, category not accepted, Mistral failed…).
    """
    article_hash = hash_article(article)
    if already_sent(article_hash):
        logging.info(f"Article déjà envoyé: {article['title']}")
        return None
    logging.info(f"Traitement de l'article: {article['title']}")
    lang = detect_language(article["summary_original"])
    summary_mistral = summarize_and_categorize_mistral(article["summary_original"], lang=lang)
    
    # Attribution du tag
    tag = summary_mistral.get("tag", "🤷 Misc")
    
    # On ajoute l'article à la liste des articles traités
    processed_article = {
        "title": article["title"],
        "link": article["link"],
        "published": article["published"],
        "source": article["source"],
        "summary_mistral": summary_mistral,
        "tag": tag,
        "hash": article_hash
    }
    
    # On vérifie si la catégorie est acceptée
    if isinstance(summary_mistral, dict) and is_accepted_category(summary_mistral.get("category", "")):
        logging.info(f"✅ Article accepté (catégorie: {summary_mistral.get('category')}): {article['title']}")
        
        # Archivage dans Notion pour les articles acceptés
        push_to_notion(processed_article)
        
        # Enregistrement de l'article comme traité
        log_article(article_hash)
        
        return processed_article
    else:
        category = summary_mistral.get("category", "Non catégorisé") if isinstance(summary_mistral, dict) else "Erreur"
        logging.info(f"❌ Article rejeté (catégorie: {category}): {article['title']}")
        return None

# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# -----------------------------------------------------------------------------

def main():
    """
    Orchestrate one complete newsletter run:

    * Fetch RSS articles.  
    * Fetch official publications.  
    * Summarise & filter.  
    * Build HTML.  
    * Send newsletter and admin report.  
    * Log every step.

    Any uncaught exception is logged with full traceback.
    """
    logging.info("Démarrage du processus de newsletter avec Mistral AI")
    if not MISTRAL_API_KEY:
        logging.error("Arrêt du script: MISTRAL_API_KEY n'est pas défini dans le fichier .env")
        return

    # AJOUT: Récupération des publications Agreste
    publications_by_zone, pub_total, pub_accepted, pub_rejected = fetch_official_publications()
    logging.info(f"Publications officielles récupérées: total={pub_total}, acceptées={pub_accepted}, rejetées={pub_rejected}")

    recent_articles, rss_error_count = get_recent_articles()
    processed_articles = []
    final_articles = []
    mistral_error_count = 0

    # AJOUT 2: Traitement séquentiel au lieu du parallélisme
    for i, article in enumerate(recent_articles):
        logging.info(f"Traitement de l'article {i+1}/{len(recent_articles)}: {article['title']}")
        
        try:
            result = process_article_with_summary(article)
            if result:
                processed_articles.append(result)
                if isinstance(result["summary_mistral"], dict) and "Error:" in result["summary_mistral"].get("summary", ""):
                    mistral_error_count += 1
                    logging.error(f"Erreur de résumé Mistral pour '{result['title']}': {result['summary_mistral']}")
                final_articles.append(result)
        except Exception as e:
            logging.exception(f"Erreur inattendue lors du traitement de l'article: {str(e)}")
        
        # Pause entre chaque article pour respecter la limite de 1 requête par seconde
        # Cette pause est en plus du rate limiter, pour garantir un espacement minimum
        time.sleep(0.5)  # 0.5 seconde de pause entre les articles

    # Tri par date décroissante
    final_articles.sort(key=lambda x: x["published"], reverse=True)

    email_sent_successfully = False
    html_newsletter = generate_newsletter_html(final_articles, publications_by_zone)
    with open("newsletter_preview_filtered.html", "w", encoding="utf-8") as f_html:
        f_html.write(html_newsletter)
        
    # Archivage des publications officielles dans Notion
    sent_publications = load_sent_publications()

    for zone, pubs in publications_by_zone.items():
        for pub in pubs:
            uid = f"{pub['title']}|{pub['date']}"
            if uid in sent_publications:
                logging.info(f"⏭️ Publication déjà archivée : {uid}")
                continue
            push_publication_to_notion(pub, zone)
            sent_publications.add(uid)

    save_sent_publications(sent_publications)

    email_sent_successfully = send_email(
        f"{NEWSLETTER_TITLE} - {datetime.now().strftime('%d/%m/%Y')}",
        html_newsletter
    )

    if final_articles:
        logging.info(f"{len(final_articles)} articles acceptés envoyés dans la newsletter")
    else:
        logging.info("Newsletter envoyée sans article, uniquement avec publications officielles")


    # Rapport administratif avec le nombre de publications et d'articles filtrés
    send_admin_report(
        len(recent_articles),
        len(final_articles),
        rss_error_count,
        mistral_error_count,
        email_sent_successfully,
        pub_total,
        pub_accepted,
        pub_rejected
    )

if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Erreur critique non interceptée dans main()")
        admin_email = os.getenv("ADMIN_EMAIL")
        if not admin_email:
            logging.error("ADMIN_EMAIL n'est pas défini dans le .env. Impossible d'envoyer le rapport admin.")
            admin_emails = []
        else:
            admin_emails = [admin_email]
        error_html = (
            "<h2>ERREUR CRITIQUE</h2><p>Le système de newsletter a rencontré une erreur non gérée.</p>"
        )
        # send_email(...) est volontairement commenté
        logging.error("Impossible d'envoyer l'email d'alerte critique si la configuration SMTP est en cause ou si l'erreur est liée à SMTP.")
