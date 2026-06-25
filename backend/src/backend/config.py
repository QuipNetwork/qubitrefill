"""Static configuration constants.

Single source of truth for all magic numbers — bankroll, slider ranges,
estimation windows, QUBO hyperparameters, solver deadlines.
"""

from __future__ import annotations

import os

# -----------------------------------------------------------------------------
# Bankroll and basket
# -----------------------------------------------------------------------------

BANKROLL_USD: float = 10_000.0
# Smallest basket the strategy meaningfully optimizes over; the kiosk's Select
# button should mirror this gate.
MIN_BASKET_SIZE: int = 3

# -----------------------------------------------------------------------------
# Estimation windows (hours)
# -----------------------------------------------------------------------------

# Risk lookback T feeding Σ — fixed, not user-facing. 30 days of hourly bars;
# can extend up to 2160h (90d, the assets-api retention cap).
SIGMA_WINDOW_HOURS: int = 720
# Return lookback τ feeding μ — fixed, not user-facing. 7 days of hourly bars.
MU_WINDOW_HOURS: int = 168

# -----------------------------------------------------------------------------
# Slider → param ranges (single source of truth, used by slider_map)
# -----------------------------------------------------------------------------

GAMMA_RANGE: tuple[float, float] = (0.5, 20.0)  # log-scaled

# Per-asset cap is RELATIVE to the basket (mirrors mvp/src/utils/strategy.ts::
# maxPositionCapPct): slider sweeps from equal weight (1/n — maximally
# diversified) up to W_MAX_CEILING in a single asset. A 1-asset basket is
# always 100%.
W_MAX_CEILING: float = 0.5

# Participation floor, not user-facing: w_min = MIN_POSITION_FRACTION/n, so
# every basket asset the player picked shows up in the portfolio. n·w_min ≤ 1
# always holds since MIN_POSITION_FRACTION ≤ 1.
MIN_POSITION_FRACTION: float = 0.25

# Rebalance-frequency slider tiers → scheduled re-optimization cadence (hours).
# Hourly is the hard cap: quantum jobs cost real money (mirrors strategy.ts).
REBALANCE_TIERS_HOURS: tuple[int, ...] = (24, 8, 4, 2, 1)

# -----------------------------------------------------------------------------
# QUBO encoding hyperparameters
# -----------------------------------------------------------------------------

# Bits per asset across [w_min, w_max]. Large baskets drop to 3 bits: with b=4
# the budget couplings span a 64× range, and past ~60 variables the QPU's
# auto-scaling pushes the low-bit couplings below coupler precision — the chip
# can't feel them and Σw drifts. Fewer bits = coarser grid (normalization
# absorbs it) but a landscape the hardware can actually represent.
BIT_PRECISION: int = 4
BIT_PRECISION_LARGE: int = 3
QUBO_PREFERRED_MAX_VARS: int = 60  # use BIT_PRECISION while n·b stays within this
# λ_sum = mult × max objective coefficient. Large enough that SA's worst-case
# budget violation stays under half a grid step (≈ 1/(2·Δ·u_top) ≈ 460 for the
# full-universe basket), small enough that the objective isn't crushed below QPU
# precision after coupler auto-scaling.
PENALTY_MULT_BUDGET: float = 500.0

# A QUBO solver's weights live on a discrete grid (and the QPU adds analog
# noise), so Σw=1 is only approximate. Decoded sums within this tolerance of 1
# are normalized onto the simplex before the feasibility gate; the box check
# stays as the backstop (a >10% rescale pushes weights past w_min/w_max + ε,
# so genuinely bad reads still fail).
QUBO_NORMALIZE_TOL: float = 0.10

# -----------------------------------------------------------------------------
# Feasibility tolerances (V0 quality bar)
# -----------------------------------------------------------------------------

EPS_BUDGET: float = 1e-3  # |Σwᵢ − 1| < EPS_BUDGET
EPS_BOX: float = 1e-3  # wᵢ ≤ w_max + EPS_BOX

