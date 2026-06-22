"""Job audit log — one record per solved Q hash (async, session-scoped).

Persists ``ProviderProvenance`` for every race so outcomes are auditable.
Replaces the old in-memory ``JobStore`` singleton.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Job
from ..solvers.types import ProviderProvenance


class JobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, agent_id: str, provenance: ProviderProvenance) -> Job:
        job = Job(
            id=uuid4().hex[:12],
            agent_id=agent_id,
            q_hash=provenance.q_hash,
            provider=provenance.provider,
            provider_role=provenance.provider_role,
            solve_time_s=provenance.solve_time_s,
            deadline_s=provenance.deadline_s,
            feasible=provenance.feasible,
            solved_at=datetime.now(UTC).isoformat(),
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: str) -> Job | None:
        return await self.session.get(Job, job_id)

    async def all(self) -> list[Job]:
        result = await self.session.execute(select(Job))
        return list(result.scalars())
