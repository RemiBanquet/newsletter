#!/usr/bin/env python3
import os
import json
import feedparser
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import html2text
import hashlib
import logging
import time
import random  # Ajouté pour le jitter dans le backoff exponentiel
from mistralai import Mistral
from concurrent.futures import ThreadPoolExecutor, as_completed
from langdetect import detect, LangDetectException
from notion_client import Client
import requests
import re
import threading  # Ajouté pour le rate limiter
from collections import deque  # Ajouté pour le rate limiter
# Imports pour le scraping Agreste
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dateutil import parser as date_parser
from deep_translator import GoogleTranslator
from bs4 import BeautifulSoup  # Pour parser la page HTML JRC
from typing import Optional

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("newsletter.log"),
        logging.StreamHandler()
    ]
)

load_dotenv()

ZONE_FLAGS = {
    "France": "🇫🇷",
    "Spain": "🇪🇸",
    "Hungary": "🇭🇺",
    "Germany": "🇩🇪",
    "Canada": "🇨🇦",
    "Romania": "🇷🇴",
    "Turkey": "🇹🇷",
    "UK": "🇬🇧",
    "Europe": "🇪🇺"
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
DAYS_TO_LOOK_BACK = 2
MAX_ARTICLES_PER_EMAIL = 500
HISTORY_FILE = "sent_articles.json"

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL_NAME = "mistral-large-latest"

# URL de la bannière
BANNER_URL = "https://raw.githubusercontent.com/RemiBanquet/newsletter-assets/main/LinkedIn_banner_green.jpg"

# Catégories acceptées pour le filtrage
ACCEPTED_CATEGORIES = ["Vegetal crops", "Agri-tech", "Climate"]

if not MISTRAL_API_KEY:
    logging.error("La clé API Mistral (MISTRAL_API_KEY) n'est pas configurée. Veuillez l'ajouter à votre fichier .env")
    # exit()

# Classe RateLimiter pour limiter les appels API à 1 requête par seconde
class RateLimiter:
    def __init__(self, requests_per_second=1):
        self.min_interval = 1.0 / requests_per_second  # Intervalle minimum entre les requêtes (en secondes)
        self.last_request_time = 0
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """Attend si nécessaire pour respecter la limite de requêtes"""
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time
            
            # Si le temps écoulé depuis la dernière requête est inférieur à l'intervalle minimum
            if elapsed < self.min_interval and self.last_request_time > 0:
                wait_time = self.min_interval - elapsed
                logging.info(f"Rate limiter: attente de {wait_time:.2f} secondes pour respecter la limite de 1 requête/seconde")
                time.sleep(wait_time)
            
            # Mettre à jour le timestamp de la dernière requête
            self.last_request_time = time.time()

# Initialisation du rate limiter global
mistral_rate_limiter = RateLimiter(requests_per_second=1)

def format_display_date(date_str):
    """
    Convertit une date chaîne (format YYYY-MM-DD ou DD/MM/YYYY) en format '28 May 2025'
    """
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return date_str  # fallback brut si parsing impossible

PUBLICATION_MEMORY_PATH = "sent_publications.json"

def detect_language(text: str):
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"

def load_sent_publications():
    if os.path.exists(PUBLICATION_MEMORY_PATH):
        with open(PUBLICATION_MEMORY_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_sent_publications(sent_ids):
    with open(PUBLICATION_MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(list(sent_ids), f, indent=2)


def should_keep_publication(pub):
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

# Fonction pour scraper les données Agreste
def scrape_agreste():
    """
    Scrape les publications récentes du site Agreste (48h max),
    avec parsing réel de la date 'Mis à jour le dd/mm/yyyy'
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
        "scrapers": []
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
    "Europe": {
        "rss": [
            "https://ec.europa.eu/eurostat/en/search?p_p_id=estatsearchportlet_WAR_estatsearchportlet&p_p_lifecycle=2&p_p_state=maximized&p_p_mode=view&p_p_resource_id=atom&_estatsearchportlet_WAR_estatsearchportlet_theme=PER_AGRFIS&_estatsearchportlet_WAR_estatsearchportlet_collection=CAT_EURNEW",
            "https://ec.europa.eu/eurostat/en/search?p_p_id=estatsearchportlet_WAR_estatsearchportlet&p_p_lifecycle=2&p_p_state=maximized&p_p_mode=view&p_p_resource_id=atom&_estatsearchportlet_WAR_estatsearchportlet_theme=PER_AGRFIS&_estatsearchportlet_WAR_estatsearchportlet_collection=dataset"
        ],
        "scrapers": [scrape_jrc]
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

def truncate_title(title, max_length=200):
    """
    Tronque un titre trop long pour l'affichage HTML de la newsletter.
    """
    return title if len(title) <= max_length else title[:max_length].rstrip() + "..."

def hash_article(article):
    return hashlib.md5(article["link"].encode()).hexdigest()

def already_sent(article_hash):
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

# AJOUT 3: Amélioration de la fonction avec backoff exponentiel plus long
def summarize_and_categorize_mistral(text_to_process, lang="unknown", max_retries=5):
    """
    Résume et catégorise un texte en utilisant l'API Mistral.
    
    Args:
        text_to_process (str): Le texte à résumer et catégoriser.
        lang (str, optional): La langue détectée du texte. Par défaut "unknown".
        max_retries (int, optional): Nombre maximum de tentatives en cas d'erreur. Par défaut 5.
        
    Returns:
        dict: Un dictionnaire contenant le résumé et la catégorie.
            {
                "summary": str,
                "category": str
            }
    """
    if not MISTRAL_API_KEY:
        logging.error("Clé API Mistral non disponible pour la traduction/résumé.")
        return {"summary": "Error: Mistral API key not configured.", "category": "Erreur"}

    client = Mistral(api_key=MISTRAL_API_KEY)
    
    # AJOUT 1: Utilisation du rate limiter global
    global mistral_rate_limiter

    prompt = f"""You are an expert in agricultural and agtech content analysis. Your role is to classify agricultural news articles for industry professionals who need quick and accurate summaries and labels.

⚠️ Return your response in the exact format below:
---
Summary:
- Bullet point 1
- Bullet point 2
- Bullet point 3

Category: <Vegetal crops / Agri-tech / Climate / Animals / Other>
Tag: <exactly one tag from the list below>
---

Then, follow these steps:

1. If the text is not in English, translate it into English first.

2. Summarize the article in exactly 3 concise and informative bullet points, focusing on the key facts and data.

3. Categorize the article with one of the following categories (choose the most precise one):
   - "Vegetal crops" → Only if the article specifically relates to one or more of the following cultivated plants: alfalfa, beans, beet, hemp, linen, maize, corn, cereals, peas, potato, soy, sorghum, barley, wheat, sunflower, triticale, canola. The topic can be about their acreage, land use, yields, price, seeds, crop protection, weather or market trends — but only if it is directly linked to one of these crops. If the article refers to other plants or general agriculture without naming one of these crops, do not assign "Vegetal crops".
   - "Agri-tech" → Innovations, companies or tools in agtech: sensors, drones, satellite monitoring, remote sensing, decision support systems, digital platforms, start-up funding, start-up partnerships, start-ups buyouts. ❌ Exclude anything related to heavy machinery, field robotics, genetic engineering, or plant breeding tools unless directly linked to the above crops.
   - "Climate" → weather conditions affecting vegetal crops, regenerative agriculture, regen ag practices and programs. ❌ Reject general weather or disasters (e.g. tornadoes, floods) unless crop impact is mentioned.
   - "Rejected" → Use this if the article doesn't match with any of the 3 previous categories or if the article fits any of the following:
     - The article is about livestock, breeding, feed, animal health, veterinary policy.
     - The article covers politics, infrastructure (e.g. pipelines, wind farms, solar), or general environment without direct impact on vegetal crops.
     - The article mentions biotech, genetics, or AI with no clear agricultural use case linked to vegetal crops.
     - The article is about protests, litigation, or public campaigns unrelated to crop production or policy.
     - The article is very vague or promotional without factual content.
     
4. Assign a semantic tag from the list below that best represents the main theme of the article:
   - "🚀 AgTech |" → sensors, remote sensing, satellite monitoring, decision support systems, digital farming, smart farming, digital platforms, start-up funding, start-up partnerships, start-ups buyouts, AI, generative AI
   - "🌍 Climate |" → weather, droughts, floods, climate impact, CO2, emissions, regenerative agriculture, agroecology, sustainable agriculture, carbon farming
   - "💧 Irrigation |" → irrigation, water use, water efficiency
   - "⚖️ Regulation |" → policies, subsidies, bans, laws, certifications, regulatory restrictions
   - "💸 Market |" → price trends, trade flows, supply/demand, market forecasts
   - "🌾 Crop land use |" → crop acreages, year-on-year crop acreage evolution
   - "📈 Yields |" → crop yields, year-on-year crop yields evolution, weather impact on yields
   - "🌱 Seeds |" → seed genetics, seed varieties, seed production, seed certification, seed multiplication, seed commercialization
   - "🛡️ Crop Protection |" → pesticides, biocontrol, insecticides, fungicides, herbicides, nematicides, resistance management
   - "🧪 Crop Nutrition |" → fertilizers, biostimulants, foliar nutrition, nutrient management, fertilization tools
   - "🤷 Misc |" → only if no tag above fits precisely

If multiple tags could apply, choose the most dominant theme.
Do NOT output multiple tags.

Text to process: {text_to_process}
"""

    messages_payload = [{
        "role": "user",
        "content": prompt
    }]

    # AJOUT 3: Backoff exponentiel avec jitter pour éviter les effets de synchronisation
    base_delay = 2  # délai de base en secondes (augmenté à 2 secondes)
    max_delay = 120  # délai maximum en secondes (augmenté à 2 minutes)

    for attempt in range(max_retries):
        try:
            # AJOUT 1: Attendre si nécessaire pour respecter le rate limit
            mistral_rate_limiter.wait_if_needed()
            
            response = client.chat.complete(
                model=MISTRAL_MODEL_NAME,
                messages=messages_payload,
                temperature=0.3
            )
            content = response.choices[0].message.content.strip()

            # Extraction : summary, category, tag
            summary_match = re.search(r"Summary:\s*(.*?)\n+Category:", content, re.DOTALL)
            category_match = re.search(r"Category:\s*(.+)", content)
            tag_match = re.search(r"Tag:\s*(.+)", content)

            summary = summary_match.group(1).strip() if summary_match else "Error: summary not found"
            category = category_match.group(1).strip() if category_match else "Erreur"
            tag = tag_match.group(1).strip() if tag_match else "🤷 Misc"

            return {"summary": summary, "category": category, "tag": tag}

        except Exception as e:
            # AJOUT 3: Vérifier si c'est une erreur 429
            is_rate_limit_error = "429" in str(e) or "rate limit" in str(e).lower()
            
            if attempt < max_retries - 1:
                # AJOUT 3: Calculer le délai avec backoff exponentiel et jitter
                delay = min(max_delay, base_delay * (2 ** attempt))
                # Ajouter un jitter aléatoire pour éviter les effets de synchronisation
                jitter = delay * 0.2 * (random.random() * 2 - 1)  # ±20% de jitter
                delay = max(1.0, delay + jitter)  # Assurer un délai minimum d'une seconde
                
                log_level = logging.WARNING if is_rate_limit_error else logging.ERROR
                logging.log(log_level, f"Erreur Mistral (tentative {attempt+1}/{max_retries}): {str(e)}")
                logging.info(f"Nouvelle tentative dans {delay:.2f} secondes...")
                
                time.sleep(delay)
            else:
                logging.error(f"Échec après {max_retries} tentatives: {str(e)}")
                return {"summary": "Error: Mistral API failed after multiple retries.", "category": "Erreur"}


def generate_official_publications_html(publications_by_zone):
    """
    Génère la section HTML pour les publications officielles récentes par zone géographique.
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

def send_email(subject, html_body, recipients=None):
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
    
notion = Client(auth=os.getenv("NOTION_TOKEN"))

def push_to_notion(article):
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

def format_bullet_summary(summary_text):
    lines = summary_text.strip().split("\n")
    return "<ul>" + "".join(f"<li>{line.lstrip('- ').strip()}</li>" for line in lines if line.strip()) + "</ul>"

def is_accepted_category(category):
    """
    Vérifie si la catégorie est dans la liste des catégories acceptées.
    
    Args:
        category (str): La catégorie à vérifier.
        
    Returns:
        bool: True si la catégorie est acceptée, False sinon.
    """
    # Normalisation de la catégorie pour la comparaison (minuscules)
    normalized_category = category.lower().strip()
    return any(accepted.lower() == normalized_category for accepted in ACCEPTED_CATEGORIES)

def push_publication_to_notion(pub, zone):
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

def process_article_with_summary(article):
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

def main():
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
