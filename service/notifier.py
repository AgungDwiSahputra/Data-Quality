# service/notifier.py
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from jinja2 import Environment, FileSystemLoader, select_autoescape

from service.config import settings
from service.runner import JobResult

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_email(result: JobResult) -> tuple[str, str]:
    """Return (subject, html_body)."""
    tpl = _jinja.get_template("email_report.html")
    verdict = {
        "success": "✅ LULUS",
        "failed": "❌ GAGAL (ada blocking)",
        "error": "⚠️ ERROR",
    }.get(result.status, result.status)

    subject = (f"{settings.mail_subject_prefix} "
               f"{verdict} — {result.dataset or 'ALL'} "
               f"({result.config}, {result.source})")
    html = tpl.render(result=result.to_dict(), verdict=verdict)
    return subject, html


def send_email(result: JobResult, to: list[str] | None = None) -> None:
    if not settings.smtp_host:
        raise RuntimeError("SMTP_HOST belum diset di .env")

    recipients = to or [x.strip() for x in settings.mail_to_default.split(",") if x.strip()]
    if not recipients:
        raise RuntimeError("Tidak ada penerima email. Set MAIL_TO_DEFAULT atau kirim 'to' di request.")

    subject, html = render_email(result)

    msg = EmailMessage()
    msg["From"] = formataddr(("DQ Service", settings.mail_from))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content("Lihat versi HTML untuk detail laporan.")
    msg.add_alternative(html, subtype="html")

    # Lampirkan report JSON & MD kalau ada
    for path, mime in [(result.report_json, "application/json"),
                       (result.report_md, "text/markdown")]:
        if path and os.path.exists(path):
            with open(path, "rb") as fh:
                msg.add_attachment(
                    fh.read(),
                    maintype=mime.split("/")[0],
                    subtype=mime.split("/")[1],
                    filename=os.path.basename(path),
                )

    if settings.smtp_tls:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
            s.starttls(context=ctx)
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)