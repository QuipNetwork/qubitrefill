# AGENTS.md

Guidance for coding agents working in this repository.

## Skills

### Bitrefill

Vendored at [`skills/bitrefill/`](skills/bitrefill/SKILL.md) (upstream: <https://github.com/bitrefill/agents/blob/main/skills/bitrefill/SKILL.md>). Read the full `SKILL.md` and follow its link-outs before acting.

**Bitrefill** sells digital goods (gift cards, mobile top-ups, eSIMs) across 180+ countries and 1,500+ brands. Pay with crypto, Lightning, USDC via x402, or pre-funded account balance. Codes deliver instantly after payment confirms.

This skill **routes by capability, not by use case**. The same intent ("buy a Steam card") plays out differently across hosts — pick a path based on what the runtime can do.

**Triggers:** the user mentions Bitrefill, gift cards, mobile top-up, eSIM data plan, refilling a phone, or asks to pay or check out with crypto, Lightning, USDC, or x402.

#### Pick a path (first match wins)

1. **Inside OpenClaw?** Check for `~/.openclaw/openclaw.json`, `~/.openclaw/skills/`, or `openclaw` on PATH. Default purchase path: guest CLI via `exec` (no auth). Sign in for `balance`/cashback.
2. **Browse-only intent (no purchase)?** Residential-IP browser → browse path. Datacenter egress only → `www.bitrefill.com` returns 403 Cloudflare; use MCP `search-products` / `product-details` instead.
3. **MCP supported?** Remote HTTP/SSE MCP at `https://api.bitrefill.com/mcp`. Highest-fidelity purchase channel — typed tool calls, OAuth or API key, no shell needed.
4. **Shell + `npm install` available?** CLI ≥ 0.3.0: guest checkout first (`buy-products --email` + crypto). Sign in for `balance`, cashback, order history.
5. **Outbound HTTP from agent loop?** REST API as last resort — verbose, no typed validation.
6. **None of the above?** Give the user a `bitrefill.com` link and stop.

#### Spending safeguards (read before any purchase)

This skill enables **real-money transactions**. Codes deliver instantly and digital goods are non-refundable.

- **Confirm before buying.** Present product, denomination, price, and payment method. Wait for explicit user approval. Autonomous purchasing only when the user opts in for the current session.
- **Treat codes as cash.** Never paste them in group chats or public channels. Prefer in-memory storage over plain-text logs. Advise the user to redeem ASAP.
- **Use a dedicated, low-balance account.** Never give the agent access to high-balance accounts or crypto wallet seeds. This skill is **not a wallet**.
- **Log every purchase.** `invoice_id`, product, amount, payment method.

#### Reference files

| File | Use when |
|------|----------|
| [browse.md](skills/bitrefill/references/browse.md) | Agent has residential-IP browser; user wants to explore |
| [mcp.md](skills/bitrefill/references/mcp.md) | MCP-capable host; preferred purchase path |
| [cli.md](skills/bitrefill/references/cli.md) | Shell + npm; guest checkout or signed-in CLI ≥ 0.3.0 |
| [cli-headless-auth.md](skills/bitrefill/references/cli-headless-auth.md) | Inbox + magic-link auth for headless agents |
| [api.md](skills/bitrefill/references/api.md) | HTTP-only runtime; Personal / Business / Affiliate REST tiers |
| [host-openclaw.md](skills/bitrefill/references/host-openclaw.md) | OpenClaw Gateway — guest CLI via `exec` preferred |
| [capability-matrix.md](skills/bitrefill/references/capability-matrix.md) | Per-client viable paths cheat sheet |
| [safeguards.md](skills/bitrefill/references/safeguards.md) | Spending policy + per-host hardening |
| [troubleshooting.md](skills/bitrefill/references/troubleshooting.md) | Common errors across all paths |

#### Source of truth

For exhaustive enums (countries, payment methods, full endpoint list), see <https://docs.bitrefill.com>.

### Qupick

Vendored at [`skills/qupick/SKILL.md`](skills/qupick/SKILL.md). Delegates purchase mechanics to the bitrefill skill above and adds portfolio selection on top.

**Triggers:** "pay with my worst performer", "use my worst crypto to buy X".

**Requires:** portfolio backend running at `http://127.0.0.1:8000`. The skill seeds a new agent over the Bitrefill-payable currencies (BTC, ETH, BNB, SOL, XRP, USDT, USDC, DOGE, ZEC, ALGO, FIL) via `POST /agents` + `POST /agents/{id}/optimize`, or re-uses an existing agent. Uses `GET /agents/{id}/market` to retrieve per-asset μ and holdings, then picks the worst performing crypto that Bitrefill accepts as payment — preferring a candidate whose holdings cover the product price, but falling back to the outright worst performing (with a shortfall warning) when none do. After purchase, retuning drops the spent asset from the basket.

#### `GET /agents/{agent_id}/market`

Returns per-asset expected return (μ) and current holdings for an agent:

```json
{
  "agentId": "abc12345",
  "assets": [
    {"ticker": "BTC", "name": "Bitcoin", "assetClass": "crypto", "mu": 0.0012, "units": 0.05, "usd": 3200.0},
    {"ticker": "ETH", "name": "Ethereum", "assetClass": "crypto", "mu": -0.0003, "units": 1.2, "usd": 3600.0}
  ]
}
```

Pre-solve: falls back to the agent's configured basket with `units=0`. Post-solve: reflects actual holdings. μ is the annualised hourly expected return over the last 7 days (`MU_WINDOW_HOURS=168`).