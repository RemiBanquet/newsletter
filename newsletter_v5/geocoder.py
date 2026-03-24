"""
Geocoding service using Nominatim (OpenStreetMap) with SQLite cache.
Resolves place names to lat/long coordinates.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import aiohttp

from models import Article, CompanySignal, GeoLocation, Publication, RunMetrics
from constants import NOMINATIM_URL, NOMINATIM_USER_AGENT, NOMINATIM_DELAY_SECONDS

logger = logging.getLogger(__name__)

# Default country centroids for publications and fallback
COUNTRY_CENTROIDS = {
    "AR": (-38.42, -63.62), "AT": (47.52, 14.55), "BE": (50.50, 4.47),
    "BR": (-14.24, -51.93), "BG": (42.73, 25.49), "CA": (56.13, -106.35),
    "HR": (45.10, 15.20), "CZ": (49.82, 15.47), "DK": (56.26, 9.50),
    "EG": (26.82, 30.80), "EE": (58.60, 25.01), "FI": (61.92, 25.75),
    "FR": (46.23, 2.21), "DE": (51.17, 10.45), "HU": (47.16, 19.50),
    "IN": (20.59, 78.96), "ID": (-0.79, 113.92), "IE": (53.14, -7.69),
    "IT": (41.87, 12.57), "LV": (56.88, 24.60), "LT": (55.17, 23.88),
    "MX": (23.63, -102.55), "MA": (31.79, -7.09), "NL": (52.13, 5.29),
    "NZ": (-40.90, 174.89), "PL": (51.92, 19.15), "PT": (39.40, -8.22),
    "RO": (45.94, 24.97), "SK": (48.67, 19.70), "ZA": (-30.56, 22.94),
    "ES": (40.46, -3.75), "SE": (60.13, 18.64), "TR": (38.96, 35.24),
    "GB": (55.38, -3.44), "UA": (48.38, 31.17), "US": (37.09, -95.71),
}


class GeoCache:
    """SQLite-backed cache for geocoding results."""

    def __init__(self, db_path: str = "geocache.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS geocache (
                    place_name TEXT PRIMARY KEY,
                    latitude REAL,
                    longitude REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def get(self, place_name: str) -> Optional[tuple[float, float]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT latitude, longitude FROM geocache WHERE place_name = ?",
                (place_name.lower().strip(),),
            ).fetchone()
            return (row[0], row[1]) if row else None

    def put(self, place_name: str, lat: float, lon: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO geocache (place_name, latitude, longitude) VALUES (?, ?, ?)",
                (place_name.lower().strip(), lat, lon),
            )
            conn.commit()


class Geocoder:
    """Resolves place names to coordinates using Nominatim + SQLite cache."""

    def __init__(self, metrics: RunMetrics, cache_path: str = "geocache.db"):
        self.cache = GeoCache(cache_path)
        self.metrics = metrics
        self._last_request_time = 0.0

    async def geocode(self, location: GeoLocation) -> GeoLocation:
        """Resolve a GeoLocation's place_name to lat/long."""
        if not location.place_name:
            # Fallback to country centroid if we have country_iso
            if location.country_iso and location.country_iso.upper() in COUNTRY_CENTROIDS:
                lat, lon = COUNTRY_CENTROIDS[location.country_iso.upper()]
                location.latitude = lat
                location.longitude = lon
            return location

        self.metrics.geocoding_attempted += 1

        # Check cache first
        cached = self.cache.get(location.place_name)
        if cached:
            location.latitude, location.longitude = cached
            self.metrics.geocoding_succeeded += 1
            self.metrics.geocoding_cached += 1
            return location

        # Rate-limit: respect Nominatim's 1 req/sec policy
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < NOMINATIM_DELAY_SECONDS:
            await asyncio.sleep(NOMINATIM_DELAY_SECONDS - elapsed)

        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "q": location.place_name,
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 0,
                }
                headers = {"User-Agent": NOMINATIM_USER_AGENT}

                async with session.get(NOMINATIM_URL, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    self._last_request_time = asyncio.get_event_loop().time()

                    if resp.status == 200:
                        results = await resp.json()
                        if results:
                            lat = float(results[0]["lat"])
                            lon = float(results[0]["lon"])
                            location.latitude = lat
                            location.longitude = lon
                            self.cache.put(location.place_name, lat, lon)
                            self.metrics.geocoding_succeeded += 1
                            return location

            # Fallback to country centroid
            if location.country_iso and location.country_iso.upper() in COUNTRY_CENTROIDS:
                lat, lon = COUNTRY_CENTROIDS[location.country_iso.upper()]
                location.latitude = lat
                location.longitude = lon
                self.cache.put(location.place_name, lat, lon)
                self.metrics.geocoding_succeeded += 1

        except Exception as e:
            logger.warning(f"Geocoding failed for '{location.place_name}': {e}")
            # Fallback to country centroid
            if location.country_iso and location.country_iso.upper() in COUNTRY_CENTROIDS:
                lat, lon = COUNTRY_CENTROIDS[location.country_iso.upper()]
                location.latitude = lat
                location.longitude = lon

        return location

    async def geocode_articles(self, articles: list[Article]) -> list[Article]:
        """Geocode a list of articles sequentially (Nominatim rate limit)."""
        for article in articles:
            article.location = await self.geocode(article.location)
        return articles

    async def geocode_publications(self, pubs: list[Publication]) -> list[Publication]:
        """Geocode publications — mostly country centroids."""
        for pub in pubs:
            if not pub.location.place_name and pub.country:
                # Publications default to country centroid
                pub.location.place_name = pub.country
            pub.location = await self.geocode(pub.location)
        return pubs

    async def geocode_signals(self, signals: list[CompanySignal]) -> list[CompanySignal]:
        """Geocode company signals."""
        for signal in signals:
            signal.location = await self.geocode(signal.location)
        return signals
