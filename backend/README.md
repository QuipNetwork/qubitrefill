# QTW 2026 Trading Game — Backend

Python backend for the booth trading competition. Reads market data from the
**assets-api** service by default; a deterministic synthetic source
(`MARKET_DATA_SOURCE=synthetic`) runs the whole pipeline offline. See
`../docs/BACKEND_DAG.md` for the dataflow and `../TODO.md` for what's left.

## Setup

`uv` is the intended tool, but any venv works. Using the existing `.venv`:

```bash
cd backend
python3.12 -m venv .venv                 # first time only
.venv/bin/python -m pip install -e . pytest httpx
```

Gurobi (`gurobipy`) and SA (`dwave-neal`) install from PyPI with a free trial
license that comfortably fits this problem size.

## Run the automated tests

```bash
.venv/bin/python -m pytest -q             # 60 tests, ~2s
```

What each suite covers:

| File | What it checks |
|---|---|
| `test_slider_map.py` | 3 sliders → params; risk inversion; basket-relative caps; rebalance tiers; min basket |
| `test_qubo_encoder.py` / `test_qubo_roundtrip.py` | QUBO shape/symmetry; bit-grid round-trips; simplex normalization |
| `test_feasibility.py` | budget / box checks |
| `test_estimators.py` / `test_synthetic_market.py` | μ, Σ; deterministic history; moving spot |
| `test_solvers_synthetic.py` | Gurobi feasible; SA feasible & matches Gurobi; the race |
| `test_assets_api.py` | assets-api client: grid alignment, forward-fill, spot, errors |
| `test_pnl.py` | mark-to-market rollup |
| `test_persistence.py` / `test_job_pipeline.py` | stores + leaderboard; first-solve & retune over the basket |
| `test_api.py` | HTTP flow, 404s/422s, and a live WebSocket push |

## Test from the CLI (no server needed)

`qtw` (or `python -m backend.cli`) exercises the pipeline against the configured
market source — handy for quick checks and for sanity-testing data later.

```bash
.venv/bin/qtw market                                  # spot, hourly μ and vol per asset
.venv/bin/qtw optimize --risk 70 --assets BTC,ETH,IONQ   # full solve → portfolio
.venv/bin/qtw race --max-position 80                  # one solver race, all providers + timing
```

Slider flags (`--risk`, `--max-position`, `--rebalance`) take 0–100;
`--assets` is a comma-separated basket (min 3, defaults to all 25).

## Market data (assets-api by default)

The backend expects the assets-api service (gitlab.com/quip.network/assets-api)
on `http://127.0.0.1:8080` (override with `ASSETS_API_BASE_URL`):

```bash
docker run -p 8080:8080 -v assets-data:/data \
  -e ALPACA_KEY_ID=... -e ALPACA_SECRET=... \
  registry.gitlab.com/quip.network/assets-api:latest
# wait for the 90-day backfill, check http://127.0.0.1:8080/healthz
```

Stock market-hour gaps are forward-filled onto the hourly grid automatically.
No service running? `MARKET_DATA_SOURCE=synthetic` switches everything
(server, CLI, tests already pin it) to the deterministic offline source.

## Enable the D-Wave QPU

The QPU joins the solver race only when a Leap token is present:

```bash
export DWAVE_API_TOKEN=...   # from cloud.dwavesys.com
.venv/bin/qtw race           # field becomes gurobi, sa, dwave
```

Reported D-Wave solve time is QPU access time (the anneal itself), not network
round-trip. The provider uses the clique sampler (cached embeddings — no
per-solve embedding search) and picks the best *feasible* anneal read, not just
the lowest-energy one.

Tuning knobs are env vars: `DWAVE_NUM_READS` (500), `DWAVE_ANNEAL_TIME_US`
(100), `DWAVE_CHAIN_STRENGTH_PREFACTOR` (3). `qtw verify-dwave [--assets …]`
submits one QUBO to Leap and reports chip, embedding, chain breaks, timing, and
feasible-read stats. `GUROBI_IN_RACE=0` previews the production field — Gurobi
stays an offline oracle and the live race is SA vs the QPU.

## Run the server

```bash
.venv/bin/python -m uvicorn backend.api.app:app --reload --workers 1
```

Then open `http://127.0.0.1:8000/docs` for interactive Swagger UI — the easiest
way to click through every endpoint. The same process also serves the qupick MCP
server at `/mcp` (see the top-level `README.md`).

### Docker

The whole stack (Postgres + this backend) runs from the repo root:

```bash
docker compose up -d --build      # backend on http://127.0.0.1:8000
```

The image (`backend/Dockerfile`, multi-stage uv build, non-root) defaults to
`MARKET_DATA_SOURCE=synthetic` and `GUROBI_IN_RACE=0`, so it solves offline with
SA — no assets-api, Gurobi licence, or D-Wave token required.

## Test the API by hand

```bash
BASE=http://127.0.0.1:8000

# 1. Create an agent → returns agentId, qrUrl, bankroll
curl -s $BASE/agents -H 'content-type: application/json' -d '{
  "name":"Neo","email":"neo@example.com",
  "sliders":{"rebalanceFrequency":50,"riskPreference":70,"maxPositionSize":50},
  "assets":["BTC","ETH","IONQ","QBTS"]
}'

# 2. Optimize (first solve). Use the agentId from step 1.
curl -s $BASE/agents/<AGENT_ID>/optimize -H 'content-type: application/json' -d '{}'
#    → RoutingResult: provider, providerType, solveTime, vsClassical, portfolio[], kind="first"

# 3. Retune — new sliders and/or a re-selected basket (liquidates + reallocates)
curl -s $BASE/agents/<AGENT_ID>/optimize -H 'content-type: application/json' \
  -d '{"sliders":{"rebalanceFrequency":50,"riskPreference":90,"maxPositionSize":80},"assets":["HON","GOOGL","IBM"]}'

# 4. Leaderboard
curl -s $BASE/leaderboard
```

Things worth checking in the response:
- `portfolio` holds **every basket asset** (min-position floor) and the `pct` values sum to 100.
- `kind` is `"first"` then `"retune"`; `jobId` and `solvedAt` are populated.

## Test the live WebSocket

The MTM loop pushes a valuation every ~3s, and each optimize also pushes one.
With the server running and an agent that has optimized at least once:

```bash
.venv/bin/python - <<'PY'
import asyncio, json, websockets
AGENT = "<AGENT_ID>"
async def main():
    async with websockets.connect(f"ws://127.0.0.1:8000/agents/{AGENT}") as ws:
        for _ in range(3):
            print(json.loads(await ws.recv()))   # {plUSD, plPct, total}
asyncio.run(main())
PY
```

`ws://127.0.0.1:8000/tv/events` streams booth events (e.g. `new-agent`).
