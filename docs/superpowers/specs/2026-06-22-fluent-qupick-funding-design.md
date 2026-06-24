# Fluent qupick: account-aware funding + one-stop flow

- **Date:** 2026-06-22
- **Status:** Design approved, pending spec review
- **Scope:** `skills/qupick/SKILL.md`, a new config file, the Claude Code permission allowlist, and docs. No backend (Python) changes.

## Problem

The `qupick` workflow (compute the worst-performing crypto, spend it on a Bitrefill
product, retune the portfolio) currently stops for the user at six points:

| # | Where | Type |
|---|-------|------|
| 1 | Seed agent | input (name/email) |
| 2 | Product denomination | input |
| 3 | Shortfall warning | approval |
| 4 | `buy-products` | hard approval gate |
| 5 | Pay invoice | manual action |
| 6 | `curl` / MCP calls | Claude Code permission prompts |

The goal is a fluent run that stops in **exactly one** place — the purchase
approval (#4) — while keeping that gate intact for real-money safety. The other
stops are removed by defaults (#1, #2), policy (#3), a configurable funding model
(#5), and a permission allowlist (#6).

## Decisions (from brainstorming)

1. **Spending autonomy:** keep the explicit approval gate before every purchase.
   Do not auto-buy; do not auto-pay from a hot wallet.
2. **Selection is never bypassed:** the worst performer (`min(μ)`) is computed on
   every run, regardless of how the bill is ultimately paid.
3. **Funding is account-aware and configurable:** the Bitrefill account holds
   multiple balances (confirmed by the user: USD, EUR, BTC). These balances, plus
   on-chain wallet payment, are funding sources drawn in a configurable priority.
4. **Retune only when the worst performer is actually sold.** Paying from a fiat account
   balance settles the bill without selling crypto, so it must not retune.
5. **Structure:** prose + a config file the skill reads at startup. No new code
   paths, matching the repo's "agent is instructions" architecture.
6. **Config location:** repo-local at `skills/qupick/config.json`, gitignored,
   with a committed `skills/qupick/config.example.json`.

## Funding model

### Selection (unchanged, always runs)

Worst performer = `min(μ)` over **spendable crypto candidates**: assets that are
held (`units > 0`), in `PAYMENT_METHOD_MAP`, and accepted by the product's
`payment_methods` list. This is the existing logic in `SKILL.md` step 5 and is
preserved verbatim. Selection does not depend on which funding source can cover
the price.

### Settlement waterfall (new)

Let `price = denomination_price_usd × (1 + funding.fee_buffer_pct/100)` and let the
chosen worst performer be e.g. `BTC`. Read live balances from `GET /accounts/balance`. Walk
`funding.priority` in order and take the **first source that covers the full
price** (no invoice splitting — Bitrefill invoices accept one payment method):

| Token | Source | Pays via | Sells it? | Retune? |
|-------|--------|----------|--------------|---------|
| `account_match` | Bitrefill account balance held in the worst-performing asset (account BTC) | `buy-products(payment_method:"balance", auto_pay:true)` | yes | yes |
| `onchain_match` | Wallet holdings of the worst-performing asset (on-chain BTC) | `buy-products(payment_method:"bitcoin", return_payment_link:true)` → pay link → poll | yes | yes |
| `account_fiat` | Bitrefill USD/EUR account balance | `buy-products(payment_method:"balance", auto_pay:true)` | no | no |

If no source covers the full price, apply `funding.on_shortfall`:
- `reject` (default): stop with a clear message naming the gap.
- `confirm`: present the shortfall and wait for explicit user approval to proceed
  on-chain with the worst-performing asset (the legacy fallback behavior).

**Default priority:** `["account_match", "onchain_match", "account_fiat"]` —
prioritizes genuinely selling the worst performer, uses instant account funds first, and
treats fiat as a gap-filler. Reorder or drop tokens to change behavior (e.g.
`["account_fiat", "account_match", "onchain_match"]` to spend fiat first, or drop
`account_fiat` to *only ever* sell crypto).

### Retune rule

Retune (drop the asset from the basket + re-optimize, `SKILL.md` step 7) fires
**only** when settlement used `account_match` or `onchain_match`. When settlement
used `account_fiat`, the portfolio is left untouched and the run reports "paid from
fiat balance; portfolio unchanged."

## Config schema

`skills/qupick/config.json` (gitignored). Committed `config.example.json` mirrors
it with placeholder values.

```json
{
  "defaults": {
    "name": "Konrad",
    "email": "konrad@postquant.xyz",
    "country": "US",
    "sliders": { "rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50 }
  },
  "funding": {
    "priority": ["account_match", "onchain_match", "account_fiat"],
    "fee_buffer_pct": 2,
    "on_shortfall": "reject"
  },
  "denomination": { "policy": "smallest_gte" }
}
```

Field behavior:
- **Identity is not stored in `config.json`.** The backend authenticates every
  per-agent request by an `Authorization: Bearer <key>` header where the key *is*
  the agent's id (a secret UUID) — there is no `agentId` path parameter and no
  `GET /agents/{agentId}` route. The key is set once as `QUPICK_API_KEY` (passed by
  `.mcp.json` as the Bearer header). "Already registered?" is answered by a
  `get_agent` (`GET /agents/me`) call succeeding, not by a stored id (removes
  friction #1). The key is delivered by email at registration and is **never
  returned in any response**, so it cannot be auto-persisted — the user sets
  `QUPICK_API_KEY` once from that email.
- `defaults` — values used only when registering a new agent.
- `funding.priority` — the configurable settlement order (see above).
- `funding.fee_buffer_pct` — coverage buffer (default 2).
- `funding.on_shortfall` — `reject` | `confirm`.
- `denomination.policy` — `smallest_gte` picks the smallest package ≥ the requested
  amount automatically (removes friction #2). Future value: `exact`.

## The single human stop (kept, funding-source aware)

```
Product:   Steam USD $20
Price:     $21.60
Worst:     BTC (bitcoin, μ=-0.0026)            ← always shown
Settle:    <chosen source from the waterfall>
             e.g. Bitrefill account BTC ($60.00 avail) · sells it ✓ · will retune
             or   On-chain BTC (wallet $227)          · sells it ✓ · will retune
             or   Bitrefill USD balance ($40.00)      · no sale · portfolio unchanged
Approve?
```

The skill resolves the waterfall to one concrete source *before* showing this
screen, so the user approves a fully-specified transaction.

## Permission allowlist

`.claude/settings.local.json` (already entirely gitignored). Add to
`permissions.allow`:

- Read-only Bitrefill MCP tools:
  `mcp__bitrefill__search-products`, `mcp__bitrefill__product-details`,
  `mcp__bitrefill__get-invoice-by-id`, `mcp__bitrefill__get-order-by-id`,
  `mcp__bitrefill__list-invoices`, `mcp__bitrefill__list-orders`
- Local backend REST (read + the create/optimize/retune writes the flow needs):
  `curl` to `http://127.0.0.1:8000/*`
- Bitrefill balance read (REST, needs `Authorization` header → must be `curl`, not
  WebFetch): `curl` to `https://api.bitrefill.com/v2/accounts/balance`

**Deliberately NOT allowlisted:** `mcp__bitrefill__buy-products`. The harness
prompt on that tool reinforces the approval gate (decision 1).

Exact Bash pattern strings (curl flag matching is prefix-sensitive in Claude Code)
are finalized during implementation by observing the actual commands the skill
emits and confirming the registered MCP server name is `bitrefill`.

## SKILL.md changes (`skills/qupick/SKILL.md`)

1. **New step 0 — read config.** Load `config.json`. On missing/malformed file,
   fall back to today's fully-interactive behavior (ask for name/denomination/etc.)
   and note that no config was found. Never crash.
2. **Step 2 (seed agent)** — call `get_agent` (`GET /agents/me`, Bearer
   `QUPICK_API_KEY`); on success, reuse that basket. On `401`/`404`, register from
   `config.defaults`; the key is emailed, so prompt the user to set `QUPICK_API_KEY`
   from that email rather than writing it back to the file.
3. **Step 3 (product)** — apply `denomination.policy` to auto-select the package.
4. **Step 4 (market)** — unchanged; still fetched, since selection always runs.
5. **Step 5 — rewrite into selection + settlement waterfall** per the funding model
   above, including the `GET /accounts/balance` read and `on_shortfall` handling.
6. **Step 6 (confirm + buy)** — the funding-source-aware approval screen; map the
   chosen source to the correct `buy-products` arguments.
7. **Step 7 (retune)** — gate on the retune rule (only if the worst performer was sold).

## Error handling

- Missing/malformed `config.json` → interactive fallback, no crash.
- `GET /accounts/balance` unreachable or returns an unexpected shape → treat
  account balances as empty, skip `account_*` waterfall tokens, fall through to
  `onchain_match`, and note the degraded mode to the user.
- No spendable crypto candidate at all → hard stop (existing behavior); do not
  silently substitute a stablecoin.
- Backend (`127.0.0.1:8000`) down → stop with an actionable message ("start the
  portfolio backend").

## Files

| File | Change |
|------|--------|
| `skills/qupick/config.json` | new, gitignored (real values) |
| `skills/qupick/config.example.json` | new, committed (placeholders) |
| `skills/qupick/SKILL.md` | step 0 + steps 2/3/5/7 rewrites above |
| `.claude/settings.local.json` | extend `permissions.allow` (already gitignored) |
| `.gitignore` | add `skills/qupick/config.json` |
| `README.md` / `AGENTS.md` | document config + funding order + one-stop flow |

## Verification (dry-run checklist)

No Python is added, so verification is a documented manual checklist run against a
low-balance Bitrefill account and the local backend:

1. **account_match** — worst-performing asset has enough account balance → settles from
   balance, instant, retune drops it.
2. **onchain_match** — worst-performing asset short on account balance but covered by wallet →
   on-chain invoice, poll to `complete`, retune drops it.
3. **account_fiat** — worst-performing asset uncovered by account/wallet but USD balance
   covers → settles from fiat, **no retune**, portfolio unchanged.
4. **on_shortfall: reject** — nothing covers → clean stop, no purchase.
5. **on_shortfall: confirm** — nothing covers → warn + wait, proceed only on yes.
6. **missing config.json** — skill falls back to fully interactive, no crash.
7. **priority reorder** — set `["account_fiat", ...]` and confirm fiat is tried
   first.
8. **buy-products gate** — confirm Claude Code still prompts before `buy-products`
   (not allowlisted).

## Open questions (resolve during implementation, not blocking)

1. **Directing a `balance` payment to a specific asset.** `buy-products` /
   `POST /invoices` documents `payment_method:"balance"` + `auto_pay:true`, but it
   is not yet confirmed whether the debited balance (BTC vs USD vs EUR) can be
   chosen explicitly or is selected by Bitrefill. This affects whether
   `account_match` (retune) and `account_fiat` (no retune) can be distinguished
   reliably. Resolve by inspecting the live `GET /accounts/balance` response shape
   and `buy-products` parameters; if the asset cannot be directed, fall back to:
   treat any `balance` payment as `account_fiat` (no retune) unless Bitrefill
   reports the BTC balance was debited.
2. **Exact `GET /accounts/balance` response schema** — confirm per-asset fields at
   implementation time; the skill reads it generically.
3. **Exact Claude Code Bash allowlist patterns** for the `curl` commands.
