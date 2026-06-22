"""Pure portfolio solve — no persistence, safe to run in a worker thread.

``solve_portfolio`` takes a plain snapshot (tickers, sliders, current holdings,
bankroll) and returns the allocation outcome. All DB I/O lives in the async API
handler (``routes.optimize``) and the MTM scheduler — this function only touches
the market data source and the solvers, so it can be dispatched via
``asyncio.to_thread`` without an event loop or DB session.

basket → slider_map → estimates(μ, Σ) → solver race → allocate. Every solve is a
fresh allocation: a retune liquidates all holdings at spot and reallocates the
full value over the (possibly re-selected) basket.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..api.schemas import PortfolioEntry, SliderValues
from ..financial.estimators.covariance import covariance
from ..financial.estimators.expected_return import expected_return
from ..financial.prices.base import MarketDataSource
from ..financial.prices.source import get_source
from ..financial.qubo_decoder import weights_to_portfolio
from ..financial.slider_map import map_sliders
from ..financial.types import PortfolioProblem
from ..solvers.router import race
from ..solvers.types import ProviderProvenance, Solution

_PROVIDER_LABELS = {
    "gurobi": "Gurobi",
    "sa": "Simulated Annealing",
    "dwave": "D-Wave Advantage",
}


@dataclass(frozen=True)
class SolveInput:
    """Plain snapshot of an agent, enough to run one solve. No DB handles."""

    tickers: list[str]
    sliders: SliderValues
    holdings_units: dict[str, float]
    bankroll: float


@dataclass(frozen=True)
class SolveOutcome:
    portfolio: list[PortfolioEntry]
    holdings_units: dict[str, float]
    total: float  # portfolio value reallocated this solve
    is_first: bool
    provenance: ProviderProvenance
    provider_label: str
    provider_role: str
    solve_time_s: float
    vs_classical: float
    # Full race field — used by the CLI's race printout.
    solver_results: list[Solution]
    winner_provider: str


def solve_portfolio(
    inp: SolveInput,
    *,
    market: MarketDataSource | None = None,
    deadline_s: float | None = None,
) -> SolveOutcome:
    """Run one solve. Raises SolverFailed when the race produces nothing feasible."""
    market = market if market is not None else get_source()
    tickers = inp.tickers
    params = map_sliders(inp.sliders, len(tickers))

    # Σ over the fixed 720h window, μ over the fixed lookback within it.
    returns = market.hourly_returns(tickers, config.SIGMA_WINDOW_HOURS)
    problem = PortfolioProblem(
        mu=expected_return(returns, config.MU_WINDOW_HOURS),
        Sigma=covariance(returns),
        gamma=params.gamma,
        w_max=params.w_max,
        w_min=params.w_min,
        asset_tickers=tickers,
    )

    race_result = race(problem, deadline_s=deadline_s)
    winner = race_result.winner

    # Liquidate everything at spot, reallocate the full value by the winner's
    # weights. The spot snapshot covers old and new holdings alike.
    spot = market.spot_prices(list({*tickers, *inp.holdings_units}))
    is_first = not inp.holdings_units
    portfolio_value = (
        inp.bankroll
        if is_first
        else sum(units * spot[t] for t, units in inp.holdings_units.items())
    )
    holdings_units = {
        t: w * portfolio_value / spot[t]
        for t, w in zip(tickers, winner.weights, strict=True)
        if w > 0.0
    }

    provenance = ProviderProvenance(
        provider=winner.provider,
        provider_role=winner.provider_role,
        q_hash=race_result.q_hash,
        deadline_s=deadline_s if deadline_s is not None else config.RACE_OVERALL_DEADLINE_S,
        solve_time_s=winner.solve_time_s,
        feasible=winner.feasible,
    )

    return SolveOutcome(
        portfolio=weights_to_portfolio(winner.weights, tickers, portfolio_value),
        holdings_units=holdings_units,
        total=portfolio_value,
        is_first=is_first,
        provenance=provenance,
        provider_label=_PROVIDER_LABELS.get(winner.provider, winner.provider),
        provider_role=winner.provider_role,
        solve_time_s=winner.solve_time_s,
        vs_classical=race_result.vs_classical,
        solver_results=race_result.all_results,
        winner_provider=winner.provider,
    )
