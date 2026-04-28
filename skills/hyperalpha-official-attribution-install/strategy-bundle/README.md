# HyperAlpha Adaptive

HyperAlpha Adaptive is a competition-quality community strategy plugin for Hyperliquid perpetual planning. It is designed to satisfy the OKX plugin-store `docs/FOR-DEVELOPERS.md` requirements for strategy submissions while keeping the trading logic dry-run-first, risk-aware, and attributable through `--strategy-id an25hlq1`.

## Included files

- `plugin.yaml` — plugin metadata and strategy manifest
- `.claude-plugin/plugin.json` — Claude skill registration metadata
- `SKILL.md` — agent-facing operating contract
- `SUMMARY.md` — English marketplace summary
- `config/` — strategy defaults, supported coins, and risk tiers
- `scripts/` — CLI entrypoint and strategy engines
- `tests/` — regression coverage

## Local verification

```bash
python3 -m py_compile scripts/*.py
python3 scripts/adaptive_hyperliquid_strategy.py validate-config --config config/default.json
python3 scripts/adaptive_hyperliquid_strategy.py evaluate --config config/default.json --input examples/single_evaluate_input.json
python3 scripts/adaptive_hyperliquid_strategy.py scan --config config/default.json --input examples/scan_input.json
python3 -m unittest discover -s tests -v
```

## Submission notes

- Category is `strategy`
- Dependent execution plugin is `hyperliquid-plugin`
- Public external API domain currently declared: `www.okx.com`
- Live-capable write paths must preserve `--strategy-id an25hlq1`
- Dry-run should remain the default safety posture
- No secrets or undeclared external calls should be introduced
