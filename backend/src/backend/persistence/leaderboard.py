"""Leaderboard — agents ranked by total = bankroll + MTM P&L.

Derived from the agents table (no separate state). Ties break by name for a
stable ordering. The agent id is the secret API key, so it is **never** included
in a leaderboard entry — public ranking is keyed by name/handle only.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas import LeaderboardEntry
from ..db.models import Agent


async def build_leaderboard(session: AsyncSession) -> list[LeaderboardEntry]:
    result = await session.execute(select(Agent).order_by(Agent.total.desc(), Agent.name))
    records = list(result.scalars())
    return [
        LeaderboardEntry(
            rank=index + 1,
            name=record.name,
            handle=record.handle,
            total=record.total,
            pl_usd=record.pl_usd,
            pl_pct=record.pl_pct,
            jobs_solved=record.jobs_solved,
            primary_provider=record.primary_provider,
        )
        for index, record in enumerate(records)
    ]
