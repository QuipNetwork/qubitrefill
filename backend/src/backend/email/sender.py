"""Email senders for the registration API-key handoff.

``get_email_sender`` is a FastAPI dependency: it returns the Proton SMTP sender
when ``SMTP_PASSWORD`` is configured, otherwise a console logger (dev/offline).
Tests override the dependency with ``FakeEmailSender`` to capture the sent key
without hitting the network.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

import aiosmtplib

from .. import config

log = logging.getLogger(__name__)


def _render(name: str, api_key: str) -> tuple[str, str, str]:
    subject = "Your Qubitrefill API key"
    text = (
        f"Hi {name},\n\n"
        f"Your Qubitrefill API key:\n\n    {api_key}\n\n"
        "Send it as `Authorization: Bearer <key>` on every request. "
        "Keep it secret — it is the only credential for your account.\n"
    )
    html = (
        f"<p>Hi {name},</p>"
        "<p>Your Qubitrefill API key:</p>"
        f"<pre style='font-size:15px;padding:12px;background:#f4f4f4;border-radius:8px'>{api_key}</pre>"
        "<p>Send it as <code>Authorization: Bearer &lt;key&gt;</code> on every request. "
        "Keep it secret — it is the only credential for your account.</p>"
    )
    return subject, text, html


class EmailSender(Protocol):
    async def send_api_key(self, to_email: str, name: str, api_key: str) -> None: ...


class ProtonSmtpEmailSender:
    """Sends via Proton's SMTP submission endpoint (smtp.protonmail.ch:587)."""

    async def send_api_key(self, to_email: str, name: str, api_key: str) -> None:
        subject, text, html = _render(name, api_key)
        msg = EmailMessage()
        msg["From"] = config.EMAIL_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        await aiosmtplib.send(
            msg,
            hostname=config.SMTP_HOST,
            port=config.SMTP_PORT,
            username=config.SMTP_USERNAME or parseaddr(config.EMAIL_FROM)[1],
            password=config.SMTP_PASSWORD,
            start_tls=config.SMTP_STARTTLS,
            timeout=10.0,
        )


class ConsoleEmailSender:
    """Dev fallback — logs the key instead of sending it."""

    async def send_api_key(self, to_email: str, name: str, api_key: str) -> None:
        log.warning("[email:console] API key for %s <%s>: %s", name, to_email, api_key)


class FakeEmailSender:
    """Test double — records every send for assertion."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send_api_key(self, to_email: str, name: str, api_key: str) -> None:
        self.sent.append({"email": to_email, "name": name, "api_key": api_key})


def get_email_sender() -> EmailSender:
    if config.SMTP_PASSWORD:
        return ProtonSmtpEmailSender()
    return ConsoleEmailSender()
