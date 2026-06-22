"""Background schedulers — the MTM loop.

`run_mtm_loop` is an async task started by the API lifespan. Every `MTM_TICK_S`
it opens a DB session, revalues every agent's holdings against a fresh spot
snapshot, persists the new valuation, and publishes an AgentUpdate per agent on
its **public** channel (keyed by handle/name, not the secret id). It never
trades — pure revaluation until the user retunes (CLAUDE.md §5.5). Publishing
happens on the event loop, so the bus queues stay loop-safe.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from .. import config
from ..db.engine import get_sessionmaker
from ..events import feeds
from ..events.bus import EventBus
from ..financial import basket
from ..financial.pnl import mark_to_market
from ..financial.prices.base import MarketDataSource
from ..financial.prices.source import get_source
from ..persistence.agents import AgentRepo


async def run_mtm_loop(
    bus: EventBus,
    stop: asyncio.Event,
    *,
    sessionmaker: async_sessionmaker | None = None,
    market: MarketDataSource | None = None,
    tick_s: float | None = None,
) -> None:
    """Revalue holdings and push AgentUpdates until ``stop`` is set."""
    market = market if market is not None else get_source()
    make_session = sessionmaker if sessionmaker is not None else get_sessionmaker()
    tick = tick_s if tick_s is not None else config.MTM_TICK_S
    tickers = list(basket.TICKERS)

    log = logging.getLogger(__name__)
    while not stop.is_set():
        try:
            spot = await asyncio.to_thread(market.spot_prices, tickers)
            async with make_session() as session:
                repo = AgentRepo(session)
                for agent in await repo.all():
                    if not agent.holdings_units:
                        continue
                    update = mark_to_market(agent.holdings_units, spot, agent.bankroll)
                    await repo.set_valuation(agent, update)
                    channel = feeds.agent_channel(agent.handle or agent.name)
                    bus.publish(channel, update.model_dump(by_alias=True))
                await session.commit()
        except Exception:
            # A flaky data source must not kill the loop; skip this tick.
            log.exception("MTM tick failed")
        # Sleep one tick, but wake immediately when asked to stop.
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick)
        except TimeoutError:
            pass
