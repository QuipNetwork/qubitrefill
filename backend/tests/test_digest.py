"""Registration digest: key-leak guard, allowlist, CSV injection, stats, idempotency."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime

import pytest

from backend import config
from backend.api.schemas import SliderValues
from backend.db.engine import session_scope
from backend.db.models import DigestState
from backend.email.sender import ConsoleEmailSender, FakeEmailSender
from backend.orchestration import digest_scheduler
from backend.persistence.agents import AgentRepo
from backend.reporting import registrations
from backend.reporting.registrations import (
    CSV_COLUMNS,
    RegistrationRow,
    compute_stats,
    rows_to_csv,
)

_SLIDERS = SliderValues(rebalanceFrequency=50, riskPreference=50, maxPositionSize=50)

# A recognizable secret standing in for the uuid API key (Agent.id).
_SECRET_KEY = "deadbeefdeadbeefdeadbeefdeadbeef"


def _row(name="Ada", *, email=None, opt_in=None, reach_out=None, created_at="2026-07-01T10:00:00+00:00"):
    return RegistrationRow(
        name=name,
        handle=name.lower(),
        email=email or f"{name.lower()}@example.com",
        updates_opt_in=opt_in,
        reach_out=reach_out,
        created_at=created_at,
        jobs_solved=0,
    )


async def _create(repo: AgentRepo, name: str, *, agent_id: str, opt_in=None):
    return await repo.create(
        agent_id=agent_id,
        name=name,
        email=f"{name.lower()}@example.com",
        handle=name.lower(),
        reach_out=None,
        updates_opt_in=opt_in,
        sliders=_SLIDERS,
        assets=["BTC", "ETH"],
        bankroll=10_000.0,
    )


# --- Pure projection tests (no DB) --------------------------------------------


def test_csv_header_is_the_allowlist():
    text = rows_to_csv([_row()])
    header = next(csv.reader(io.StringIO(text)))
    assert tuple(header) == CSV_COLUMNS
    assert "id" not in header  # the API key column is never present


def test_registration_row_has_no_id_field():
    assert not hasattr(_row(), "id")


def test_csv_injection_is_neutralized():
    rows = [_row(name="=cmd()", reach_out=["+telegram", "safe"])]
    parsed = list(csv.reader(io.StringIO(rows_to_csv(rows))))
    data = parsed[1]
    name_cell = data[CSV_COLUMNS.index("name")]
    reach_cell = data[CSV_COLUMNS.index("reach_out")]
    assert name_cell == "'=cmd()"
    assert reach_cell.startswith("'+telegram")


def test_bool_and_none_rendering():
    rows = [_row(opt_in=True), _row(opt_in=False), _row(opt_in=None)]
    data = list(csv.reader(io.StringIO(rows_to_csv(rows))))[1:]
    col = CSV_COLUMNS.index("updates_opt_in")
    assert [r[col] for r in data] == ["yes", "no", ""]


def test_compute_stats_counts_and_rate():
    rows = [
        _row(name="A", opt_in=True, created_at="2026-07-01T09:00:00+00:00"),
        _row(name="B", opt_in=False, created_at="2026-07-02T09:00:00+00:00"),
        _row(name="C", opt_in=True, created_at="2026-07-02T11:00:00+00:00"),
    ]
    stats = compute_stats(rows, since="2026-07-01T12:00:00+00:00")
    assert stats.total == 3
    assert stats.opted_in == 2
    assert stats.opt_in_rate == pytest.approx(2 / 3)
    assert stats.new_since == 2  # the two created on 07-02


def test_compute_stats_counts_same_day_after_send():
    """Finding 2: a registrant created after the send, on the send's own day, is
    still 'new since' — the boundary is the full send timestamp, not the date."""
    rows = [
        _row(name="Before", created_at="2026-07-02T06:00:00+00:00"),
        _row(name="After", created_at="2026-07-02T09:30:00+00:00"),
    ]
    # Previous digest sent at 07:00 the same day.
    assert compute_stats(rows, since="2026-07-02T07:00:00+00:00").new_since == 1


def test_compute_stats_first_digest_counts_all():
    rows = [_row(name="A"), _row(name="B")]
    assert compute_stats(rows, since=None).new_since == 2


def test_compute_stats_empty():
    stats = compute_stats([], since=None)
    assert stats == registrations.RegistrationStats(0, 0, 0.0, 0)


# --- DB-backed tests ----------------------------------------------------------


async def test_fetch_rows_never_exposes_api_key():
    async with session_scope() as session:
        repo = AgentRepo(session)
        await _create(repo, "Ada", agent_id=_SECRET_KEY, opt_in=True)
        await session.commit()

        rows = await registrations.fetch_rows(session)
        csv_text = rows_to_csv(rows)

    assert len(rows) == 1
    assert rows[0].email == "ada@example.com"
    # The secret key must not leak into any row or the serialized CSV.
    assert _SECRET_KEY not in csv_text
    assert all(_SECRET_KEY not in str(getattr(r, c)) for r in rows for c in CSV_COLUMNS)


async def test_run_once_is_idempotent_within_a_day(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", ["team@example.com"])
    monkeypatch.setattr(config, "DIGEST_HOUR_UTC", 7)
    sender = FakeEmailSender()

    async with session_scope() as session:
        repo = AgentRepo(session)
        await _create(repo, "Ada", agent_id=_SECRET_KEY, opt_in=True)
        await session.commit()

        day1_morning = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)
        assert await digest_scheduler.run_once(session, sender, day1_morning) is True
        # Second tick same day: already sent → no-op.
        assert await digest_scheduler.run_once(session, sender, day1_morning) is False
        # Next day → sends again.
        assert await digest_scheduler.run_once(session, sender, datetime(2026, 7, 3, 9, 0, tzinfo=UTC)) is True

    assert len(sender.digests) == 2
    assert sender.digests[0]["recipients"] == ["team@example.com"]
    assert _SECRET_KEY not in sender.digests[0]["csv"]


async def test_run_once_no_duplicate_send_under_concurrency(monkeypatch):
    """Two schedulers racing on the same UTC day send exactly once.

    With WEB_CONCURRENCY>1 each uvicorn worker runs its own ``_loop`` in a
    separate process, so the in-process ``last_sent_at`` guard cannot prevent a
    double send — only the DB advisory lock serializing check→send→mark can.
    """
    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", ["team@example.com"])
    monkeypatch.setattr(config, "DIGEST_HOUR_UTC", 7)
    sender = FakeEmailSender()

    async with session_scope() as setup:
        await _create(AgentRepo(setup), "Ada", agent_id=_SECRET_KEY)
        await setup.commit()

    now = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)

    async def tick() -> bool:
        async with session_scope() as session:
            return await digest_scheduler.run_once(session, sender, now)

    results = await asyncio.gather(tick(), tick())

    assert sum(1 for sent in results if sent) == 1  # exactly one worker sent
    assert len(sender.digests) == 1


async def test_run_once_waits_until_digest_hour(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", ["team@example.com"])
    monkeypatch.setattr(config, "DIGEST_HOUR_UTC", 7)
    sender = FakeEmailSender()

    async with session_scope() as session:
        before = datetime(2026, 7, 2, 5, 0, tzinfo=UTC)  # before the hour
        assert await digest_scheduler.run_once(session, sender, before) is False
    assert sender.digests == []


async def test_send_now_forces_regardless_of_schedule(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", ["team@example.com"])
    monkeypatch.setattr(config, "DIGEST_HOUR_UTC", 23)
    sender = FakeEmailSender()

    async with session_scope() as session:
        await _create(AgentRepo(session), "Ada", agent_id=_SECRET_KEY)
        await session.commit()
        # 03:00, well before DIGEST_HOUR_UTC=23, but send_now ignores the guard.
        stats = await digest_scheduler.send_now(session, sender, datetime(2026, 7, 2, 3, 0, tzinfo=UTC))
        state = await session.get(DigestState, 1)

    assert stats.total == 1
    assert len(sender.digests) == 1
    assert state.last_sent_at == "2026-07-02T03:00:00+00:00"


async def test_console_sender_does_not_log_pii(caplog):
    sender = ConsoleEmailSender()
    with caplog.at_level("WARNING"):
        await sender.send_digest(
            recipients=["team@example.com"],
            subject="s",
            text="t",
            html="h",
            csv_bytes=b"name,email\nAda,ada@secret.example\n",
            csv_filename="registrations.csv",
        )
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "ada@secret.example" not in joined
    assert "suppressed" in joined


def test_enabled_requires_recipients_and_smtp(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", [])
    monkeypatch.setattr(config, "SMTP_PASSWORD", "x")
    assert digest_scheduler._enabled()[0] is False

    monkeypatch.setattr(config, "DIGEST_RECIPIENTS", ["a@b.com"])
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    assert digest_scheduler._enabled()[0] is False

    monkeypatch.setattr(config, "SMTP_PASSWORD", "resend-key")
    assert digest_scheduler._enabled()[0] is True