# -----------------------------------------------------------------------------
# Race field
# -----------------------------------------------------------------------------

# Gurobi races locally during development but is NOT deployed to production
# (licensing) — there it remains the offline oracle only, and the live race is
# SA (CPU) vs D-Wave (QPU). GUROBI_IN_RACE=0 to preview the production field.
GUROBI_IN_RACE: bool = os.environ.get("GUROBI_IN_RACE", "1").lower() not in ("0", "false")

# -----------------------------------------------------------------------------
# D-Wave (joins the race only when DWAVE_API_TOKEN is set)
# -----------------------------------------------------------------------------

# All three knobs are env-overridable for tuning sweeps, e.g.
#   DWAVE_CHAIN_STRENGTH_PREFACTOR=4 qtw verify-dwave
DWAVE_NUM_READS: int = int(os.environ.get("DWAVE_NUM_READS", 500))  # parity with SA
DWAVE_ANNEAL_TIME_US: int = int(os.environ.get("DWAVE_ANNEAL_TIME_US", 100))
# Chain strength = uniform torque compensation × this prefactor. Raise if
# verify-dwave reports chain breaks above ~5% (long chains need stronger bonds).
# ×3 from hardware sweeps: ×2 leaves ~17% chain breaks at 75+ vars; ×3 gives
# 0.4% there with margin to spare at small baskets.
DWAVE_CHAIN_STRENGTH_PREFACTOR: float = float(os.environ.get("DWAVE_CHAIN_STRENGTH_PREFACTOR", 3.0))

# -----------------------------------------------------------------------------
# Solver deadlines (seconds)
# -----------------------------------------------------------------------------

SOLVER_DEADLINE_S: float = 2.0  # per-solver wall-clock budget
RACE_OVERALL_DEADLINE_S: float = 3.0  # outer cap on the parallel race

# -----------------------------------------------------------------------------
# MTM tick cadence (seconds)
# -----------------------------------------------------------------------------

MTM_TICK_S: float = 3.0

# -----------------------------------------------------------------------------
# Market data source
# -----------------------------------------------------------------------------

# "assets-api" → the assets-api price-indexing service (REST, SQLite-backed);
# "synthetic" → deterministic stand-in (no network — tests and offline demos).
MARKET_DATA_SOURCE: str = os.environ.get("MARKET_DATA_SOURCE", "assets-api")
ASSETS_API_BASE_URL: str = os.environ.get("ASSETS_API_BASE_URL", "http://127.0.0.1:8080")
ASSETS_API_TIMEOUT_S: float = 10.0
SYNTHETIC_SEED: int = 20260625  # booth day — deterministic synthetic history

# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------

# Origins allowed by CORS. The deployed MVP plus local dev.
CORS_ORIGINS: tuple[str, ...] = (
    "https://qtw-tradinggame.netlify.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
QR_BASE_URL: str = "https://qtw-tradinggame.netlify.app"  # /p/{agentId} deep link base

# -----------------------------------------------------------------------------
# Database (PostgreSQL, async SQLAlchemy + asyncpg)
# -----------------------------------------------------------------------------

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://qtw:qtw@127.0.0.1:5432/qtw"
)

# -----------------------------------------------------------------------------
# Email (Proton SMTP) — registration delivers the agent's API key out-of-band
# -----------------------------------------------------------------------------

# Proton's SMTP submission endpoint (smtp.protonmail.ch:587, STARTTLS). The
# username is the Proton address and the password is an SMTP token generated in
# the Proton account settings. When SMTP_PASSWORD is unset the sender falls back
# to a console logger (dev/offline).
SMTP_HOST: str = os.environ.get("SMTP_HOST", "smtp.protonmail.ch")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
SMTP_STARTTLS: bool = os.environ.get("SMTP_STARTTLS", "true").lower() != "false"
EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "Qubitrefill <onboarding@protonmail.com>")
