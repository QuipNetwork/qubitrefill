"""Async DB repositories: agent lifecycle, valuation, leaderboard ranking."""

from __future__ import annotations

import pytest

from backend.api.schemas import AgentUpdate, SliderValues
from backend.db.engine import session_scope
from backend.persistence.agents import AgentRepo, agent_to_config
from backend.persistence.leaderboard import build_leaderboard

_SLIDERS = SliderValues(rebalanceFrequency=50, riskPreference=50, maxPositionSize=50)


async def _create(repo: AgentRepo, name: str):
    return await repo.create(
        agent_id=name.lower(),
        name=name,
        email=f"{name.lower()}@example.com",
        handle=name.lower(),
        reach_out=None,
        updates_opt_in=None,
        sliders=_SLIDERS,
        assets=["BTC", "ETH"],
        bankroll=10_000.0,
    )


async def test_create_and_get_roundtrip():
    async with session_scope() as session:
        repo = AgentRepo(session)
        record = await _create(repo, "Alice")
        await session.commit()

        got = await repo.get(record.id)
        assert got is not None
        assert got.total == 10_000.0
        config = agent_to_config(got)
        assert config.assets == ["BTC", "ETH"]
        assert config.email == "alice@example.com"
        assert await repo.get("missing") is None


async def test_unique_email_and_name():
    async with session_scope() as session:
        repo = AgentRepo(session)
        await _create(repo, "Bob")
        await session.commit()
        assert await repo.get_by_email("bob@example.com") is not None
        assert await repo.get_by_name("Bob") is not None


async def test_apply_solve_updates_holdings_and_count():
    async with session_scope() as session:
        repo = AgentRepo(session)
        record = await _create(repo, "Carol")
        await repo.apply_solve(record, {"BTC": 0.1}, total=11_000.0, provider_type="QPU")
        await session.commit()

        updated = await repo.get(record.id)
        assert updated.holdings_units == {"BTC": 0.1}
        assert updated.pl_usd == pytest.approx(1_000.0)
        assert updated.jobs_solved == 1
        assert updated.primary_provider == "QPU"


async def test_leaderboard_ranks_by_total_descending():
    async with session_scope() as session:
        repo = AgentRepo(session)
        low = await _create(repo, "Low")
        high = await _create(repo, "High")
        await repo.set_valuation(low, AgentUpdate(plUSD=-500.0, plPct=-5.0, total=9_500.0))
        await repo.set_valuation(high, AgentUpdate(plUSD=2_000.0, plPct=20.0, total=12_000.0))
        await session.commit()

        board = await build_leaderboard(session)
        assert [e.name for e in board] == ["High", "Low"]
        assert board[0].rank == 1
