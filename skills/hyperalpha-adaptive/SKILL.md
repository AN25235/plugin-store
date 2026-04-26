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

HyperAlpha Adaptive is a community-installable Hyperliquid strategy skill for multi-asset perpetual planning across BTC, ETH, SOL, HYPE, XRP, DOGE, BNB, LINK, and AVAX. It produces dry-run-first trade plans, risk-aware action decisions, and Hyperliquid Plugin command templates while preserving explicit strategy attribution through `--strategy-id an25hlq1`.

This skill does not bypass the Hyperliquid Plugin and does not use private exchange APIs for execution. It is designed as a competition-quality strategy submission with release-grade documentation, explicit safety guardrails, and deterministic no-trade fallbacks for unsupported or invalid states.

## Pre-flight Checks

Before using this skill, ensure:

1. The plugin metadata files remain present and consistent: `plugin.yaml`, `.claude-plugin/plugin.json`, `SKILL.md`, `SUMMARY.md`, `README.md`, and `LICENSE`.
2. The required metadata fields match across files where applicable: `name`, `description`, `version`, `license`, and author identity.
3. `.claude-plugin/plugin.json` is submission-ready, including `license`, `author`, and useful discovery metadata such as `keywords` (and optionally `homepage` / `repository` before final PR submission).
4. `SUMMARY.md` stays in English and keeps the store-friendly structure `Overview`, `Prerequisites`, and `Quick Start`.
5. The strategy plugin metadata is intact in `plugin.yaml`, including:
   - `category: strategy`
   - `dependent_plugin` referencing `hyperliquid-plugin`
   - `risk_level: high`
   - declared external API domain `www.okx.com`
6. Python 3 is available for local validation.
7. The operator understands that `dry_run` is the default safety mode and that any live write action requires explicit user confirmation.
8. Protective TP/SL may need to be placed separately through the Hyperliquid Plugin flow if bracket support is unavailable.
9. The project should remain submission-focused: avoid generated artifacts, undeclared external calls, or stale references to deprecated strategy copies.

