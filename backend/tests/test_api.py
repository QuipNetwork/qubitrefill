"""API surface via FastAPI TestClient: registration, API-key auth, and a WS push."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.email.sender import FakeEmailSender, get_email_sender

requires_gurobi = pytest.mark.skipif(
    importlib.util.find_spec("gurobipy") is None, reason="gurobipy not installed"
)

_SLIDERS = {"rebalanceFrequency": 50, "riskPreference": 70, "maxPositionSize": 50}
_BASKET = ["BTC", "ETH", "IONQ", "QBTS"]


@pytest.fixture
def fake_email() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def client(fake_email):
    app = create_app()
    app.dependency_overrides[get_email_sender] = lambda: fake_email
    with TestClient(app) as c:
        yield c


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _register(client: TestClient, fake_email: FakeEmailSender, name: str = "Neo") -> str:
    """Register an agent and return its API key (read from the captured email)."""
    response = client.post(
        "/agents",
        json={
            "name": name,
            "email": f"{name.lower()}@example.com",
            "sliders": _SLIDERS,
            "assets": _BASKET,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["bankroll"] == 10_000.0
    api_key = fake_email.sent[-1]["api_key"]
    # email-only delivery: the key (and any id/qr) must NOT be in the response
    assert api_key not in str(body)
    assert "agentId" not in body and "qrUrl" not in body
    return api_key


def test_register_emails_key_and_get_me(client, fake_email):
    key = _register(client, fake_email)
    assert fake_email.sent[-1]["email"] == "neo@example.com"

    me = client.get("/agents/me", headers=_auth(key))
    assert me.status_code == 200
    body = me.json()
    assert body["sliders"]["riskPreference"] == 70
    assert body["assets"] == _BASKET
    assert body["email"] == "neo@example.com"


def test_protected_endpoints_require_a_valid_key(client, fake_email):
    _register(client, fake_email)
    assert client.get("/agents/me").status_code == 401  # no header
    assert client.get("/agents/me", headers=_auth("not-a-real-key")).status_code == 401
    assert client.post("/agents/optimize", json={}).status_code == 401
    assert client.get("/agents/market").status_code == 401


def test_duplicate_email_or_name_is_409(client, fake_email):
    _register(client, fake_email, name="Trinity")
    dup_email = client.post(
        "/agents",
        json={"name": "Someone", "email": "trinity@example.com", "sliders": _SLIDERS, "assets": _BASKET},
    )
    assert dup_email.status_code == 409
    dup_name = client.post(
        "/agents",
        json={"name": "Trinity", "email": "other@example.com", "sliders": _SLIDERS, "assets": _BASKET},
    )
    assert dup_name.status_code == 409


def test_leaderboard_is_public_and_hides_the_key(client, fake_email):
    _register(client, fake_email, name="Morpheus")
    board = client.get("/leaderboard").json()  # no auth needed
    assert any(entry["name"] == "Morpheus" for entry in board)
    for entry in board:
        assert "agentId" not in entry and "agent_id" not in entry


@requires_gurobi
def test_optimize_returns_routing_result(client, fake_email):
    key = _register(client, fake_email)
    response = client.post("/agents/optimize", json={}, headers=_auth(key))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "first"
    assert body["providerType"] in ("QPU", "CPU")
    assert {entry["ticker"] for entry in body["portfolio"]} == set(_BASKET)
    assert sum(entry["pct"] for entry in body["portfolio"]) == pytest.approx(100.0, abs=1e-6)


@requires_gurobi
def test_websocket_streams_agent_update(client, fake_email):
    key = _register(client, fake_email, name="Cypher")
    client.post("/agents/optimize", json={}, headers=_auth(key))
    with client.websocket_connect("/ws/agent/cypher") as socket:
        client.post("/agents/optimize", json={}, headers=_auth(key))  # retune → push
        update = socket.receive_json()
        assert {"plUSD", "plPct", "total"} <= set(update)


def test_market_returns_asset_data_for_basket(client, fake_email):
    key = _register(client, fake_email)
    response = client.get("/agents/market", headers=_auth(key))
    assert response.status_code == 200
    body = response.json()
    tickers_returned = {a["ticker"] for a in body["assets"]}
    assert tickers_returned == set(_BASKET)
    for asset in body["assets"]:
        assert isinstance(asset["mu"], float)
        assert asset["assetClass"] in ("crypto", "stock")
        assert asset["units"] == 0.0  # no solve yet


def test_basket_below_minimum_is_rejected(client):
    response = client.post(
        "/agents",
        json={"name": "Tiny", "email": "tiny@example.com", "sliders": _SLIDERS, "assets": ["BTC"]},
    )
    assert response.status_code == 422
    assert "at least" in response.json()["detail"]


def test_registration_requires_email(client):
    response = client.post("/agents", json={"name": "NoMail", "sliders": _SLIDERS, "assets": _BASKET})
    assert response.status_code == 422  # email is required


@requires_gurobi
def test_optimize_accepts_a_new_basket(client, fake_email):
    key = _register(client, fake_email)
    client.post("/agents/optimize", json={}, headers=_auth(key))
    response = client.post(
        "/agents/optimize", json={"assets": ["HON", "GOOGL", "IBM"]}, headers=_auth(key)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "retune"
    assert {e["ticker"] for e in body["portfolio"]} == {"HON", "GOOGL", "IBM"}

    too_small = client.post("/agents/optimize", json={"assets": ["BTC"]}, headers=_auth(key))
    assert too_small.status_code == 422
