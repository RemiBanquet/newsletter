"""
Email delivery via SMTP (Office 365 / Outlook).
Sends newsletter to all recipients via BCC, plus admin reports.
"""

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from models import PipelineConfig, RunMetrics
from constants import SENDER_EMAIL

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _send_smtp(
    to: str,
    subject: str,
    html_body: str,
    cc: list[str] = None,
    bcc: list[str] = None,
) -> bool:
    """Send a single email via Office 365 SMTP."""
    smtp_user = os.getenv("SMTP_USERNAME", SENDER_EMAIL)
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not smtp_pass:
        logger.error("SMTP_PASSWORD not set")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Hyperplan <{SENDER_EMAIL}>"
    msg["To"] = f"Hyperplan Newsletter <{to}>"
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    # BCC is intentionally NOT added to headers

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    all_recipients = [to] + (cc or []) + (bcc or [])

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(SENDER_EMAIL, all_recipients, msg.as_string())

        logger.info(f"SMTP send OK (to={to}, bcc={len(bcc or [])})")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP auth failed: {e} — check SMTP_PASSWORD and that SMTP AUTH is enabled for {smtp_user}")
        return False
    except Exception as e:
        logger.error(f"SMTP send error: {e}")
        return False


def send_newsletter(
    config: PipelineConfig,
    subject: str,
    html_body: str,
    metrics: RunMetrics,
    test_mode: bool = False,
) -> bool:
    """Send newsletter to all recipients via SMTP (BCC)."""
    if not os.getenv("SMTP_PASSWORD"):
        logger.error("SMTP_PASSWORD not set — cannot send")
        return False

    if test_mode:
        admin_email = os.getenv("ADMIN_EMAIL", SENDER_EMAIL)
        ok = _send_smtp(to=admin_email, subject=f"[TEST] {subject}", html_body=html_body)
        metrics.emails_sent += 1 if ok else 0
        metrics.emails_failed += 0 if ok else 1
        return ok

    # Collect all active recipient emails
    all_emails = [r.email for r in config.active_recipients if r.email]
    if not all_emails:
        logger.error("No active recipients found")
        metrics.emails_failed += 1
        return False

    # Send to self, BCC all recipients
    ok = _send_smtp(to=SENDER_EMAIL, subject=subject, html_body=html_body, bcc=all_emails)
    if ok:
        metrics.emails_sent += 1
        logger.info(f"Newsletter sent to {len(all_emails)} recipients via BCC")
    else:
        metrics.emails_failed += 1

    return ok


def send_admin_report(
    metrics: RunMetrics,
    success: bool,
    error_message: str = "",
    test_mode: bool = False,
):
    """Send admin report email with run KPIs or failure alert."""
    admin_email = os.getenv("ADMIN_EMAIL", SENDER_EMAIL)

    if not os.getenv("SMTP_PASSWORD"):
        logger.warning("Cannot send admin report: SMTP_PASSWORD not set")
        _print_admin_report(metrics, success, error_message)
        return

    if success:
        # Promote subject line to a warning if any source has been silent ≥3 runs
        warn_prefix = ""
        if getattr(metrics, "silent_sources", None):
            dead = [s for s in metrics.silent_sources if s.get("status") == "DEAD"]
            if dead:
                warn_prefix = f"🔴 {len(dead)} DEAD source(s) — "
            else:
                warn_prefix = f"⚠️ {len(metrics.silent_sources)} quiet source(s) — "
        subject = (
            f"{warn_prefix}✅ Agri-Digest ran OK — "
            f"{metrics.articles_accepted} articles, {metrics.publications_accepted} pubs"
        )
        body = _build_success_report_html(metrics)
    else:
        subject = f"❌ Agri-Digest FAILED — {error_message[:80]}"
        body = _build_failure_report_html(metrics, error_message)

    if test_mode:
        subject = f"[TEST] {subject}"

    _send_smtp(admin_email, subject, body)


