"""
Newsletter HTML renderer using Jinja2.
Builds the email body from classified articles, publications, and signals.
"""

import logging
from collections import OrderedDict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from models import Article, CompanySignal, CompanyType, Publication, MarketBrief
from constants import CATEGORY_EMOJI, SIGNAL_TYPE_EMOJI, SENDER_EMAIL

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class _Namespace:
    """Simple namespace for Jinja2 dot-access on dicts."""
    def __init__(self, d: dict):
        for k, v in d.items():
            if isinstance(v, dict):
                setattr(self, k, _Namespace(v))
            elif isinstance(v, list):
                setattr(self, k, [_Namespace(i) if isinstance(i, dict) else i for i in v])
            else:
                setattr(self, k, v)

    def __bool__(self):
        return True

    def items(self):
        """Allow Jinja2 {% for k, v in obj.items() %} on namespace objects."""
        return {k: v for k, v in self.__dict__.items()}.items()


def _dict_to_namespace(d):
    """Convert nested dict to namespace for Jinja dot access."""
    if d is None:
        return None
    if isinstance(d, dict):
        return _Namespace(d)
    return d


def _group_by_category(articles: list[Article]) -> OrderedDict[str, list[Article]]:
    """Group articles by category, ordered by count descending."""
    groups: dict[str, list[Article]] = {}
    for article in articles:
        cat = article.category.value
        groups.setdefault(cat, []).append(article)
    # Sort by count descending
    return OrderedDict(sorted(groups.items(), key=lambda x: -len(x[1])))


def _group_signals_by_company(signals: list[CompanySignal]) -> OrderedDict[str, list[CompanySignal]]:
    """Group signals by company name, ordered alphabetically."""
    groups: dict[str, list[CompanySignal]] = {}
    for signal in signals:
        groups.setdefault(signal.company_name, []).append(signal)
    return OrderedDict(sorted(groups.items()))


def _tag_counts(articles: list[Article]) -> OrderedDict[str, int]:
    """Count articles per category for the navigation."""
    counts: dict[str, int] = {}
    for article in articles:
        cat = article.category.value
        counts[cat] = counts.get(cat, 0) + 1
    return OrderedDict(sorted(counts.items(), key=lambda x: -x[1]))


def render_newsletter(
    date: str,
    articles: list[Article],
    publications: list[Publication],
    client_signals: list[CompanySignal],
    prospect_signals: list[CompanySignal],
    psd_data: dict = None,
    market_brief: MarketBrief = None,
) -> str:
    """Render the full newsletter HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,  # HTML template, no auto-escaping needed
    )
    template = env.get_template("newsletter.html")

    articles_by_category = _group_by_category(articles)
    client_groups = _group_signals_by_company(client_signals)
    prospect_groups = _group_signals_by_company(prospect_signals)
    tag_counts = _tag_counts(articles)

    # Convert psd_data dict to a namespace-like object for Jinja dot access
    psd_ns = _dict_to_namespace(psd_data) if psd_data else None

    html = template.render(
        date=date,
        articles=articles,
        publications=publications,
        client_signals=client_signals,
        prospect_signals=prospect_signals,
        articles_by_category=articles_by_category,
        client_groups=client_groups,
        prospect_groups=prospect_groups,
        tag_counts=tag_counts,
        category_emoji=CATEGORY_EMOJI,
        signal_type_emoji=SIGNAL_TYPE_EMOJI,
        sender_email=SENDER_EMAIL,
        psd_data=psd_ns,
        market_brief=market_brief,
    )

    logger.info(
        f"Rendered newsletter: {len(publications)} pubs, {len(articles)} articles, "
        f"{len(client_signals)} client signals, {len(prospect_signals)} prospect signals"
    )
    return html


# ── v6 renderer (Hyperplan brand, selected content) ────────────────

READ_WPM = 230  # skim pace used for the "N min read" chip


def estimate_read_minutes(html: str) -> int:
    """Rough reading time from the visible text of the rendered email."""
    import re
    text = re.sub(r"<(style|title)[\s\S]*?</\1>", " ", html)
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return max(1, round(len(text.split()) / READ_WPM))


def render_newsletter_v6(
    date: str,
    preheader: str,
    market_brief: MarketBrief,
    publications: list[Publication],
    top_stories: list,
    total_articles: int,
    radar: list,
    radar_more: int,
    psd_movers: list,
    psd_release_label: str,
    archive_url: str,
    signals_url: str,
    feedback_url: str,
) -> str:
    """Render the v6 email. Items are pre-selected by ranking.py.

    autoescape is ON: titles and summaries come from third-party feeds.
    Rendered twice so the read-time chip reflects the final text.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("newsletter_v6.html")
    ctx = dict(
        date=date, preheader=preheader, read_minutes=1,
        market_brief=market_brief, publications=publications,
        top_stories=top_stories, total_articles=total_articles,
        radar=radar, radar_more=radar_more,
        psd_movers=psd_movers, psd_release_label=psd_release_label,
        archive_url=archive_url, signals_url=signals_url, feedback_url=feedback_url,
    )
    ctx["read_minutes"] = estimate_read_minutes(template.render(**ctx))
    html = template.render(**ctx)
    logger.info(
        f"Rendered v6: {len(publications)} pubs, {len(top_stories)}/{total_articles} stories, "
        f"{len(radar)} radar (+{radar_more}), {len(psd_movers)} PSD movers, "
        f"{len(html.encode()) // 1024} KB, ~{ctx['read_minutes']} min"
    )
    return html
