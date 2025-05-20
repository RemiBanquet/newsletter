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
MAX_ARTICLES_PER_EMAIL = 300
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

# AJOUT 1: Classe RateLimiter pour limiter les appels API à 1 requête par seconde
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

# AJOUT: Fonction pour scraper les données Agreste
def scrape_agreste():
    """
    Scrape les publications récentes du site Agreste.
    
    Returns:
        list: Liste des articles des 2 derniers jours avec titre, date et lien
    """
    logging.info("Début du scraping des publications Agreste")
    
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless')  # Pour exécuter sans fenêtre
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')

    # Utilisation de Service pour spécifier le chemin du chromedriver
    service = Service(ChromeDriverManager().install())
    
    # Initialisation du driver
    driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.get('https://agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/4545f1a9-afe6-4c86-a141-693f2c72d550!1b69a349-ca8f-4353-82bb-4c00c502412c!729f399f-53c3-4952-9971-4753794a7c1b!c6be0c43-70a0-4666-853f-80de38a08ec7!0c593aed-b1d0-476e-9359-12d6347d8243!b125c6dc-13b7-4260-9abd-6e9321b2b963!fec0e278-6655-4c48-ac47-aab6d8847e15/search/')

    # Ajouter une pause pour s'assurer que la page se charge
    time.sleep(5)  # Attendre 5 secondes pour s'assurer que la page a bien fini de se charger

    # Liste des articles
    articles = []

    try:
        rows = driver.find_elements(By.CSS_SELECTOR, '.ptfixed')  # Ajuste le sélecteur si nécessaire

        if not rows:
            logging.warning("Aucune publication Agreste trouvée. Vérifiez le sélecteur CSS.")
        
        today = datetime.today()

        for row in rows:
            try:
                title_tag = row.find_element(By.CSS_SELECTOR, '.titreSearch a')
                title = title_tag.text.strip()
                date_raw = row.find_element(By.CSS_SELECTOR, '.disar-split-panel-right-table-cell-content-info').text.strip().split(" | ")[-1]
                link = title_tag.get_attribute('href')

                # Nettoyer la date brute et convertir en format datetime
                if "Mis à jour le " in date_raw:
                    date_str = date_raw.replace("Mis à jour le ", "")
                    date_article = datetime.strptime(date_str, "%d/%m/%Y")

                    # Filtrer les articles des 2 derniers jours
                    if date_article > today - timedelta(days=2):
                        article = {
                            'title': title,
                            'date': date_str,
                            'link': link
                        }
                        articles.append(article)
                        logging.info(f"Publication Agreste trouvée: {article['title']} ({article['date']})")

            except Exception as e:
                logging.warning(f"Erreur lors de l'extraction des données d'une publication Agreste: {e}")

    except Exception as e:
        logging.error(f"Erreur lors du scraping des publications Agreste: {e}")
    finally:
        driver.quit()

    logging.info(f"Scraping Agreste terminé: {len(articles)} publications trouvées")
    return articles

def detect_language(text):
    try:
        lang = detect(text)
        return lang
    except LangDetectException:
        return "unknown"

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
   - "Vegetal crops" → Only if the article specifically relates to one or more of the following cultivated plants: alfalfa, beans, beet, hemp, linen, maize, corn, cereals, peas, potato, soy, sorghum, barley, wheat, sunflower, triticale, canola. The topic can be about their area, yield, price, seeds, crop protection, weather or market trends — but only if it is directly linked to one of these crops. If the article refers to other plants or general agriculture without naming one of these crops, do not assign "Vegetal crops".
   - "Agri-tech" → Innovations, companies or tools in agtech: sensors, drones, satellite monitoring, remote sensing, decision support systems, digital platforms, start-up funding, start-up partnerships, start-ups buyouts. ❌ Exclude anything related to heavy machinery, field robotics, genetic engineering, or plant breeding tools.
   - "Climate" → weather conditions affecting vegetal crops, regenerative agriculture, regen ag practices and programs
   - "Animals" → All content related to livestock, breeding, feed, animal health, veterinary policy.
   - "Other" → Only if the article clearly does not belong to any of the above categories (e.g. general environment, climate change, politics unrelated to agriculture).
