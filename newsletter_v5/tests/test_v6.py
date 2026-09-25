"""Offline tests for the v6 selection layer, filters and renderer.

Run from newsletter_v5/:  python -m pytest -q tests
No network, no API keys.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (  # noqa: E402
    Article, ArticleCategory, CompanySignal, CompanyType, GeoLocation,
    MarketBrief, BriefSection, Publication, SignalType,
)
from ranking import flag_emoji, select_psd_movers, select_radar, select_top_stories  # noqa: E402
from fetcher import is_signal_noise  # noqa: E402
from renderer import render_newsletter_v6  # noqa: E402

NOW = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)


def art(i, source="Src A", importance=3, key="", title=None, iso="US", hours=0):
    return Article(
        id=f"a{i}", title=title or f"Story {i}", url=f"https://example.com/{i}",
        source_name=source, published_at=NOW - timedelta(hours=hours),
        summary=f"Summary {i}", category=ArticleCategory.MARKETS,
        location=GeoLocation(country_iso=iso), relevant=True,
        importance=importance, story_key=key,
    )


def sig(i, company="Syngenta", score=4, key="", actor=True, ctype=CompanyType.CLIENT, hours=0):
    return CompanySignal(
        id=f"s{i}", title=f"Signal {i}", url=f"https://example.com/s{i}",
        company_name=company, company_type=ctype, signal_type=SignalType.PARTNERSHIP,
        summary=f"Signal summary {i}", published_at=NOW - timedelta(hours=hours),
        score=score, story_key=key, company_is_actor=actor,
    )


# ── Top stories ──

def test_clusters_collapse_to_one_story_with_count():
    items = [art(i, source=f"S{i}", key="corn-belt-harvest-rain") for i in range(11)]
    items.append(art(99, source="Other", importance=4))
    out = select_top_stories(items)
    assert len(out) == 2
    assert out[0].importance == 4                      # higher importance first
    cluster = [s for s in out if s.cluster_size == 11]
    assert len(cluster) == 1


def test_fallback_key_is_normalised_title():
    items = [art(1, title="Wheat up 2%!"), art(2, source="Src B", title="wheat UP 2%")]
    out = select_top_stories(items)
    assert len(out) == 1 and out[0].cluster_size == 2


def test_per_source_cap_and_max():
    items = [art(i, source="Brownfield") for i in range(5)] + \
            [art(10 + i, source=f"S{i}") for i in range(20)]
    out = select_top_stories(items, max_n=12, per_source=2)
    assert len(out) == 12
    assert sum(1 for s in out if s.source_name == "Brownfield") <= 2


def test_flag_emoji():
    assert flag_emoji("fr") == "🇫🇷"
    assert flag_emoji("EU") == "🇪🇺"
    assert flag_emoji("") == "" and flag_emoji("XYZ") == ""


# ── Radar ──

def test_radar_filters_score_and_actor_and_dedups():
    signals = [sig(i, company="OCP Nutricrops", key="ocp-brazil-mou", ctype=CompanyType.PROSPECT)
               for i in range(7)]
    signals += [sig(20, score=3), sig(21, score=5, actor=False), sig(22, score=5, company="Corteva")]
    shown, more = select_radar(signals, {"Corteva": "high"})
    names = [r.company_name for r in shown]
    assert names == ["Corteva", "OCP Nutricrops"]      # score 5 first
    assert shown[1].sources == 7
    assert shown[1].company_type == "prospect"
    assert more == 2                                    # the score-3 and non-actor groups


def test_radar_max_and_priority_tiebreak():
    signals = [sig(i, company=f"Co{i}") for i in range(8)]
    shown, more = select_radar(signals, {"Co7": "high"}, max_n=5)
    assert len(shown) == 5 and more == 3
    assert shown[0].company_name == "Co7"


# ── PSD movers ──

def _psd(new=True):
    return {
        "available": True, "is_new_update": new, "release_label": "Sep 2026",
        "production_all": {"crops": ["Wheat", "Corn"], "rows": [
            {"name": "EU Total", "flag": "🇪🇺", "values": {
                "Wheat": {"value": 144000.0, "yoy_pct": 17.9},
                "Corn": {"value": 56950.0, "yoy_pct": -3.5}}},
            {"name": "Estonia", "flag": "🇪🇪", "values": {
                "Wheat": {"value": 900.0, "yoy_pct": 60.0},       # below 1 Mt: ignored
                "Corn": {"value": None, "yoy_pct": None}}},
        ]},
    }


def test_psd_movers_only_on_release_day():
    assert select_psd_movers(_psd(new=False)) == []
    assert select_psd_movers({}) == []
    movers = select_psd_movers(_psd())
    assert [m.country for m in movers] == ["EU Total", "EU Total"]
    assert movers[0].yoy_pct == 17.9


# ── Rule filter on signals ──

def test_signal_noise_filter():
    noise = [
        "CHS is hiring summer interns 2027",
        "#HIRING Territory Manager Syngenta",
        "Crop Protection Market Size, Share & Forecast 2026-2033",
        "Biostimulants market to reach $6bn, CAGR 11%",
    ]
    keep = [
        "Syngenta signs exclusive EU distribution deal with Amoéba",
        "Corteva board approves separation creating Vylor",
        "OCP signs MoU with Brazil for 3.8 Mt of fertilizer",
        "UPL is looking for a State Marketing Head - We are Hiring",
        "Nutrien hiring Senior Director, NA Agronomy in Deerfield, IL",
        "Syngenta Bangladesh signs MoU with CAB International",
    ]
    assert all(is_signal_noise(t) for t in noise)
    assert not any(is_signal_noise(t) for t in keep)


# ── Renderer ──

def _render(**over):
    brief = MarketBrief(takeaway="Grains rallied. Rain slows harvest.",
                        sections=[BriefSection(label="Markets", text="Corn up 15¢.")])
    pubs = [Publication(id="p1", title="Crop Progress", url="https://nass.usda.gov/x",
                        source_name="USDA NASS", flag_emoji="🇺🇸", published_at=NOW)]
    stories = select_top_stories([art(1, title="<script>alert(1)</script> & Co"), art(2, source="B")])
    radar, more = select_radar([sig(1, score=5), sig(2, company="BASF", score=3)], {})
    kw = dict(
        date="Wed 23 Sep 2026", preheader="Story 2", market_brief=brief, publications=pubs,
        top_stories=stories, total_articles=40, radar=radar, radar_more=more,
        psd_movers=select_psd_movers(_psd()), psd_release_label="Sep 2026",
        archive_url="https://notion.so/a", signals_url="https://notion.so/s",
        feedback_url="mailto:x@y.z",
    )
    kw.update(over)
    return render_newsletter_v6(**kw)


def test_render_contains_sections_in_order_and_escapes():
    html = _render()
    order = [html.index(x) for x in
             ("The day in 90 seconds", "Official publications · ", "Top stories · ", "Account radar · ", "USDA PSD · ")]
    assert order == sorted(order)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "1 more signals in the Signal board" in html
    assert len(html.encode()) < 102 * 1024           # under the Gmail clip limit


def test_render_hides_empty_blocks():
    html = _render(market_brief=None, radar=[], radar_more=0, psd_movers=[])
    assert "The day in 90 seconds" not in html
    assert "Account radar · " not in html
    assert "USDA PSD · " not in html
    assert "more signals in the Signal board" not in html


def test_country_flag_for_rss_publications():
    from constants import country_flag
    assert country_flag("United Kingdom") == "🇬🇧"
    assert country_flag("Finland") == "🇫🇮"
    assert country_flag("Global") == "🌍"
    assert country_flag("Atlantis") == ""
