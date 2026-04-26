# hyperalpha-adaptive

## Overview

HyperAlpha Adaptive is a multi-asset Hyperliquid perpetual strategy plugin that evaluates market state, position state, and account risk before producing dry-run-first trading plans.

Core operations:

- Evaluate a single market and return an action such as `open_long`, `open_short`, `reduce`, `scale_in`, `close_all`, or `observe`
- Scan multiple supported markets and rank the best opportunities by action priority and signal score
- Generate Hyperliquid Plugin command templates with explicit strategy attribution through `--strategy-id an25hlq1`
- Enforce risk-aware no-trade fallbacks for invalid payloads, unsupported coins, market-fetch failures, and session guardrail breaches

Tags: `hyperliquid` `perpetuals` `strategy` `risk-management` `adaptive`

## Prerequisites

- Python 3 available for local validation and execution
- Supported venue: Hyperliquid perpetuals via the dependent `hyperliquid-plugin`
- Supported coins for live-capable planning: BTC, ETH, SOL, HYPE, XRP, DOGE, BNB, LINK, AVAX
- Public market fetch source declared in metadata: `www.okx.com`
- Dry-run mode should remain the default operating mode
- Explicit user confirmation is required before any live write action
- Protective TP/SL may need to be placed separately if the execution flow does not support bracket orders

## Quick Start

1. **Validate the configuration**: Run `python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json` to confirm the config bundle is internally consistent.
2. **Run a single evaluation**: Use `python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json` to inspect one market decision.
3. **Run a market scan**: Use `python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json` to compare multiple markets and rank opportunities.
4. **Optionally fetch public market data**: Add `--fetch-market` to `evaluate` or `scan` when you want public OKX swap market data to override the input market block for supported symbols.
5. **Review safety before execution**: Only consider a live action after confirming the result, the strategy attribution flag, and the risk controls such as stop-loss and session guardrails.
