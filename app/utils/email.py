import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body_text: str, body_html: str = "") -> None:
    """Send an email in a thread pool so it doesn't block the event loop."""
    try:
        await asyncio.to_thread(_send_sync, to, subject, body_text, body_html)
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)


def _send_sync(to: str, subject: str, body_text: str, body_html: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        logger.warning("SMTP_HOST not set — email to %s not sent", to)
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", user) or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)

    logger.info("Email sent to %s: %s", to, subject)
