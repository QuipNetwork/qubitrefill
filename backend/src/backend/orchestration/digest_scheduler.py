"""Registration-digest scheduler — a single in-app daily email to the team.

A background task started in the app lifespan wakes hourly and sends the digest
at most once per UTC calendar day, at/after ``DIGEST_HOUR_UTC``. Idempotency is a
one-row ``digest_state`` marker guarded by a Postgres advisory lock, so neither a
restart nor concurrent workers (``WEB_CONCURRENCY>1``) can double-send. The task
is **fail-closed**: it does
not even start unless both ``DIGEST_RECIPIENTS`` and real SMTP are configured, so
the contact list can never fall through to the console logger.

``run_once`` (scheduled, guarded) and ``send_now`` (forced, for the CLI) share
one build path and are pure enough to unit-test with a fake sender + session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..db.engine import session_scope
from ..db.models import DigestState
from ..email.sender import EmailSender, get_email_sender
from ..reporting import registrations

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = 3600  # wake hourly; the date/hour guards decide when to send

# Cross-process/instance mutex for the send. A Postgres advisory lock is
# cluster-global and transaction-scoped (auto-released on commit OR rollback), so
# acquiring it before the guard makes the whole check→send→mark sequence atomic
# even when WEB_CONCURRENCY>1 runs one scheduler per worker. Key is arbitrary but
# fixed — the ASCII bytes of "DIGEST".
_SEND_LOCK_KEY = 0x444947455354  # b"DIGEST"


async def _acquire_send_lock(session: AsyncSession) -> None:
    """Serialize the digest send across workers/instances until this txn ends."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SEND_LOCK_KEY})


async def _get_state(session: AsyncSession) -> DigestState:
    state = await session.get(DigestState, 1)
    if state is None:
        state = DigestState(id=1, last_sent_at=None)
        session.add(state)
    return state


async def _build_and_send(
    session: AsyncSession, sender: EmailSender, now: datetime, since: str | None
) -> registrations.RegistrationStats:
    """Fetch → stats → CSV → send → advance the marker (committed)."""
    today = now.date().isoformat()
    rows = await registrations.fetch_rows(session)
    stats = registrations.compute_stats(rows, since)
    csv_bytes = registrations.rows_to_csv(rows).encode("utf-8")
    subject, body, html = registrations.render(stats, today)
    await sender.send_digest(
        recipients=config.DIGEST_RECIPIENTS,
        subject=subject,
        text=body,
        html=html,
        csv_bytes=csv_bytes,
        csv_filename=f"registrations-{today}.csv",
    )
    state = await _get_state(session)
    state.last_sent_at = now.isoformat()
    await session.commit()
    log.info(
        "registration digest sent: %d recipients, %d registrants (%d new)",
        len(config.DIGEST_RECIPIENTS),
        stats.total,
        stats.new_since,
    )
    return stats


async def run_once(session: AsyncSession, sender: EmailSender, now: datetime) -> bool:
    """Send the digest iff it is due (past DIGEST_HOUR_UTC, not yet sent today).

    Returns True when a digest was sent. The marker only advances on success, so a
    send failure is retried on the next wake.
    """
    today = now.date().isoformat()
    await _acquire_send_lock(session)
    state = await _get_state(session)
    if state.last_sent_at is not None and state.last_sent_at[:10] == today:
        return False
    if now.hour < config.DIGEST_HOUR_UTC:
        return False
    await _build_and_send(session, sender, now, state.last_sent_at)
    return True


async def send_now(
    session: AsyncSession, sender: EmailSender, now: datetime
) -> registrations.RegistrationStats:
    """Force a send regardless of the schedule (CLI/ops). Still advances the marker."""
    await _acquire_send_lock(session)
    state = await _get_state(session)
    return await _build_and_send(session, sender, now, state.last_sent_at)


def _enabled() -> tuple[bool, str]:
    if not config.DIGEST_RECIPIENTS:
        return False, "no DIGEST_RECIPIENTS set"
    if not config.SMTP_PASSWORD:
        return False, "no SMTP configured (SMTP_PASSWORD unset)"
    return True, ""


async def _loop() -> None:
    while True:
        try:
            async with session_scope() as session:
                await run_once(session, get_email_sender(), datetime.now(UTC))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("registration digest tick failed; will retry next wake")
        await asyncio.sleep(_POLL_INTERVAL_S)


def start(app) -> None:
    """Start the digest loop as a lifespan-scoped background task (if enabled)."""
    enabled, why = _enabled()
    if not enabled:
        log.info("registration digest disabled: %s", why)
        app.state.digest_task = None
        return
    log.info(
        "registration digest enabled: %d recipients, daily at %02d:00 UTC",
        len(config.DIGEST_RECIPIENTS),
        config.DIGEST_HOUR_UTC,
    )
    app.state.digest_task = asyncio.create_task(_loop())


async def stop(app) -> None:
    task = getattr(app.state, "digest_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
