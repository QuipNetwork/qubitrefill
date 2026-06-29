"""Tests for the registration API-key email senders.

The SMTP sender is exercised by faking ``aiosmtplib.send`` at the module
boundary, so no network or real SMTP server is touched.
"""

from __future__ import annotations

from email.message import EmailMessage

import backend.email.sender as sender_mod
from backend import config
from backend.email.sender import (
    ConsoleEmailSender,
    SmtpEmailSender,
    get_email_sender,
)


class _CapturingSend:
    """Fake for ``aiosmtplib.send`` — records the message and connection kwargs."""

    def __init__(self) -> None:
        self.message: EmailMessage | None = None
        self.kwargs: dict = {}

    async def __call__(self, message: EmailMessage, **kwargs: object) -> None:
        self.message = message
        self.kwargs = kwargs


async def test_smtp_sender_delivers_key_over_starttls(monkeypatch):
    capture = _CapturingSend()
    monkeypatch.setattr(sender_mod.aiosmtplib, "send", capture)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.resend.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USERNAME", "resend")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "re_token")
    monkeypatch.setattr(config, "SMTP_STARTTLS", True)
    monkeypatch.setattr(config, "EMAIL_FROM", "Qubitrefill <noreply@quip.network>")

    await SmtpEmailSender().send_api_key("agent@example.com", "Ada", "key-123")

    msg = capture.message
    assert msg is not None
    assert msg["To"] == "agent@example.com"
    assert msg["From"] == "Qubitrefill <noreply@quip.network>"
    assert msg["Subject"] == "Your Qubitrefill API key"
    body = msg.get_body(preferencelist=("plain",))
    assert body is not None
    assert "key-123" in body.get_content()
    assert capture.kwargs["hostname"] == "smtp.resend.com"
    assert capture.kwargs["port"] == 587
    assert capture.kwargs["username"] == "resend"
    assert capture.kwargs["password"] == "re_token"
    assert capture.kwargs["start_tls"] is True


async def test_smtp_sender_uses_configured_username(monkeypatch):
    # Resend requires the literal username "resend"; the sender must pass
    # SMTP_USERNAME through unchanged rather than deriving it from EMAIL_FROM.
    capture = _CapturingSend()
    monkeypatch.setattr(sender_mod.aiosmtplib, "send", capture)
    monkeypatch.setattr(config, "SMTP_USERNAME", "resend")
    monkeypatch.setattr(config, "EMAIL_FROM", "Qubitrefill <noreply@quip.network>")

    await SmtpEmailSender().send_api_key("agent@example.com", "Ada", "key-123")

    assert capture.kwargs["username"] == "resend"


def test_get_email_sender_uses_smtp_when_password_present(monkeypatch):
    monkeypatch.setattr(config, "SMTP_PASSWORD", "token")
    assert isinstance(get_email_sender(), SmtpEmailSender)


def test_get_email_sender_falls_back_to_console_without_password(monkeypatch):
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    assert isinstance(get_email_sender(), ConsoleEmailSender)
