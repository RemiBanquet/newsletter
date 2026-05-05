"""
Shared Selenium helper for scrapers.

Centralises Chrome driver creation so all selenium-scraper sources
benefit from the same setup logic. In GitHub Actions we use the Chrome
+ ChromeDriver pre-installed by `browser-actions/setup-chrome@v1`
(exposed via CHROME_PATH / CHROMEDRIVER_PATH env vars). Locally we
fall back to webdriver-manager.

Usage:
    from sources._selenium_helper import build_chrome_driver
    driver = build_chrome_driver()  # raises ImportError if selenium missing
    try:
        driver.get(url)
        ...
    finally:
        driver.quit()
"""

import logging
import os

logger = logging.getLogger(__name__)


def build_chrome_driver():
    """
    Return a headless Chrome WebDriver, or raise ImportError if Selenium
    isn't installed. Uses CHROME_PATH / CHROMEDRIVER_PATH env vars when set
    (CI), otherwise tries webdriver-manager (local dev).
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    # Reasonable UA so government CDN/WAFs don't block us
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    chrome_path = os.environ.get("CHROME_PATH")
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

    if chrome_path:
        options.binary_location = chrome_path

    if chromedriver_path:
        service = Service(executable_path=chromedriver_path)
        logger.debug(f"Selenium: using CI chromedriver at {chromedriver_path}")
    else:
        # Local dev fallback
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            logger.debug("Selenium: using webdriver-manager (local dev)")
        except ImportError:
            # Last resort: assume chromedriver is on PATH
            service = Service()
            logger.debug("Selenium: using chromedriver from PATH")

    return webdriver.Chrome(service=service, options=options)
