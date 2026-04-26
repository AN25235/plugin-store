# Input Schema

## `evaluate`

```json
{
  "account": {
    "equity": 1000,
    "free_collateral": 700,
    "session_loss_pct": 0.01,
    "session_actions": 0
  },
  "market": {
    "coin": "ETH",
    "price": 3200,
    "recent_high": 3180,
    "recent_low": 3085,
    "atr": 42,
    "rsi": 61,
    "funding_rate": 0.0002,
    "trend_bias": 0.72,
    "trend_strength": 0.66,
    "news_risk": "low"
  },
  "position": {
    "side": "long",
    "entry_price": 3110,
    "notional_usd": 300,
    "unrealized_pnl": 28,
    "peak_unrealized_pnl": 41,
    "scale_ins_used": 0
  }
}
```

## `scan`

```json
{
  "account": {...},
  "markets": [{...}, {...}],
  "positions": {
    "BTC": {...},
    "ETH": {...}
  }
}
```

## Notes

- `no_trade` is returned only through `safe_result`, not as an action.
- `hold` is an internal concept and is normalized outward to `observe`.
- Unsupported coins return `action="observe"` with `safe_result="unsupported_coin"`.
- `halt_new_entries` is a risk-state output with no live command template.
- Signal-bearing results can also expose `upper_band`, `lower_band`, and `component_scores` for diagnostics.
- `profit_giveback` / `position_risk` outcomes include explicit metrics such as `profit_giveback_pct` or `loss_pct_equity`.
- `scale_in` outcomes include `current_notional_usd` and `post_scale_in_notional_usd` so the resulting size is visible before execution.


## TP/SL Post-execution Note

Open-long and open-short plans may include `take_profit`, `stop_loss`, and `post_execution_steps`.

The `take_profit` and `stop_loss` levels are advisory risk levels in the strategy plan. They do not mean protective orders have already been placed.

The user or agent must set protective TP/SL through Hyperliquid Plugin if the installed plugin supports bracket or protective orders. If the plugin flow does not support TP/SL orders, the user must manually confirm the risk before live execution.