Run these checks before release or submission:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json
python3 -m unittest discover -s tests -v
```

## Commands

### Validate configuration

```bash
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
```

**When to use**: Before submission, after config edits, or before running strategy evaluation in a fresh environment.
**Output**: Validation result for `config/default.json`, `config/coins.json`, and `config/risk-tiers.json`.
**Example**: Use this after changing thresholds, supported coins, or risk tiers.

### Evaluate a single market input

```bash
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json
```

**When to use**: When the user wants a decision for one market at a time.
**Output**: A single strategy decision including action, safety result, confidence, diagnostics, and a Hyperliquid command template when a live-capable action is allowed.
**Example**: Evaluate whether ETH should be opened, reduced, scaled in, closed, or observed under the current account and position state.

### Scan multiple markets

```bash
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json
```

**When to use**: When the user wants a ranked multi-asset scan across supported markets.
**Output**: Ordered results prioritized by action severity and signal score.
**Example**: Compare BTC, ETH, SOL, HYPE, XRP, DOGE, BNB, LINK, and AVAX to identify the best current opportunity.

### Evaluate or scan with public market fetching

```bash
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json --fetch-market
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json --fetch-market
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json --fetch-market --audit-output /tmp/evaluate-audit.json
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json --fetch-market --audit-output /tmp/scan-audit.json
```

**When to use**: When public OKX swap market data should override the input `market` payload, and optionally when you need a durable audit artifact of fetched/derived state.
**Output**: Strategy output using public fetched market data where supported. With `--audit-output`, also writes a JSON artifact containing the original request payload, fetched market data, derived market fields, and the final strategy result.
**Example**: For OKX-supported symbols, the fetched data is used. For unsupported fetches such as `HYPE`, the skill degrades safely to `action="observe"` with no live command template. In audit mode, keep the JSON for post-run debugging or handoff.

**Implementation notes / regression guards**:
1. In `scan --fetch-market`, requested markets should be resolved from the input payload's `markets` list when present, with fallback to the enabled coin list only when `payload.markets` is absent or empty. Do not reference CLI `args` or a nonexistent `bundle` object inside helper resolution logic. Keep a regression test covering this path so market-fetch scans cannot crash on helper-scope mistakes.
2. Public fetch is now sourced from OKX swap endpoints, not Binance. Keep metadata and docs aligned with `www.okx.com`, parse `mark-price`, `funding-rate`, and `history-candles` responses with explicit `code == "0"` checks, and sort returned candles by `open_time` before deriving indicators.
3. In mean-reversion / fade logic, watch for structurally unreachable comparisons when breakout or breakdown thresholds are derived from the same swing extrema being checked. Example anti-pattern: `recent_high >= breakout_up` when `breakout_up = swing_high + positive_buffer`, or `recent_low <= breakdown_dn` when `breakdown_dn = swing_low - positive_buffer`. With a positive buffer or ATR multiple, those predicates cannot become true unless the references come from different time windows or different sources. Prove reachability with simple inequality reasoning first, then confirm empirically with a tiny regression harness over representative candle samples. Keep regression tests that (a) demonstrate the old predicates stay false across normal samples and (b) exercise the corrected fade/rebound branches with explicit re-entry fixtures.
4. In `--fetch-market` paths, derive `prior_high`, `prior_low`, `probe_high`, and `probe_low` from separated prior/probe candle windows before calling the decision engine. Without those levels, fetched-market inputs can silently lose fade/rebound eligibility even when explicit manual fixtures still pass.
5. In risk-based sizing, notional can now be reduced by stop distance and an ATR-derived floor in the sizing path may dominate tiny-price assets. For low-priced tier3 fixtures such as DOGE, shrinking ATR alone may not raise size because the implementation clamps ATR to a minimum before deriving stop distance. When writing regression tests, do not assume the default DOGE fixture will still produce `open_long` after sizing changes; instead, use a clearly tradeable fixture (for example a higher price / tighter stop-ratio combination that still reflects tier3 behavior) and assert the intended invariant, such as `tier3 notional < tier1 notional` plus the presence of sizing diagnostics.
6. Account validation must not reject `free_collateral=0` when an existing position is present. Defensive position-management paths still need to evaluate `reduce`, `close_all`, or normalized `observe` outcomes even when no fresh collateral is available. Keep a regression test for zero free collateral plus an open position so payload validation does not accidentally block position defense flows.
7. Risk-based sizing should incorporate stop distance instead of relying only on a fixed equity ratio. A robust pattern here is: derive a base target notional from `per_trade_ratio * risk_mult * news position_mult`, compute `stop_distance_usd = atr * stop_mult`, convert that to `stop_distance_ratio = stop_distance_usd / price`, shrink the base target with a bounded volatility scalar such as `target_stop_distance_ratio / (target_stop_distance_ratio + stop_distance_ratio)`, then cap by `per_trade_ratio_max` and free collateral. Surface diagnostics including `stop_distance_usd`, `stop_distance_ratio`, `target_stop_distance_ratio`, `volatility_scalar`, and `risk_budget_usd`. In regression tests, compare two ATR fixtures that both still clear the entry threshold so the assertion isolates sizing behavior rather than signal gating.
8. `scan` outputs must preserve a consistent ordering contract across both normal and `--fetch-market` paths: `results` should be fully sorted by action priority and then `signal_score`, while `top_opportunities` should contain only actionable items (`open_long`, `open_short`, `scale_in`, `reduce`, `close_all`) when any exist, falling back to the first three sorted results only when no actionable entries are present. Keep a regression test that distinguishes these two expectations so future fetch-path edits do not silently drift from the documented scan behavior.
9. In `--fetch-market` trend derivation, do not rely only on `price` versus a short SMA window. That underestimates structured advances on OKX hourly data and can suppress valid breakout candidates into `range/no_trade`. Prefer a blended trend feature using whole-window move, short-vs-long average spread, close location within the recent range, and directional consistency of closes; keep a regression test where the old SMA-bias logic would leave `trend_strength < trend_strength_min` but the corrected derivation upgrades the same fetched market into a breakout candidate.
10. Breakout/breakdown detection must still respect overextension guardrails. Even if fetch-derived trend strength now qualifies a move as directional, overheated long breakouts above `rsi_overbought` and capitulation short breakdowns below `rsi_oversold` should degrade to no-trade rather than opening or promoting a chase entry. Keep a regression test for the overheated fetch-breakout case so future trend improvements do not silently re-enable momentum chasing.
9. After migrating public fetch to OKX, do not treat passing parser/unit tests as evidence that live fetch-market signals are healthy. Run a live `scan --fetch-market` or per-coin fetch audit across the supported OKX symbols and inspect the derived fields (`price`, `recent_high`, `recent_low`, `atr`, `rsi`, `trend_bias`, `trend_strength`, `prior_*`, `probe_*`) alongside the resulting `action`, `market_regime`, and `signal_score`. A known failure mode is deriving `trend_strength` only from short-horizon SMA displacement, which can produce values near zero on real OKX hourly data and collapse most coins to `market_regime="range"` / `signal_score=32` even though fetch, probe-level derivation, and unit tests all pass. When this happens, treat it as a strategy-feature calibration gap: add a failing regression test for realistic fetched-market trend behavior before changing thresholds or trend-strength logic.
10. Public fetch should include participation confirmation, not only price/funding/candles. Extend OKX fetch-market support with `rubik/stat/contracts/open-interest-history`, return normalized `oi_history_1h` plus current `oi_usd`, and derive `volume_ratio`, `oi_change_ratio`, and `participation_bias` from fetched candles and OI history before evaluating setups. Keep regression tests that verify parser coverage for the OI endpoint and that fetched-market builds surface these derived fields.
11. `--fetch-market` audit export is a supported debugging workflow on both `evaluate` and `scan`. Add `--audit-output /path/to/file.json` when you need a durable artifact for handoff or root-cause analysis. For `evaluate`, the audit should capture `input_payload`, `fetch_result`, `derived_market` on success, optional `fetch_error` on failure, and the final `result`. For `scan`, the audit should capture top-level `input_payload`, `validation_errors`, final `result`, and per-coin entries with `request`, `fetch_result`, `fetch_status`, `derived_market` or `fetch_error`, and the per-coin `result`. Keep regression tests that patch `Path.write_text` and assert the audit schema for both commands so future refactors do not silently drop file export behavior.
12. When adding export-only functionality to this project, follow the user's preferred flow: add targeted failing regression tests first, verify they fail for the intended reason, then implement the feature and rerun both the targeted tests and the full suite. For CLI export/report paths, prefer delivering long handoff material as a file instead of pasting large blobs in chat.

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `Input payload validation failed.` | Missing, invalid, NaN, Infinity, or negative values in account / market / position fields | Fix the payload and rerun `evaluate` or `scan`. |
| `market_fetch_not_supported_for_coin` | Public fetch requested for a coin not supported by the OKX swap fetcher | Return a safe observe result and do not generate a live execution plan. |
| `no_trade_input_state` | Public market fetch or derived market state is incomplete | Keep `dry_run=true`, inspect the input or fetch source, and retry only after data is valid. |
| Unsupported coin result | Coin is outside the strategy whitelist for live execution | Return `action="observe"` and no command template. |
| Session loss / session action guardrail hit | Risk controls block new entries or scale-ins | Allow only safer outcomes such as `reduce` or `close_all` where applicable. |

## Security Notices

- This is a **high-risk strategy plugin** intended for derivatives planning and must be treated as an advanced trading workflow.
- Default behavior is **dry-run first**. Any live write action requires explicit user confirmation.
- Every live write path must preserve `--strategy-id an25hlq1` for attribution.
- The skill must execute through `hyperliquid-plugin`; it must not bypass the dependent plugin with private exchange APIs.
- Stop-loss, session loss caps, action-count caps, and notional guardrails are part of the safety design and must not be removed casually.
- Plans may include take-profit and stop-loss levels, but those levels are strategy outputs, not proof that protective orders have already been placed.
- No secrets, private keys, or undeclared external API calls may be committed in this project.

## Additional Notes

### Decision normalization

- `no_trade` is a safe result, not an action.
- Internal `hold` logic is normalized outward to `observe`.
- `halt_new_entries` is a risk-state output, not a trading command.
- Unsupported coins return `action="observe"` and `safe_result="unsupported_coin"` with no command template.
- Session loss and per-session action caps can block new entries and scale-ins while still allowing `reduce` or `close_all`.

### Project layout

- `config/default.json` — strategy defaults and thresholds
- `config/coins.json` — supported coins, tiers, and risk multipliers
- `config/risk-tiers.json` — shared per-tier policy
- `scripts/adaptive_hyperliquid_strategy.py` — CLI entrypoint
- `scripts/input_adapter.py` — input adaptation layer
- `scripts/decision_engine.py` — decision policy and scan logic
- `scripts/market_engine.py` — ATR / RSI / bands helpers
- `scripts/risk_engine.py` — sizing and risk policy
- `scripts/position_engine.py` — open-position management
- `scripts/execution_mapper.py` — Hyperliquid command templates
- `scripts/validators.py` — config and payload validation
- `tests/test_strategy.py` — regression suite

### Verification reminders

- Confirm every live template still contains `strategy-id`.
- Confirm `SUMMARY.md` stays in English with `Overview`, `Prerequisites`, and `Quick Start`.
- Confirm the competition submission quality bar remains intact after every change.
