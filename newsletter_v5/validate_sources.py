"""
Standalone source smoke test for the Daily Agri-News Digest.

Fetches every enabled RSS/Atom source from config_cache.yaml and reports:
entry count, newest item date, and a PASS / EMPTY / FAIL verdict. No LLM
calls, no Notion writes, no email. Safe to run anytime.

Usage:
    cd newsletter_v5 && python validate_sources.py            # all RSS sources
    python validate_sources.py --only "farmer"                # name filter
    python validate_sources.py --category official_publication

Tip: run `python newsletter_v5.py --dry-run` first if you changed sources in
Notion — that refreshes config_cache.yaml.

Exit code: 0 if every source PASSes, 1 otherwise (CI-friendly; add a weekly
GitHub Actions job that runs this and fails loudly).
"""

import argparse
import sys
from datetime import datetime, timezone

import feedparser
import requests
import yaml

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def newest_entry_date(feed) -> str:
    dates = []
    for e in feed.entries:
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        if parsed:
            try:
                dates.append(datetime(*parsed[:6], tzinfo=timezone.utc))
            except Exception:
                pass
    if not dates:
        return "no dates"
    newest = max(dates)
    age_days = (datetime.now(timezone.utc) - newest).days
    return f"{newest:%Y-%m-%d} ({age_days}d old)"


def check_source(name: str, url: str) -> tuple[str, str]:
    """Returns (verdict, detail). Verdicts: PASS / EMPTY / FAIL."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        return "FAIL", f"request error: {type(e).__name__}: {e}"
    if resp.status_code != 200:
        return "FAIL", f"HTTP {resp.status_code}"
    if not resp.content:
        return "FAIL", "empty body (bot block?)"
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        return "EMPTY", f"parsed but 0 entries ({len(resp.content)} bytes)"
    return "PASS", f"{len(feed.entries)} entries, newest: {newest_entry_date(feed)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config_cache.yaml")
    ap.add_argument("--only", default="", help="substring filter on source name")
    ap.add_argument("--category", default="", help="media / official_publication")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    sources = [
        s for s in cfg.get("sources", [])
        if s.get("enabled")
        and s.get("source_type") == "rss"
        and (not args.only or args.only.lower() in s.get("name", "").lower())
        and (not args.category or s.get("category") == args.category)
    ]

    if not sources:
        print("No matching enabled RSS sources in config. "
              "Run `python newsletter_v5.py --dry-run` to refresh the cache from Notion.")
        sys.exit(1)

    print(f"Checking {len(sources)} sources...\n")
    failures = 0
    for s in sources:
        verdict, detail = check_source(s["name"], s["url"])
        mark = {"PASS": "✅", "EMPTY": "⚠️ ", "FAIL": "❌"}[verdict]
        print(f"{mark} {verdict:5} {s['name']:<45} {detail}")
        if verdict != "PASS":
            failures += 1

    print(f"\n{len(sources) - failures}/{len(sources)} passing")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
