"""The global rate limit on POST /agents (registration sends one email per success)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.api.app import create_app
from backend.db.engine import session_scope
from backend.db.models import Agent
from backend.email.sender import FakeEmailSender, get_email_sender

_SLIDERS = {"rebalanceFrequency": 50, "riskPreference": 70, "maxPositionSize": 50}
_BASKET = ["BTC", "ETH", "IONQ", "QBTS"]


def _body(name: str) -> dict:
    return {
        "name": name,
        "email": f"{name.lower()}@example.com",
        "sliders": _SLIDERS,
        "assets": _BASKET,
    }


@pytest.fixture
def fake_email() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def app(fake_email):
    app = create_app()
    app.dependency_overrides[get_email_sender] = lambda: fake_email
    return app


@pytest.fixture
async def client(app):
    # No lifespan here: conftest already set up the engine and tables, and the
    # digest scheduler isn't needed. Requests run concurrently on one event loop.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _caps(monkeypatch, *, hour: int, day: int) -> None:
    monkeypatch.setattr(config, "REGISTER_LIMIT_PER_HOUR", hour)
    monkeypatch.setattr(config, "REGISTER_LIMIT_PER_DAY", day)


async def _seed(count: int, age: timedelta) -> None:
    """Insert ``count`` agents whose created_at lies ``age`` in the past."""
    created = (datetime.now(UTC) - age).isoformat()
    async with session_scope() as session:
        for i in range(count):
            session.add(
                Agent(
                    id=f"seed{age.total_seconds():.0f}x{i}",
                    name=f"seed-{age.total_seconds():.0f}-{i}",
                    email=f"seed-{age.total_seconds():.0f}-{i}@example.com",
                    sliders=_SLIDERS,
                    assets=_BASKET,
                    bankroll=config.BANKROLL_USD,
                    holdings_units={},
                    total=config.BANKROLL_USD,
                    pl_usd=0.0,
                    pl_pct=0.0,
                    jobs_solved=0,
                    primary_provider="CPU",
                    created_at=created,
                )
            )
        await session.commit()


async def _agent_count() -> int:
    async with session_scope() as session:
        return len((await session.execute(Agent.__table__.select())).all())


async def test_hourly_cap_answers_429_and_sends_nothing(client, fake_email, monkeypatch):
    _caps(monkeypatch, hour=2, day=50)

    assert (await client.post("/agents", json=_body("Neo"))).status_code == 201
    assert (await client.post("/agents", json=_body("Trinity"))).status_code == 201

    third = await client.post("/agents", json=_body("Morpheus"))
    assert third.status_code == 429, third.text
    # The oldest of the two ages out of the hour in just under 3600 s.
    retry = int(third.headers["Retry-After"])
    assert 3500 < retry <= 3600
    assert len(fake_email.sent) == 2, "a refused registration must not send an email"
    assert await _agent_count() == 2, "a refused registration must not create an agent"


async def test_daily_cap_counts_the_last_24_hours_only(client, fake_email, monkeypatch):
    _caps(monkeypatch, hour=50, day=2)

    # Two registrations 25 h ago are outside the day window: room for new ones.
    await _seed(2, timedelta(hours=25))
    assert (await client.post("/agents", json=_body("Neo"))).status_code == 201

    # One more 2 h ago fills the day (2 in window), though the hour has room.
    await _seed(1, timedelta(hours=2))
    full = await client.post("/agents", json=_body("Trinity"))
    assert full.status_code == 429, full.text
    # The 2 h old one leaves the window after about 22 h.
    retry = int(full.headers["Retry-After"])
    assert 22 * 3600 - 120 < retry <= 22 * 3600
    assert len(fake_email.sent) == 1


async def test_409_and_422_are_unchanged_and_do_not_count(client, fake_email, monkeypatch):
    _caps(monkeypatch, hour=2, day=50)

    assert (await client.post("/agents", json=_body("Neo"))).status_code == 201
    assert (await client.post("/agents", json=_body("Neo"))).status_code == 409
    bad = _body("Smith") | {"assets": ["BTC"]}  # below the minimum basket
    assert (await client.post("/agents", json=bad)).status_code == 422
    # Neither the 409 nor the 422 used up the cap: one more fits.
    assert (await client.post("/agents", json=_body("Trinity"))).status_code == 201

    # While the cap is full, a duplicate still says 409 and a bad body still 422.
    assert (await client.post("/agents", json=_body("Morpheus"))).status_code == 429
    assert (await client.post("/agents", json=_body("Neo"))).status_code == 409
    assert (await client.post("/agents", json=bad)).status_code == 422
    assert len(fake_email.sent) == 2


async def test_concurrent_registrations_cannot_overshoot_the_cap(client, fake_email, monkeypatch):
    _caps(monkeypatch, hour=2, day=50)

    names = ["Neo", "Trinity", "Morpheus", "Niobe", "Tank"]
    responses = await asyncio.gather(*(client.post("/agents", json=_body(n)) for n in names))
    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 201, 429, 429, 429], [r.text for r in responses]
    assert len(fake_email.sent) == 2
    assert await _agent_count() == 2


async def test_default_caps_are_enforced(client, fake_email):
    """No overrides: the shipped hourly cap is live, not a value that means 'off'."""
    await _seed(config.REGISTER_LIMIT_PER_HOUR, timedelta(minutes=10))
    refused = await client.post("/agents", json=_body("Neo"))
    assert refused.status_code == 429, refused.text
    assert fake_email.sent == []


def test_register_agent_mcp_tool_is_limited_too(app, fake_email, monkeypatch):
    """The MCP tool reaches the same route through the app's ASGI stack."""
    _caps(monkeypatch, hour=1, day=50)
    accept = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as c:
        assert c.post("/agents", json=_body("Neo")).status_code == 201

        init = c.post(
            "/mcp",
            headers=accept,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        sid = {"mcp-session-id": init.headers["mcp-session-id"]}
        c.post(
            "/mcp",
            headers=accept | sid,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        call = c.post(
            "/mcp",
            headers=accept | sid,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "register_agent", "arguments": _body("Trinity")},
            },
        )

    text = call.text
    if not text.lstrip().startswith("{"):  # SSE frame
        text = next(ln[5:] for ln in text.splitlines() if ln.startswith("data:"))
    result = json.loads(text)["result"]
    assert result["isError"], result
    assert "too many registrations" in json.dumps(result)
    assert len(fake_email.sent) == 1


@pytest.mark.parametrize("raw", ["0", "-1", "lots", ""])
def test_limits_refuse_values_that_would_switch_them_off(monkeypatch, raw):
    monkeypatch.setenv("REGISTER_LIMIT_TEST", raw)
    with pytest.raises(ValueError, match="REGISTER_LIMIT_TEST must be a positive integer"):
        config._positive_int("REGISTER_LIMIT_TEST", 5)


def test_limits_read_the_environment_and_default_on(monkeypatch):
    monkeypatch.delenv("REGISTER_LIMIT_TEST", raising=False)
    assert config._positive_int("REGISTER_LIMIT_TEST", 5) == 5
    monkeypatch.setenv("REGISTER_LIMIT_TEST", "7")
    assert config._positive_int("REGISTER_LIMIT_TEST", 5) == 7
    assert config.REGISTER_LIMIT_PER_HOUR >= 1
    assert config.REGISTER_LIMIT_PER_DAY >= 1