4. Assign a semantic tag from the list below that best represents the main theme of the article:

   - "🚀 AgTech |" → new tools, startups, technology, platforms, sensors, AI, robotics, data
   - "🌍 Climate |" → weather, droughts, floods, climate impact, CO2, emissions
   - "💧 Irrigation |" → irrigation, water use, water efficiency
   - "⚖️ Regulation |" → policies, subsidies, bans, laws, certifications
   - "💸 Market |" → price trends, trade flows, supply/demand, market forecasts
   - "🌱 Seeds |" → genetics, varieties, seed production, certification, commercialization
   - "🛡️ Crop Protection |" → pesticides, biocontrol, fungicides, herbicides, resistance management, approvals
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

# AJOUT: Fonction pour générer le HTML de la section Flash Agreste
def generate_agreste_html(agreste_articles):
    """
    Génère le HTML pour la section Flash Agreste.
    
    Args:
        agreste_articles (list): Liste des publications Agreste récentes
        
    Returns:
        str: HTML formaté pour la section Flash Agreste
    """
    # Style pour encadrer toute la section comme une bannière secondaire
    html = """
    <div style="margin: 30px 0; padding: 15px; background-color: #F0F4FA;">
    <h2 style="margin-top: 0; color: #14213D;">🇫🇷 Flash Agreste 🇫🇷</h2>
    """
    
    if not agreste_articles:
        html += """
        <p style="padding: 10px; color: #14213D; border-left: 3px solid #69BE82;">
            No publications over the last 2 days from the French Ministry of Agriculture.
        </p>
        """
    else:
        html += """
        <p style="color: #14213D;">Latest publications from the French Ministry of Agriculture:</p>
        <ul style="list-style-type: none; padding-left: 0; margin-bottom: 0;">
        """
        
        for article in agreste_articles:
            html += f"""
            <li style="margin-bottom: 10px; padding: 10px; border-left: 3px solid #69BE82;">
                <a href="{article['link']}" style="font-weight: bold; color: #69BE82; text-decoration: none;">
                    {article['title']}
                </a>
                <div style="font-size: 0.9em; color: #14213D; margin-top: 3px;">
                    Published on {article['date']}
                </div>
            </li>
            """
        
        html += """
        </ul>
        """
    
    # Fermeture du div qui encadre toute la section
    html += """
    </div>
    """
    
    return html

