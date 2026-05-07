---
name: hyperalpha-adaptive
description: "Multi-asset adaptive perpetual strategy skill for Hyperliquid Plugin with dry-run safety, dynamic risk controls, position management, and explicit strategy attribution."
version: "1.0.0"
author: "cw"
license: MIT
tags:
  - hyperliquid
  - perpetuals
  - multi-asset
  - strategy
  - risk-management
  - adaptive
---

# HyperAlpha Adaptive

## Overview

HyperAlpha Adaptive is a multi-asset adaptive perpetual trading strategy for Hyperliquid DEX. It covers BTC, ETH, SOL, HYPE, XRP, DOGE, BNB, LINK, and AVAX, producing risk-aware trade decisions with automatic position sizing, stop-loss/take-profit levels, and Hyperliquid Plugin execution templates.

Core features:
- **Dry-run first** — all decisions default to simulation mode; live execution requires explicit confirmation
- **Multi-regime detection** — breakout, breakdown, fade/rebound, and range regimes with overextension guardrails
- **Adaptive risk sizing** — stop-distance-based position sizing with per-tier risk multipliers and ATR volatility scaling
- **Participation confirmation** — volume ratio, OI change, and participation bias as secondary filters
- **Full attribution** — all trades preserve `--strategy-id an25hlq1` through Hyperliquid Plugin

## Trigger

This skill should be activated when the user expresses intent to:

**Primary triggers:**
- "帮我分析一下BTC行情" / "分析市场"
- "扫描一下哪个币有机会" / "scan markets"
- "BTC能不能做多" / "ETH能开空吗"
- "帮我跑一下策略" / "run strategy"
- "evaluate BTC" / "evaluate ETH"
- "现在适合开仓吗"

**Secondary triggers:**
- "看看哪个币信号最强"
- "多资产扫描"
- "检查一下持仓要不要减仓"
- "策略怎么看当前行情"

**Should NOT trigger:**
- General price queries without strategy intent ("BTC现在多少钱")
- Manual order placement ("帮我开多0.01个BTC")
- Non-Hyperliquid exchange operations
- Portfolio balance checks without strategy analysis

## Prerequisites

1. Python 3.8+ available
2. `hyperliquid-plugin` installed and configured (Agentic Wallet registered)
3. Funded Hyperliquid perp account (USDC deposited)
4. Network access to `www.okx.com` for public market data fetching

## Commands

### Validate configuration

```bash
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
```

**When to use**: Before first run or after config edits.
**Output**:
```json
{"status": "valid", "configs_checked": ["default.json", "coins.json", "risk-tiers.json"]}
```

### Evaluate a single market

```bash
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json --fetch-market
```

**When to use**: Analyze one coin's current setup and get a trade/no-trade decision.
**Output example**:
```json
{
  "action": "open_long",
  "coin": "BTC",
  "market_regime": "breakout",
  "signal_score": 72,
  "confidence": "medium",
  "notional_usd": 45.6,
  "take_profit": 98500.0,
  "stop_loss": 94200.0,
  "command_template": "hyperliquid-plugin order --coin BTC --side buy --size 0.00048 --leverage 5 --tp-px 98500 --sl-px 94200 --strategy-id an25hlq1 --confirm",
  "diagnostics": {
    "trend_bias": 0.65,
    "trend_strength": 0.42,
    "rsi": 58.3,
    "atr": 1250.0,
    "participation_bias": 0.15
  }
}
```

### Scan multiple markets

```bash
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json --fetch-market
```

**When to use**: Rank all supported coins by opportunity strength.
**Output example**:
```json
{
  "results": [
    {"coin": "BTC", "action": "open_long", "market_regime": "breakout", "signal_score": 72},
    {"coin": "ETH", "action": "observe", "market_regime": "range", "signal_score": 45},
    {"coin": "SOL", "action": "open_short", "market_regime": "breakdown", "signal_score": 68}
  ],
  "top_opportunities": [
    {"coin": "BTC", "action": "open_long", "signal_score": 72}
  ]
}
```

### Audit mode (with market data export)

```bash
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json --fetch-market --audit-output /tmp/scan-audit.json
```

**When to use**: Debug or verify fetched market data and derived indicators.
**Output**: Strategy result + JSON audit file with raw fetched data, derived fields, and per-coin decisions.

## Decision Logic

| Market Regime | Action | Condition |
|---------------|--------|-----------|
| Breakout | `open_long` | Price breaks above resistance + trend confirmation + RSI not overbought |
| Breakdown | `open_short` | Price breaks below support + trend confirmation + RSI not oversold |
| Fade/Rebound | `open_long/short` | Mean-reversion at extremes with participation divergence |
| Range | `observe` | No directional edge detected |
| Overextended | `no_trade` | RSI extreme + chase risk too high |

Signal score thresholds (configurable per tier):
- Tier 1 (BTC, ETH): ≥ 55
- Tier 2 (SOL, HYPE, XRP): ≥ 60
- Tier 3 (DOGE, BNB, LINK, AVAX): ≥ 65

## Risk Controls

- **Per-trade sizing**: ATR-based stop distance × volatility scalar, capped by max equity ratio
- **Session loss cap**: Halts new entries after cumulative session loss exceeds threshold
- **Action count cap**: Limits trades per session to prevent overtrading
- **Scale-in guard**: Min-notional check after capacity clipping; blocks sub-threshold additions
- **News risk filter**: Extreme news blocks all new entries; high news reduces size

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `Input payload validation failed` | Missing or invalid account/market/position fields | Fix input payload and retry |
| `market_fetch_not_supported_for_coin` | Coin not available on OKX swap API | Returns safe `observe` with no execution template |
| `no_trade_input_state` | Incomplete market data after fetch | Keep dry_run, inspect data source, retry |
| `threshold_not_met` | Signal score below tier minimum | No trade — wait for stronger setup |
| `session_loss_cap_hit` | Cumulative loss exceeded limit | Only `reduce`/`close_all` allowed until reset |

## Project Layout

```
config/
  default.json          — strategy defaults and thresholds
  coins.json            — supported coins, tiers, risk multipliers
  risk-tiers.json       — per-tier signal thresholds and policies
scripts/
  adaptive_hyperliquid_strategy.py  — CLI entrypoint
  decision_engine.py    — market regime detection and signal scoring
  market_engine.py      — ATR / RSI / bands / trend derivation
  risk_engine.py        — position sizing and risk policy
  position_engine.py    — open-position management (reduce/close)
  market_fetcher.py     — OKX public API data fetcher
  execution_mapper.py   — Hyperliquid Plugin command templates
  input_adapter.py      — input normalization layer
  validators.py         — config and payload validation
examples/
  single_evaluate_input.json  — sample evaluate input
  scan_input.json             — sample scan input
tests/
  test_strategy.py      — regression test suite
```

## Security

- Default mode is **dry-run** — no live trades without explicit user confirmation
- All execution goes through `hyperliquid-plugin` — never bypasses to direct RPC
- Every command template includes `--strategy-id an25hlq1` for attribution
- No API keys, private keys, or secrets in the codebase
- Stop-loss and session caps are safety-critical and must not be disabled

## Verification

```bash
python3 -m py_compile scripts/*.py
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json
python3 -m unittest discover -s tests -v
```
