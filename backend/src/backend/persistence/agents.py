"""Agent repository — async, session-scoped (PostgreSQL via SQLAlchemy).

Replaces the old in-memory ``AgentStore`` singleton. Each request gets an
``AgentRepo`` bound to its ``AsyncSession`` (via the ``get_session`` dependency);
the MTM scheduler opens its own session per tick. Holdings are token **units**
(not USD) so mark-to-market is just units × spot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas import AgentConfig, AgentUpdate, SliderValues
from ..db.models import Agent


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _derive_handle(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def agent_to_config(agent: Agent) -> AgentConfig:
    """ORM Agent → public-facing AgentConfig (used by GET /agents/me)."""
    return AgentConfig(
        name=agent.name,
        handle=agent.handle,
        email=agent.email,
        reach_out=agent.reach_out,
        updates_opt_in=agent.updates_opt_in,
        sliders=SliderValues(**agent.sliders),
        assets=agent.assets,
    )


class AgentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        agent_id: str,
        name: str,
        email: str,
        handle: str | None,
        reach_out: list[str] | None,
        updates_opt_in: bool | None,
        sliders: SliderValues,
        assets: list[str] | None,
        bankroll: float,
    ) -> Agent:
        agent = Agent(
            id=agent_id,
            name=name,
            email=email,
            handle=handle or _derive_handle(name),
            reach_out=reach_out,
            updates_opt_in=updates_opt_in,
            sliders=sliders.model_dump(),
            assets=assets,
            bankroll=bankroll,
            holdings_units={},
            total=bankroll,
            pl_usd=0.0,
            pl_pct=0.0,
            jobs_solved=0,
            primary_provider="CPU",
            created_at=_now_iso(),
        )
        self.session.add(agent)
        await self.session.flush()
        return agent

    async def get(self, agent_id: str) -> Agent | None:
        return await self.session.get(Agent, agent_id)

    async def get_by_email(self, email: str) -> Agent | None:
        result = await self.session.execute(select(Agent).where(Agent.email == email))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Agent | None:
        result = await self.session.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def all(self) -> list[Agent]:
        result = await self.session.execute(select(Agent))
        return list(result.scalars())

    async def update_sliders(self, agent: Agent, sliders: SliderValues) -> None:
        agent.sliders = sliders.model_dump()
        await self.session.flush()

    async def update_assets(self, agent: Agent, assets: list[str]) -> None:
        agent.assets = list(assets)
        await self.session.flush()

    async def apply_solve(
        self,
        agent: Agent,
        holdings_units: dict[str, float],
        total: float,
        provider_type: str,
    ) -> None:
        """Record the outcome of a solve: new holdings, valuation, provider, count."""
        agent.holdings_units = dict(holdings_units)
        agent.total = total
        agent.pl_usd = total - agent.bankroll
        agent.pl_pct = (agent.pl_usd / agent.bankroll * 100.0) if agent.bankroll else 0.0
        agent.jobs_solved += 1
        agent.primary_provider = provider_type
        await self.session.flush()

    async def set_valuation(self, agent: Agent, update: AgentUpdate) -> None:
        """Update the mark-to-market valuation from the MTM loop."""
        agent.total = update.total
        agent.pl_usd = update.pl_usd
        agent.pl_pct = update.pl_pct
        await self.session.flush()
