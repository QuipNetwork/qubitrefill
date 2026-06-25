"""Pydantic schemas mirroring mvp/src/api/types.ts.

Field aliases preserve the camelCase JSON wire format expected by the frontend
while keeping Python attributes snake_case.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..financial.basket import AssetClass


class SliderValues(BaseModel):
    """The three strategy sliders, all 0–100 from the UI.

    rebalance_frequency — how often the agent dispatches a scheduled
        re-optimization job (Daily → Hourly; hourly is the hard cap).
    risk_preference — risk-aversion term γ in the objective.
    max_position_size — per-asset weight cap, relative to the basket
        (equal weight 1/n → ~50% in a single asset).
    """

    rebalance_frequency: float = Field(ge=0, le=100, alias="rebalanceFrequency")
    risk_preference: float = Field(ge=0, le=100, alias="riskPreference")
    max_position_size: float = Field(ge=0, le=100, alias="maxPositionSize")

    model_config = ConfigDict(populate_by_name=True)


class AgentConfig(BaseModel):
    name: str
    handle: str | None = None  # display handle, auto-derived from the name
    email: str | None = None  # required at sign-up; optional for seeded demo agents
    reach_out: list[str] | None = Field(default=None, alias="reachOut")
    updates_opt_in: bool | None = Field(default=None, alias="updatesOptIn")
    sliders: SliderValues
    # The player's selected basket — a subset of the 28-asset universe.
    # None/empty falls back to the full universe; re-selectable on retune.
    assets: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)


ProviderType = Literal["QPU", "CPU"]


class PortfolioEntry(BaseModel):
    ticker: str
    pct: float  # 0–100, w_i × 100
    usd: float  # holdings value in USD


class RoutingResult(BaseModel):
    """Returned from POST /agents/{id}/optimize."""

    provider: str  # 'dwave' | 'sa' | 'gurobi'
    provider_type: ProviderType = Field(alias="providerType")
    solve_time: float = Field(alias="solveTime")  # seconds (winning solver)
    vs_classical: float = Field(alias="vsClassical")  # ×-faster than the classical runner-up
    portfolio: list[PortfolioEntry]

    # Extensions beyond the mock contract
    kind: Literal["first", "retune"] | None = None
    job_id: str | None = Field(default=None, alias="jobId")
    solved_at: str | None = Field(default=None, alias="solvedAt")  # ISO-8601 UTC

    model_config = ConfigDict(populate_by_name=True)


class LeaderboardEntry(BaseModel):
    # No agent_id: the id is the secret API key, so the public board is keyed
    # by name/handle only.
    rank: int
    name: str
    handle: str | None = None
    total: float  # bankroll + plUSD
    pl_usd: float = Field(alias="plUSD")
    pl_pct: float = Field(alias="plPct")
    jobs_solved: int = Field(alias="jobsSolved")
    primary_provider: ProviderType = Field(alias="primaryProvider")

    model_config = ConfigDict(populate_by_name=True)


# -----------------------------------------------------------------------------
# Request payloads
# -----------------------------------------------------------------------------


class RegistrationRequest(BaseModel):
    """POST /agents — register a new agent. Email + name are required and unique."""

    name: str
    handle: str | None = None
    email: EmailStr
    reach_out: list[str] | None = Field(default=None, alias="reachOut")
    updates_opt_in: bool | None = Field(default=None, alias="updatesOptIn")
    sliders: SliderValues
    assets: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)


class RegistrationResponse(BaseModel):
    """The API key is emailed out-of-band — never returned in the response body."""

    message: str
    email: EmailStr
    bankroll: float

    model_config = ConfigDict(populate_by_name=True)


class OptimizeRequest(BaseModel):
    """POST /agents/{id}/optimize.

    Optional `sliders` and `assets` update + optimize in one atomic round-trip.
    A retune liquidates all holdings and reallocates over the new basket.
    """

    sliders: SliderValues | None = None
    assets: list[str] | None = None


# -----------------------------------------------------------------------------
# Market data
# -----------------------------------------------------------------------------


class MarketAsset(BaseModel):
    ticker: str
    name: str
    asset_class: AssetClass = Field(alias="assetClass")
    mu: float
    units: float
    usd: float

    model_config = ConfigDict(populate_by_name=True)


class MarketResult(BaseModel):
    # Token-derived endpoint — returns the caller's own holdings, so no id is echoed.
    assets: list[MarketAsset]

    model_config = ConfigDict(populate_by_name=True)
