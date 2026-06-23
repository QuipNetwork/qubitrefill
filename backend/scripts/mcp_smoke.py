"""End-to-end smoke test against a RUNNING qupick server (REST + MCP).

Unlike tests/test_mcp.py (in-process TestClient), this drives the real HTTP
transport at a live URL — use it to verify a docker-compose or uvicorn instance.

    uv run python scripts/mcp_smoke.py            # default http://127.0.0.1:8000
    BASE=http://127.0.0.1:8000 uv run python scripts/mcp_smoke.py

It registers a throwaway agent, reads the key from the server's response is NOT
possible (the key is emailed), so pass it explicitly once you have it:

    QUPICK_API_KEY=<key> uv run python scripts/mcp_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8000").rstrip("/")
MCP = f"{BASE}/mcp"
ACCEPT = "application/json, text/event-stream"
KEY = os.environ.get("QUPICK_API_KEY")


def parse(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    raise SystemExit(f"unparseable MCP reply: {text[:200]!r}")


def rpc(client: httpx.Client, payload: dict, *, key=None, sid=None) -> httpx.Response:
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if sid:
        headers["mcp-session-id"] = sid
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.post(MCP, json=payload, headers=headers)


def main() -> None:
    with httpx.Client(timeout=15.0) as client:
        # 1. REST liveness
        hz = client.get(f"{BASE}/healthz")
        print("healthz:", hz.status_code, hz.json())

        # 2. MCP handshake (no key — must not be 401'd)
        init = rpc(client, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "smoke", "version": "0"}},
        })
        assert init.status_code == 200, f"handshake blocked: {init.status_code} {init.text}"
        sid = init.headers.get("mcp-session-id")
        rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid)
        print("initialize OK, session:", (sid or "")[:8])

        # 3. tools/list
        tools = {t["name"] for t in parse(rpc(client, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid=sid).text)["result"]["tools"]}
        print("tools:", sorted(tools))
        assert tools == {"register_agent", "get_agent", "optimize",
                         "get_market", "get_leaderboard", "ping_backend"}, tools

        # 4. get_agent without a key → must error
        nk = parse(rpc(client, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_agent", "arguments": {}}}, sid=sid).text)
        assert nk["result"]["isError"], "SECURITY: get_agent succeeded without a key!"
        print("get_agent (no key): correctly refused")

        # 5. register a throwaway agent (public tool) — key is emailed/logged
        name = f"smoke-{uuid.uuid4().hex[:6]}"
        reg = parse(rpc(client, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "register_agent", "arguments": {
                "name": name, "email": f"{name}@example.com",
                "sliders": {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50},
                "assets": ["BTC", "ETH", "SOL"]}}}, sid=sid).text)
        print("register:", reg["result"]["content"][0]["text"][:90], "...")
        print("  -> retrieve this agent's key from the backend logs "
              "([email:console] ...) or email")

        # 6. authed call, if a key was supplied
        if KEY:
            wk = parse(rpc(client, {
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "get_agent", "arguments": {}}}, key=KEY, sid=sid).text)
            assert not wk["result"].get("isError"), wk
            print("get_agent (with QUPICK_API_KEY): OK ->",
                  wk["result"]["content"][0]["text"][:60], "...")
        else:
            print("get_agent (with key): SKIPPED — set QUPICK_API_KEY to run it")

        print("\nSMOKE OK ✓")


if __name__ == "__main__":
    try:
        main()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        sys.exit(f"cannot reach {BASE} — is the server up? (docker compose up -d --build)")
