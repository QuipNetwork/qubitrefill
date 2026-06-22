"""HTTP routes.

`POST /agents` is the public **registration** endpoint (unique email + name; the
agent uuid is the API key, emailed out-of-band). Every other endpoint is gated by
the Bearer middleware and operates on the **authenticated** agent
(`request.state.agent_id`) — no agent id appears in any URL.

The heavy optimize pipeline runs its pure-compute core in a worker thread
(`solve_portfolio`); all DB I/O stays here on the event loop.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..db.engine import get_session
from ..email.sender import EmailSender, get_email_sender
from ..events import feeds
from ..events.bus import get_bus
from ..financial.basket import get_asset, validate_basket
from ..financial.estimators.expected_return import expected_return
from ..financial.prices.source import get_source
from ..orchestration.job import SolveInput, solve_portfolio
from ..persistence.agents import AgentRepo, agent_to_config
from ..persistence.jobs import JobRepo
from ..persistence.leaderboard import build_leaderboard
from ..solvers.types import SolverFailed
from .schemas import (
    AgentConfig,
    AgentUpdate,
    LeaderboardEntry,
    MarketAsset,
    MarketResult,
    OptimizeRequest,
    RegistrationRequest,
    RegistrationResponse,
    RoutingResult,
    SliderValues,
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Registration (public)
# -----------------------------------------------------------------------------
@router.post("/agents", response_model=RegistrationResponse, status_code=201)
async def register(
    body: RegistrationRequest,
    session: AsyncSession = Depends(get_session),
    email: EmailSender = Depends(get_email_sender),
) -> RegistrationResponse:
    try:
        assets = validate_basket(body.assets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = AgentRepo(session)
    if await repo.get_by_email(body.email) is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    if await repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail="name already taken")

    api_key = uuid4().hex
    agent = await repo.create(
        agent_id=api_key,
        name=body.name,
        email=body.email,
        handle=body.handle,
        reach_out=body.reach_out,
        updates_opt_in=body.updates_opt_in,
        sliders=body.sliders,
        assets=assets,
        bankroll=config.BANKROLL_USD,
    )
    try:
        await session.flush()
    except IntegrityError as exc:  # race on the unique constraints
        raise HTTPException(status_code=409, detail="email or name already taken") from exc

    # Email-only delivery: if the send fails, roll back so the user can retry
    # (a lost email would otherwise mean a permanently lost, uncreated key).
    try:
        await email.send_api_key(agent.email, agent.name, api_key)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=502, detail="failed to send the API-key email; please retry"
        ) from exc

    return RegistrationResponse(
        message="Check your email for your API key.",
        email=agent.email,
        bankroll=agent.bankroll,
    )


# -----------------------------------------------------------------------------
# Authenticated per-agent endpoints (Bearer-gated; agent from request.state)
# -----------------------------------------------------------------------------
@router.get("/agents/me", response_model=AgentConfig)
async def get_me(request: Request, session: AsyncSession = Depends(get_session)) -> AgentConfig:
    agent = await AgentRepo(session).get(request.state.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent_to_config(agent)


@router.post("/agents/optimize", response_model=RoutingResult)
async def optimize(
    request: Request,
    body: OptimizeRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> RoutingResult:
    repo = AgentRepo(session)
    agent = await repo.get(request.state.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    sliders = body.sliders if body is not None else None
    assets = body.assets if body is not None else None
    if sliders is not None:
        await repo.update_sliders(agent, sliders)
    if assets is not None:
        try:
            await repo.update_assets(agent, validate_basket(assets))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    snapshot = SolveInput(
        tickers=validate_basket(agent.assets),
        sliders=SliderValues(**agent.sliders),
        holdings_units=dict(agent.holdings_units or {}),
        bankroll=agent.bankroll,
    )
    try:
        outcome = await asyncio.to_thread(solve_portfolio, snapshot)
    except SolverFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await repo.apply_solve(agent, outcome.holdings_units, outcome.total, outcome.provider_role)
    job = await JobRepo(session).record(agent.id, outcome.provenance)

    # Publish on the agent's PUBLIC channel (handle/name — never the secret id).
    pl_usd = outcome.total - agent.bankroll
    pl_pct = (pl_usd / agent.bankroll * 100.0) if agent.bankroll else 0.0
    update = AgentUpdate(pl_usd=pl_usd, pl_pct=pl_pct, total=outcome.total)
    handle = agent.handle or agent.name
    bus = get_bus()
    bus.publish(feeds.agent_channel(handle), update.model_dump(by_alias=True))
    if outcome.is_first:
        bus.publish(feeds.TV_CHANNEL, {"type": "new-agent", "handle": handle, "name": agent.name})

    return RoutingResult(
        provider=outcome.provider_label,
        provider_type=outcome.provider_role,
        solve_time=outcome.solve_time_s,
        vs_classical=outcome.vs_classical,
        portfolio=outcome.portfolio,
        kind="first" if outcome.is_first else "retune",
        job_id=job.id,
        solved_at=job.solved_at,
    )


@router.get("/agents/market", response_model=MarketResult)
async def market(request: Request, session: AsyncSession = Depends(get_session)) -> MarketResult:
    agent = await AgentRepo(session).get(request.state.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return await asyncio.to_thread(_compute_market, dict(agent.holdings_units or {}), agent.assets)


def _compute_market(holdings_units: dict[str, float], assets: list[str] | None) -> MarketResult:
    tickers = list(holdings_units) or validate_basket(assets)
    source = get_source()
    returns = source.hourly_returns(tickers, config.SIGMA_WINDOW_HOURS)
    mu = expected_return(returns, config.MU_WINDOW_HOURS)
    spot = source.spot_prices(tickers)
    market_assets = []
    for i, t in enumerate(tickers):
        meta = get_asset(t)
        units = holdings_units.get(t, 0.0)
        market_assets.append(
            MarketAsset(
                ticker=t,
                name=meta.name,
                asset_class=meta.asset_class,
                mu=float(mu[i]),
                units=units,
                usd=units * spot[t],
            )
        )
    return MarketResult(assets=market_assets)


# -----------------------------------------------------------------------------
# Leaderboard (public)
# -----------------------------------------------------------------------------
@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(session: AsyncSession = Depends(get_session)) -> list[LeaderboardEntry]:
    return await build_leaderboard(session)
