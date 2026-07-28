#!/usr/bin/env python3
"""
One-off diagnostic for feeds flagged DEAD by the health tracker.

For each URL it prints two attempts side by side:
  1. default TLS  — a plain browser-UA request
  2. Chrome-impersonated TLS — what the curl_cffi fallback in fetcher.py does

For each attempt it shows HTTP status, content-type, byte size, feedparser
entry count, and the first 300 bytes of the body.

Why both: the useful signal is the contrast. Run this locally AND from a
GitHub Actions step (a manual workflow_dispatch job is enough). If local says
200 + entries while the runner says 403, that's an IP/WAF block, not a dead
feed — keep the source, the curl_cffi fallback handles it. If both attempts
return 200 but 0 entries, the body isn't valid RSS (challenge page, redirect,
or a malformed feed) and the source needs a real fix or a swap.

    python diagnose_feed.py                      # checks the six failing feeds
    python diagnose_feed.py https://some/feed     # checks ad-hoc URL(s)
"""
import sys

import feedparser

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    sys.exit("curl_cffi not installed. Run: pip install curl_cffi")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# The six feeds that failed in the 2026-07-28 production run.
#
# The first four returned HTTP 200 with 0 parsed entries on BOTH the plain and
# the Chrome-impersonated path. Per the decision rule in the docstring above
# that means the body is not parseable RSS — so the useful output for these is
# the "first 300B" line, which distinguishes a WAF interstitial served with an
# RSS content-type from an undecoded compressed body.
#
# The last two are hard failures, kept for contrast: Grainews 403s on both
# paths, INSSE times out at 30s on both (the runner's address range looks
# blocked at the network level, which no client-side change will fix).
DEFAULT_URLS = [
    "https://api.io.canada.ca/io-server/gc/news/en/v2?dept=departmentofagricultureandagrifood&type=newsreleases&format=atom&atomtitle=AAFC",
    "https://statbel.fgov.be/en/rss.xml",
    "https://ukragroconsult.com/en/feed/",
    "https://www.ruralnewsgroup.co.nz/?format=feed&type=rss",
    "https://www.grainews.ca/feed/",
    "https://insse.ro/cms/files/rss_ins_en.xml",
]


def probe(url: str, impersonate: bool) -> None:
    label = "Chrome-impersonated TLS" if impersonate else "default TLS"
    print(f"\n  [{label}]")
    try:
        kwargs = {"headers": HEADERS, "timeout": 30}
        if impersonate:
            kwargs["impersonate"] = "chrome"
        r = curl_requests.get(url, **kwargs)
        body = r.content or b""
        feed = feedparser.parse(body)
        print(f"    status      : {r.status_code}")
        print(f"    content-type: {r.headers.get('content-type', '?')}")
        print(f"    bytes       : {len(body)}")
        print(f"    feed entries: {len(feed.entries)}")
        if feed.bozo:
            print(f"    parse warning: {feed.bozo_exception}")
        snippet = body[:300].decode("utf-8", "replace").replace("\n", " ").strip()
        print(f"    first 300B  : {snippet}")
    except Exception as e:
        print(f"    ERROR: {e}")


def main() -> None:
    urls = sys.argv[1:] or DEFAULT_URLS
    for url in urls:
        print("=" * 80)
        print(url)
        probe(url, impersonate=False)
        probe(url, impersonate=True)
    print("=" * 80)


if __name__ == "__main__":
    main()
