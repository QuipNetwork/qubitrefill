"""Pure solve pipeline on the synthetic market (first solve + retune).

These cover ``solve_portfolio`` (the DB-free compute core). Persistence and event
publishing are covered by test_api (the optimize endpoint) and test_persistence.
"""

from __future__ import annotations

import importlib.util

import pytest

from backend.api.schemas import SliderValues
from backend.financial.basket import TICKERS, validate_basket
from backend.orchestration.job import SolveInput, solve_portfolio

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("gurobipy") is None, reason="gurobipy not installed"
)

BASKET = ["BTC", "ETH", "SOL", "USDC", "IONQ", "QBTS", "RGTI"]


def _sliders(**overrides: int) -> SliderValues:
    base = {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50}
    base.update(overrides)
    return SliderValues(**base)


def _solve(tickers, holdings=None, **sliders):
    return solve_portfolio(
        SolveInput(
            tickers=tickers,
            sliders=_sliders(**sliders),
            holdings_units=holdings or {},
            bankroll=10_000.0,
        )
    )


def test_first_solve_allocates_bankroll_over_the_basket():
    out = _solve(BASKET)

    assert out.is_first
    # every basket asset is held (w_min floor), nothing outside the basket
    assert {e.ticker for e in out.portfolio} == set(BASKET)
    assert sum(e.pct for e in out.portfolio) == pytest.approx(100.0, abs=1e-6)
    assert sum(e.usd for e in out.portfolio) == pytest.approx(10_000.0, abs=1e-3)
    assert out.total == pytest.approx(10_000.0, abs=1e-3)
    assert set(out.holdings_units) == set(BASKET)


def test_retune_is_value_neutral_and_keeps_the_basket():
    first = _solve(BASKET)
    out = _solve(BASKET, holdings=first.holdings_units, riskPreference=90, maxPositionSize=80)

    assert not out.is_first
    assert {e.ticker for e in out.portfolio} == set(BASKET)
    # fixed-clock market → stationary spot → retune conserves value
    assert sum(e.usd for e in out.portfolio) == pytest.approx(10_000.0, abs=1e-2)


def test_retune_can_select_a_new_basket():
    """A retune liquidates everything and reallocates over the new basket."""
    first = _solve(BASKET)
    new_basket = ["HON", "GOOGL", "IBM", "QBTS"]
    out = _solve(new_basket, holdings=first.holdings_units)

    assert not out.is_first
    assert {e.ticker for e in out.portfolio} == set(new_basket)
    assert sum(e.usd for e in out.portfolio) == pytest.approx(10_000.0, abs=1e-2)


def test_no_basket_defaults_to_full_universe():
    out = _solve(validate_basket(None))
    assert {e.ticker for e in out.portfolio} == set(TICKERS)
