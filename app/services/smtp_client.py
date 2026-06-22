"""Workspace SMTP sender (Gmail, app password).

Sends through ``smtp.gmail.com`` on port 587 (STARTTLS) authenticating with a
Google Workspace app password. ``aiosmtplib`` is imported lazily so the rest of
the notification code (digest rendering, gating) stays unit-testable without the
dependency installed.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage

from app.config import Settings

logger = logging.getLogger(__name__)


class SmtpError(RuntimeError):
    """Raised when an email can't be sent (config missing or transport error)."""


class SmtpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str,
        bcc: str | None = None,
    ) -> None:
        s = self.settings
        if not s.smtp_username or not s.smtp_password:
            raise SmtpError("SMTP_USERNAME / SMTP_PASSWORD are not configured")

        msg = EmailMessage()
        msg["From"] = s.email_from
        msg["To"] = to
        msg["Reply-To"] = s.email_reply_to
        msg["Subject"] = subject
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        # Bcc is an envelope recipient, never a header (so the client can't see it).
        recipients = [to]
        if bcc:
            recipients.append(bcc)

        import aiosmtplib  # lazy import

        try:
            await aiosmtplib.send(
                msg,
                recipients=recipients,
                hostname=s.smtp_host,
                port=s.smtp_port,
                username=s.smtp_username,
                password=s.smtp_password,
                start_tls=True,
            )
        except Exception as exc:  # noqa: BLE001 — normalise to our error type
            raise SmtpError(f"SMTP send failed: {exc}") from exc
        logger.info("email.sent to=%s bcc=%s subject=%r", to, bool(bcc), subject)
