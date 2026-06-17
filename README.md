# qupick — usage

Pay for a Bitrefill product (gift card, top-up, eSIM) with the **worst-performing crypto** in your
portfolio — the asset with the lowest expected return μ — then retune the portfolio without it.

The agent logic lives in [`SKILL.md`](./SKILL.md); this README is the operator's quick-start.
Purchase mechanics are delegated to the sibling [`bitrefill`](../bitrefill/SKILL.md) skill.

## Prerequisites

- The **portfolio backend** running locally on `http://127.0.0.1:8000` (see below).
- The **Bitrefill MCP** connected (`https://api.bitrefill.com/mcp`, OAuth or API key), or the
  Bitrefill REST API key — used for product search and invoice creation.
- A funded Lightning / crypto wallet to actually pay the invoice. Bitrefill account `balance` is not
  required (invoices are paid from your own wallet).

## Install the skill

Claude Code discovers skills under `.claude/skills/`. Because `qupick` links the bitrefill
skill via the relative path `../bitrefill/SKILL.md`, install **both** as siblings:

```bash
mkdir -p .claude/skills
cp -R skills/qupick .claude/skills/
cp -R skills/bitrefill     .claude/skills/
```

`.claude/` is gitignored — this is a local install, not committed.

## Run the backend

```bash
cd backend
MARKET_DATA_SOURCE=synthetic uv run uvicorn backend.api.app:app --workers 1 --port 8000
```

Wait until `GET http://127.0.0.1:8000/leaderboard` responds (it returns `[]` on a fresh start).

> First-solve cold start: the very first `optimize` call can return
> `503 no feasible solution ... before deadline` while the D-Wave/Gurobi libs warm up. Just retry
> once — subsequent solves are sub-10ms.

## Use it

Ask the agent in natural language, e.g.:

> "Buy a $20 Steam gift card and pay with my worst-performing crypto."

The skill then runs the 7-step flow from `SKILL.md`:

1. **Available currencies** — static map of Bitrefill-payable crypto (BTC, ETH, BNB, SOL, XRP, USDT,
   USDC, DOGE, ZEC, ALGO, FIL).
2. **Seed agent (REST)** — `POST /agents` over those currencies, then `POST /agents/{id}/optimize`.
   (Reuses an existing agent via `GET /agents/{id}` if you already have one.)
3. **Pick product (MCP)** — `search-products` → `get-product-details` for price + accepted
   `payment_methods`.
4. **Market (REST)** — `GET /agents/{id}/market` for per-asset μ, units, USD value.
5. **Choose the loser** — among held crypto that the product accepts, prefer those whose holdings
   cover the price (×1.02 buffer) and pick `min(μ)`; otherwise fall back to the outright worst performer
   with a shortfall warning.
6. **Confirm + buy (MCP)** — the agent **stops for your explicit approval**, then `buy-products`
   returns a payment link / Lightning invoice. Pay it; the agent polls to `complete` and surfaces
   the redemption code.
7. **Retune (REST)** — `POST /agents/{id}/optimize` with the basket minus the spent ticker.

### Example (verified run)

```
Seed → optimize → /market ranked by μ (worst first):
  BTC   crypto   μ=-0.002567   $230.67   ← worst performer
  SOL   crypto   μ=-0.000341   $225.07
  USDC  crypto   μ=+0.000046   $2272.70
  ETH   crypto   μ=+0.000129   $229.61

Product: Steam USD $20 ($21.60) · accepts bitcoin/ethereum/solana/usdc_base
Chosen:  BTC (worst performer, holdings cover price) → pay via Lightning
Invoice: 33,481 sats, status unpaid → pay → complete → redemption code
Retune:  drop BTC, re-optimize over the remaining 10 currencies
```

## Safeguards (real money)

- The agent **never buys without explicit approval** — it always pauses at step 6.
- Codes deliver instantly and are **non-refundable**; treat redemption codes as cash and redeem ASAP.
- Use a dedicated, low-balance wallet. Full policy: [`../bitrefill/references/safeguards.md`](../bitrefill/references/safeguards.md).
- The step-7 retune is irreversible — the spent asset leaves the basket until you re-add it.
```
```