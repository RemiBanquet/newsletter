"""
Email delivery via Lemlist API.
Sends newsletter to internal + external groups, plus admin reports.
"""

import logging
import os
from datetime import datetime, timezone

import requests

from models import PipelineConfig, RecipientGroup, RunMetrics
from constants import (
    LEMLIST_API_BASE, LEMLIST_SEND_USER_ID,
    LEMLIST_SEND_EMAIL, LEMLIST_SEND_MAILBOX_ID,
)

logger = logging.getLogger(__name__)


def _send_via_lemlist(
    api_key: str,
    lead_id: str,
    subject: str,
    html_body: str,
    cc: list[str] = None,
) -> bool:
    """Send a single email via Lemlist inbox API."""
    payload = {
        "sendUserId": LEMLIST_SEND_USER_ID,
        "sendUserEmail": LEMLIST_SEND_EMAIL,
        "sendUserMailboxId": LEMLIST_SEND_MAILBOX_ID,
        "leadId": lead_id,
        "subject": subject,
        "message": html_body,
    }
    if cc:
        payload["cc"] = cc

    try:
        response = requests.post(
            f"{LEMLIST_API_BASE}/inbox/send",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        if response.ok:
            logger.info(f"Lemlist send OK (lead={lead_id}, cc={len(cc or [])})")
            return True
        else:
            logger.error(f"Lemlist send failed: {response.status_code} — {response.text}")
            return False
    except Exception as e:
        logger.error(f"Lemlist send error: {e}")
        return False


def send_newsletter(
    config: PipelineConfig,
    subject: str,
    html_body: str,
    metrics: RunMetrics,
    test_mode: bool = False,
) -> bool:
    """Send newsletter to all recipient groups via Lemlist."""
    api_key = os.getenv("LEMLIST_API_KEY")
    if not api_key:
        logger.error("LEMLIST_API_KEY not set")
        return False

    if test_mode:
        # In test mode, just send to admin with no CC
        admin_lead_id = os.getenv("LEMLIST_ADMIN_LEAD_ID", "")
        if admin_lead_id:
            ok = _send_via_lemlist(api_key, admin_lead_id, f"[TEST] {subject}", html_body)
            metrics.emails_sent += 1 if ok else 0
            metrics.emails_failed += 0 if ok else 1
            return ok
        else:
            logger.warning("No LEMLIST_ADMIN_LEAD_ID set — skipping send in test mode")
            return True

    groups = config.recipient_groups()
    all_ok = True

    for group_type, group_data in groups.items():
        lead_id = group_data["lead_id"]
        cc = group_data["cc"]

        if not lead_id:
            logger.error(f"No lead_id for {group_type.value} group — skipping")
            metrics.emails_failed += 1
            all_ok = False
            continue

        ok = _send_via_lemlist(api_key, lead_id, subject, html_body, cc)
        if ok:
            metrics.emails_sent += 1
        else:
            metrics.emails_failed += 1
            all_ok = False

    return all_ok


def send_admin_report(
    metrics: RunMetrics,
    success: bool,
    error_message: str = "",
    test_mode: bool = False,
):
    """Send admin report email with run KPIs or failure alert."""
    api_key = os.getenv("LEMLIST_API_KEY")
    admin_lead_id = os.getenv("LEMLIST_ADMIN_LEAD_ID", "")

    if not api_key or not admin_lead_id:
        logger.warning("Cannot send admin report: missing API key or admin lead ID")
        # Print to console as fallback
        _print_admin_report(metrics, success, error_message)
        return

    if success:
        subject = f"✅ Agri-Digest ran OK — {metrics.articles_accepted} articles, {metrics.publications_accepted} pubs"
        body = _build_success_report_html(metrics)
    else:
        subject = f"❌ Agri-Digest FAILED — {error_message[:80]}"
        body = _build_failure_report_html(metrics, error_message)

    if test_mode:
        subject = f"[TEST] {subject}"

    _send_via_lemlist(api_key, admin_lead_id, subject, body)


def _print_admin_report(metrics: RunMetrics, success: bool, error_message: str = ""):
    """Print admin report to console (fallback when Lemlist not available)."""
    metrics.estimate_cost()
    print("\n" + "=" * 60)
    print("ADMIN REPORT" + (" — SUCCESS" if success else " — FAILED"))
    print("=" * 60)
    if not success:
        print(f"ERROR: {error_message}")
    print(f"Articles:      {metrics.articles_accepted}/{metrics.articles_fetched} accepted")
    print(f"Publications:  {metrics.publications_accepted}/{metrics.publications_fetched} accepted")
    print(f"Signals:       {metrics.signals_accepted}/{metrics.signals_fetched} accepted")
    print(f"Duplicates:    {metrics.articles_duplicate} articles, {metrics.publications_duplicate} pubs, {metrics.signals_duplicate} signals")
    print(f"Geocoding:     {metrics.geocoding_rate}")
    print(f"Sources:       {metrics.sources_healthy}/{metrics.sources_total} healthy")
    if metrics.source_errors:
        print(f"Source errors:  {', '.join(metrics.source_errors[:5])}")
    print(f"Claude tokens: {metrics.input_tokens:,} in / {metrics.output_tokens:,} out / {metrics.cache_read_tokens:,} cached")
    print(f"Est. cost:     ${metrics.estimated_cost_usd:.3f}")
    print(f"Emails sent:   {metrics.emails_sent}/{metrics.emails_sent + metrics.emails_failed}")
    print(f"Runtime:       {metrics.runtime_display}")
    print("=" * 60 + "\n")


def _build_success_report_html(metrics: RunMetrics) -> str:
    """Build HTML for the success admin report."""
    metrics.estimate_cost()
    return f"""
    <div style="font-family: 'Source Sans 3', sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #69BE82;">✅ Daily Agri-News Digest — Run Report</h2>
        <p style="color: #666;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr style="background: #14213D; color: white;">
                <th style="padding: 8px; text-align: left;">Metric</th>
                <th style="padding: 8px; text-align: right;">Value</th>
            </tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Articles fetched</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.articles_fetched}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Articles accepted</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee; font-weight: bold;">{metrics.articles_accepted}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Articles rejected / duplicate</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.articles_rejected} / {metrics.articles_duplicate}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Publications fetched</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.publications_fetched}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Publications accepted</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee; font-weight: bold;">{metrics.publications_accepted}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Signals fetched</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.signals_fetched}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Signals accepted</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee; font-weight: bold;">{metrics.signals_accepted}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Geocoding success</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.geocoding_rate}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Sources healthy</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.sources_healthy}/{metrics.sources_total}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Claude tokens (in/out/cached)</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.input_tokens:,} / {metrics.output_tokens:,} / {metrics.cache_read_tokens:,}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Estimated cost</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee; font-weight: bold;">${metrics.estimated_cost_usd:.3f}</td></tr>
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Emails sent</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.emails_sent}/{metrics.emails_sent + metrics.emails_failed}</td></tr>
            <tr style="background: #f0f9f0;"><td style="padding: 6px; font-weight: bold;">Runtime</td>
                <td style="padding: 6px; text-align: right; font-weight: bold;">{metrics.runtime_display}</td></tr>
        </table>

        {"<h3>Source errors</h3><ul>" + "".join(f"<li>{e}</li>" for e in metrics.source_errors) + "</ul>" if metrics.source_errors else ""}
    </div>
    """


def _build_failure_report_html(metrics: RunMetrics, error_message: str) -> str:
    """Build HTML for the failure admin report."""
    return f"""
    <div style="font-family: 'Source Sans 3', sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #e74c3c;">❌ Daily Agri-News Digest — RUN FAILED</h2>
        <p style="color: #666;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

        <div style="background: #ffeaea; border: 1px solid #e74c3c; border-radius: 8px; padding: 16px; margin: 16px 0;">
            <h3 style="margin-top: 0; color: #c0392b;">Error</h3>
            <pre style="white-space: pre-wrap; font-size: 13px;">{error_message}</pre>
        </div>

        <p>Runtime before failure: {metrics.runtime_display}</p>
        <p>Articles fetched before failure: {metrics.articles_fetched}</p>
        <p>Sources with errors: {len(metrics.source_errors)}</p>

        {"<h3>Source errors</h3><ul>" + "".join(f"<li>{e}</li>" for e in metrics.source_errors) + "</ul>" if metrics.source_errors else ""}
    </div>
    """
