"""The mounted qupick MCP server: tool surface and auth-through-MCP enforcement.

These exercise the in-process fastapi-mcp mount (``/mcp``). The security-critical
assertion is that a per-agent *tool call* is gated by the same APIKeyMiddleware as
the REST surface — fastapi-mcp dispatches each tool through the app's own ASGI
stack, so the forwarded ``Authorization`` header lands on a real auth check.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import _MCP_OPERATIONS, create_app
from backend.email.sender import FakeEmailSender, get_email_sender

_SLIDERS = {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50}
_BASKET = ["BTC", "ETH", "SOL"]
_MCP_ACCEPT = "application/json, text/event-stream"
_EXPECTED_TOOLS = {
    "register_agent",
    "get_agent",
    "optimize",
    "get_market",
    "get_leaderboard",
    "ping_backend",
}


@pytest.fixture
def fake_email() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def client(fake_email):
    app = create_app()
    app.dependency_overrides[get_email_sender] = lambda: fake_email
    with TestClient(app) as c:
        yield c


def _parse(text: str) -> dict:
    """MCP streamable-http replies are either application/json or an SSE frame."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"unparseable MCP response: {text[:200]!r}")


def _rpc(client: TestClient, payload: dict, *, key: str | None = None, sid: str | None = None):
    headers = {"Accept": _MCP_ACCEPT, "Content-Type": "application/json"}
    if sid:
        headers["mcp-session-id"] = sid
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.post("/mcp", json=payload, headers=headers)


def _handshake(client: TestClient) -> str:
    """initialize → notifications/initialized; returns the session id."""
    init = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}},
    })
    # The transport handshake must NOT be blocked by APIKeyMiddleware (no key sent).
    assert init.status_code == 200, init.text
    sid = init.headers.get("mcp-session-id")
    _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid)
    return sid


def _register(client: TestClient, fake_email: FakeEmailSender, name: str = "Neo") -> str:
    resp = client.post(
        "/agents",
        json={"name": name, "email": f"{name.lower()}@example.com",
              "sliders": _SLIDERS, "assets": _BASKET},
    )
    assert resp.status_code == 201, resp.text
    return fake_email.sent[-1]["api_key"]


def test_openapi_exposes_exactly_the_mcp_operation_ids(client):
    ops = {
        o["operationId"]
        for p in client.app.openapi()["paths"].values()
        for o in p.values()
        if isinstance(o, dict) and "operationId" in o
    }
    assert _EXPECTED_TOOLS <= ops
    assert set(_MCP_OPERATIONS) == _EXPECTED_TOOLS


def test_healthz_is_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_mcp_lists_exactly_the_six_tools(client):
    sid = _handshake(client)
    listed = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid)
    tools = {t["name"] for t in _parse(listed.text)["result"]["tools"]}
    assert tools == _EXPECTED_TOOLS


def test_optimize_tool_exposes_sliders_and_assets(client):
    """The optimize tool must surface its body params, or MCP clients can't retune.

    Regression guard: an `OptimizeRequest | None` body makes OpenAPI emit a
    nullable `anyOf`, which fastapi-mcp flattens to an empty inputSchema — silently
    dropping `assets`/`sliders` and making a basket retune impossible over MCP.
    """
    sid = _handshake(client)
    listed = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid)
    tools = {t["name"]: t for t in _parse(listed.text)["result"]["tools"]}
    schema = tools["optimize"]["inputSchema"]
    assert {"sliders", "assets"} <= set(schema.get("properties", {})), schema
    # Body stays optional so a no-arg first-run optimize still works.
    assert schema.get("required", []) == []


def test_retune_via_mcp_changes_the_basket(client, fake_email):
    """A retune passed through the MCP optimize tool replaces the agent's basket."""
    key = _register(client, fake_email, name="Trinity")  # seeded basket: BTC, ETH, SOL
    sid = _handshake(client)

    retune = _rpc(client, {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "optimize", "arguments": {"assets": ["BTC", "ETH", "XRP"]}},
    }, key=key, sid=sid)
    out = _parse(retune.text)["result"]
    assert not out.get("isError"), out

    after = _rpc(client, {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "get_agent", "arguments": {}},
    }, key=key, sid=sid)
    basket = _parse(after.text)["result"]["content"][0]["text"]
    assert "BTC" in basket and "ETH" in basket and "XRP" in basket
    assert "SOL" not in basket, "dropped ticker must not survive the retune"


def test_per_agent_tool_requires_key_through_mcp(client, fake_email):
    """get_agent must 401 without a key and succeed with the forwarded key."""
    key = _register(client, fake_email)
    sid = _handshake(client)

    no_key = _rpc(client, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_agent", "arguments": {}},
    }, sid=sid)
    body = _parse(no_key.text)
    assert body["result"]["isError"], "get_agent must fail without a key"
    assert "api key" in json.dumps(body).lower()

    with_key = _rpc(client, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "get_agent", "arguments": {}},
    }, key=key, sid=sid)
    ok = _parse(with_key.text)["result"]
    assert not ok.get("isError"), ok
    assert "Neo" in ok["content"][0]["text"]