def _print_admin_report(metrics: RunMetrics, success: bool, error_message: str = ""):
    """Print admin report to console (fallback when SMTP not available)."""
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
    if getattr(metrics, "silent_sources", None):
        print(
            "⚠️  Silent sources (≥3 runs): "
            + ", ".join(f"{s['name']} ({s['streak']} runs)" for s in metrics.silent_sources[:8])
        )
    print(f"Claude tokens: {metrics.input_tokens:,} in / {metrics.output_tokens:,} out / {metrics.cache_read_tokens:,} cached")
    print(f"Est. cost:     ${metrics.estimated_cost_usd:.3f}")
    print(f"Emails sent:   {metrics.emails_sent}/{metrics.emails_sent + metrics.emails_failed}")
    print(f"Market brief:  {metrics.brief_status or 'n/a'}")
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
            <tr><td style="padding: 6px; border-bottom: 1px solid #eee;">Market brief</td>
                <td style="padding: 6px; text-align: right; border-bottom: 1px solid #eee;">{metrics.brief_status or 'n/a'}</td></tr>
            <tr style="background: #f0f9f0;"><td style="padding: 6px; font-weight: bold;">Runtime</td>
                <td style="padding: 6px; text-align: right; font-weight: bold;">{metrics.runtime_display}</td></tr>
        </table>

        {"<h3>Source errors</h3><ul>" + "".join(f"<li>{e}</li>" for e in metrics.source_errors) + "</ul>" if metrics.source_errors else ""}

        {_build_silent_sources_block(metrics) if getattr(metrics, "silent_sources", None) else ""}
    </div>
    """


def _build_silent_sources_block(metrics: RunMetrics) -> str:
    """Highlight sources that have returned 0 entries for ≥3 consecutive runs."""
    _status_colors = {"DEAD": "#c0392b", "QUIET": "#e67e22", "SILENT": "#888888"}
    rows = "".join(
        f"<tr>"
        f"<td style='padding: 6px; border-bottom: 1px solid #eee;'>{s['name']}</td>"
        f"<td style='padding: 6px; border-bottom: 1px solid #eee; font-weight: bold; "
        f"color: {_status_colors.get(s.get('status') or 'SILENT', '#888888')};'>"
        f"{s.get('status') or 'SILENT'}</td>"
        f"<td style='padding: 6px; text-align: right; border-bottom: 1px solid #eee;'>"
        f"{s['streak']} runs (limit {s.get('threshold', '?')})</td>"
        f"<td style='padding: 6px; border-bottom: 1px solid #eee; color: #666; "
        f"font-size: 12px;'>last entry: {s.get('last_seen') or 'never'}</td>"
        f"</tr>"
        for s in metrics.silent_sources
    )
    return f"""
    <div style="background: #fff8e6; border: 1px solid #f0ad4e; border-radius: 8px; padding: 16px; margin: 16px 0;">
        <h3 style="margin-top: 0; color: #c0392b;">⚠️ Sources past their silence threshold</h3>
        <p style="color: #666; font-size: 13px; margin: 0 0 8px 0;">
            <b style="color: #c0392b;">DEAD</b> = the fetch itself returns nothing
            (broken URL, bot block, dead feed) — act on these.
            <b style="color: #e67e22;">QUIET</b> = the feed is fetched fine but no
            item passed the filters for longer than its expected cadence — check
            keywords or accept. <b style="color: #888;">SILENT</b> = scraper with
            no raw-count tracking — judge case by case.
        </p>
        <table style="width: 100%; border-collapse: collapse;">
            <tr style="background: #f5f5f5;">
                <th style="padding: 6px; text-align: left;">Source</th>
                <th style="padding: 6px; text-align: left;">Status</th>
                <th style="padding: 6px; text-align: right;">Streak</th>
                <th style="padding: 6px; text-align: left;">Last successful entry</th>
            </tr>
            {rows}
        </table>
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
