"""WebSocket endpoints — the live push channels.

`WS /agents/{id}` streams per-agent AgentUpdates (subscribeAgent). `WS /tv/events`
streams booth-wide events (new-agent splash → TV State D, rank reshuffles). Both
just drain a bus subscription queue to the socket; the MTM scheduler and the
optimize handler are the publishers.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import feeds
from ..events.bus import get_bus

router = APIRouter()


async def _pump(websocket: WebSocket, channel: str) -> None:
    """Forward everything published on ``channel`` to the socket until it closes."""
    bus = get_bus()
    await websocket.accept()
    queue = bus.subscribe(channel)
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Send on a closed socket (client vanished) — drop and clean up.
        pass
    finally:
        bus.unsubscribe(channel, queue)


@router.websocket("/ws/agent/{handle}")
async def agent_updates(websocket: WebSocket, handle: str) -> None:
    # Public-by-design: subscribes by the agent's public handle/name, never the
    # secret API key. Read-only P&L stream.
    await _pump(websocket, feeds.agent_channel(handle))


@router.websocket("/tv/events")
async def tv_events(websocket: WebSocket) -> None:
    await _pump(websocket, feeds.TV_CHANNEL)
