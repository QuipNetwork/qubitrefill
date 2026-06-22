from __future__ import annotations

import os

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from backend.db import engine as db_engine
from backend.db.engine import session_scope
from backend.db.models import Base
from backend.financial.prices.source import set_source
from backend.financial.prices.synthetic import SyntheticMarketSource
from backend.financial.types import PortfolioProblem

# A throwaway database (NOT the dev `qtw` DB). Create it once with:
#   docker compose exec db createdb -U qtw qtw_test
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://qtw:qtw@127.0.0.1:5432/qtw_test"
)


@pytest.fixture(autouse=True)
def no_dwave_token(monkeypatch):
    """Keep tests hermetic — never let a configured Leap token reach the QPU."""
    monkeypatch.delenv("DWAVE_API_TOKEN", raising=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_db():
    """Point the engine at the test DB (NullPool → loop-safe) and create tables."""
    db_engine.init_engine(TEST_DATABASE_URL, force=True, poolclass=NullPool)
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await db_engine.dispose_engine()


@pytest_asyncio.fixture(autouse=True)
async def isolated_state():
    """Truncate tables and pin a fixed-clock market so pipeline math is reproducible."""
    async with session_scope() as session:
        await session.execute(text("TRUNCATE jobs, agents RESTART IDENTITY CASCADE"))
        await session.commit()
    set_source(SyntheticMarketSource(clock=lambda: 1000.0))
    yield
    set_source(None)


@pytest.fixture
def synthetic_returns() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal((720, 4)) * 0.01


@pytest.fixture
def synthetic_problem_3assets() -> PortfolioProblem:
    mu = np.array([0.02, 0.01, 0.005])
    Sigma = np.array(
        [
            [0.04, 0.01, 0.005],
            [0.01, 0.03, 0.002],
            [0.005, 0.002, 0.01],
        ]
    )
    return PortfolioProblem(
        mu=mu,
        Sigma=Sigma,
        gamma=2.0,
        w_max=0.6,
        w_min=0.1,
        asset_tickers=["A", "B", "C"],
    )
