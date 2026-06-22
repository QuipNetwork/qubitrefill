"""SQLAlchemy ORM models.

The agent ``id`` is a uuid hex string that doubles as the **secret API key** — so
it never appears on a public surface (leaderboard, TV, WS). ``name`` and ``email``
are unique. JSON columns hold the small structured blobs (sliders, assets,
holdings) that were plain dicts in the old in-memory store.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid hex = API key
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    reach_out: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updates_opt_in: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sliders: Mapped[dict] = mapped_column(JSON)
    assets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    bankroll: Mapped[float] = mapped_column(Float)
    holdings_units: Mapped[dict] = mapped_column(JSON, default=dict)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    pl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    jobs_solved: Mapped[int] = mapped_column(Integer, default=0)
    primary_provider: Mapped[str] = mapped_column(String(16), default="CPU")
    created_at: Mapped[str] = mapped_column(String(64))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.id"), index=True)
    q_hash: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    provider_role: Mapped[str] = mapped_column(String(16))
    solve_time_s: Mapped[float] = mapped_column(Float)
    deadline_s: Mapped[float] = mapped_column(Float)
    feasible: Mapped[bool] = mapped_column(Boolean)
    solved_at: Mapped[str] = mapped_column(String(64))