def generate_newsletter_html(articles, agreste_articles=None):
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{NEWSLETTER_TITLE}</title>
    <style>
        body {{ font-family: Aptos, 'Segoe UI', Tahoma, sans-serif; line-height: 1.6; max-width: 700px; margin: 0 auto; padding: 20px; }}
        h1 {{ text-align: center; margin-top: 20px; }}
        h2 {{ margin-top: 40px; }}
        h3 {{ margin-bottom: 5px; }}
        ul {{ margin-top: 5px; }}
        .category {{ font-size: 0.9em; color: #555; font-style: italic; }}
        .tag {{ font-weight: bold; color: #333; }}
        .banner {{ width: 100%; max-width: 700px; display: block; margin: 0 auto; }}
    </style>
</head>
<body>
    <img src="{BANNER_URL}" alt="Hyperplan" class="banner">
    <h1>{NEWSLETTER_TITLE}</h1>
"""

    # AJOUT: Intégration de la section Flash Agreste au-dessus du sommaire, même si vide
    html += generate_agreste_html(agreste_articles)

    # 👉 Étape 1 : regrouper les articles par tag
    articles_by_tag = {}
    for i, article in enumerate(articles):
        tag = article.get("tag", "🤷 Misc |")
        articles_by_tag.setdefault(tag, []).append((i, article))

    # 👉 Étape 2 : générer le sommaire
    # Encadrement de la section Today's news avec fond vert clair
    html += """
    <div style="margin: 30px 0; padding: 15px; background-color: #F0F9F3;">
    <h2 style="margin-top: 0; color: #69BE82;">🌍 Today's news 🌍</h2>
    <ul>
    """
    for tag, items in articles_by_tag.items():
        html += f"<li><strong>{tag}</strong>\n<ul>\n"
        for i, article in items:
            html += f'<li><a href="#article-{i}" style="color: #69BE82; text-decoration: none;">{article["title"]}</a></li>\n'
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
        <h3 id="article-{i}"><span class="tag">{tag}</span> <a href="{article['link']}" style="color: #69BE82; text-decoration: none;">{article['title']}</a></h3>
        <p class="category">
            <em>📎 <a href="{article['link']}" style="color: #69BE82; text-decoration: none;">{source}</a> | {article['published'].strftime('%d %b %Y')}</em>
        </p>
        {format_bullet_summary(summary_text)}
        <p style="text-align: right; margin-top: 10px; font-size: 0.9em;">
            <a href="#top" style="text-decoration: none; color: #69BE82;">🔝 Bring me back up</a>
        </p>
        <hr>
        """

    html += """
    <p style="font-size: 0.9em; color: #777; text-align: center; margin-top: 40px;">
        The Daily Agri-News Digest historical articles are available on 
        <a href="https://www.notion.so/1f45e97ecd7d809cad9ff048ce70d972?v=1f45e97ecd7d8081b194000c523ce926" target="_blank" style="color: #69BE82; text-decoration: none;">
            Notion, enjoy !
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
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(gmail_server, int(gmail_port)) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(msg["From"], recipients, msg.as_string())
        logging.info(f"Email envoyé avec succès à {len(recipients)} destinataires: {', '.join(recipients)}")
        return True
    except Exception:
        logging.exception("Erreur lors de l'envoi de l'email")

        return False

def send_admin_report(articles_count, filtered_count, error_count_rss, error_count_mistral, email_success):
    admin_email_default = EMAIL_TO[0] if EMAIL_TO else "admin@example.com"
    admin_emails = [os.getenv("ADMIN_EMAIL", admin_email_default)]
    report_html = f"""
    <h2>Newsletter System Report</h2>
    <p>Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    <p>Articles récupérés: {articles_count}</p>
    <p>Articles filtrés (Vegetal crops, Agri-tech ou Climate): {filtered_count}</p>
    <p>Articles exclus: {articles_count - filtered_count}</p>
    <p>Erreurs de flux RSS: {error_count_rss}</p>
    <p>Erreurs de résumé Mistral AI: {error_count_mistral}</p>
    <p>Statut d'envoi de la newsletter: {'Succès' if email_success else 'Échec'}</p>
    """
    send_email(f"Newsletter System Report - {datetime.now().strftime('%d/%m/%Y')}", report_html, admin_emails)
    
notion = Client(auth=os.getenv("NOTION_TOKEN"))
database_id = os.getenv("NOTION_DATABASE_ID")

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
                        "content": article["title"]
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
        logging.info(f"Article accepté (catégorie: {summary_mistral.get('category')}): {article['title']}")
        
        # Archivage dans Notion pour les articles acceptés
        push_to_notion(processed_article)
        
        # Enregistrement de l'article comme traité
        log_article(article_hash)
        
        return processed_article
    else:
        category = summary_mistral.get("category", "Non catégorisé") if isinstance(summary_mistral, dict) else "Erreur"
        logging.info(f"Article filtré (catégorie: {category}): {article['title']}")
        return None

def main():
    logging.info("Démarrage du processus de newsletter avec Mistral AI")
    if not MISTRAL_API_KEY:
        logging.error("Arrêt du script: MISTRAL_API_KEY n'est pas défini dans le fichier .env")
        return

    # AJOUT: Récupération des publications Agreste
    agreste_articles = scrape_agreste()
    logging.info(f"Publications Agreste récupérées: {len(agreste_articles)}")

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
    if final_articles:
        # AJOUT: Intégration des publications Agreste dans la génération de la newsletter
        html_newsletter = generate_newsletter_html(final_articles, agreste_articles)
        with open("newsletter_preview_filtered.html", "w", encoding="utf-8") as f_html:
            f_html.write(html_newsletter)
        logging.info(f"Aperçu de la newsletter sauvegardé dans newsletter_preview_filtered.html")
        email_sent_successfully = send_email(f"{NEWSLETTER_TITLE} - {datetime.now().strftime('%d/%m/%Y')}", html_newsletter)
        if email_sent_successfully:
            logging.info(f"{len(final_articles)} articles acceptés envoyés dans la newsletter")
        else:
            logging.error("Échec de l'envoi de la newsletter")
    else:
        logging.info("Aucun article accepté à envoyer aujourd'hui")
        email_sent_successfully = True

    # Rapport administratif avec le nombre d'articles filtrés
    send_admin_report(
        len(recent_articles),
        len(final_articles),
        rss_error_count,
        mistral_error_count,
        email_sent_successfully
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
