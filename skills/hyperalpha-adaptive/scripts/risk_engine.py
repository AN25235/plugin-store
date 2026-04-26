from __future__ import annotations

from typing import Any, Dict

_DEFAULT_TARGET_STOP_DISTANCE_RATIO = 0.002


def get_news_policy(level: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return config["news_risk"].get(level, config["news_risk"]["none"])


def evaluate_session_guard(account: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    risk = config["risk"]
    reasons = []
    block_new_entries = False
    block_scale_in = False

    if account.get("session_loss_pct", 0) >= risk["session_loss_limit_pct"]:
        reasons.append("session loss limit reached")
        block_new_entries = True
        block_scale_in = True

    if account.get("session_actions", 0) >= risk["max_actions_per_session"]:
        reasons.append("max actions per session reached")
        block_new_entries = True
        block_scale_in = True

    return {
        "block_new_entries": block_new_entries,
        "block_scale_in": block_scale_in,
        "reason": "; ".join(reasons) if reasons else "session risk healthy",
    }


def compute_position_size(
    account: Dict[str, Any],
    market: Dict[str, Any],
    coin_policy: Dict[str, Any],
    news_policy: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    equity = float(account["equity"])
    free_collateral = float(account.get("free_collateral", equity))
    price = max(float(market["price"]), 1e-9)
    atr = max(float(market["atr"]), 0.01)
    risk = config["risk"]
    band_params = config["band_params"]
    base_ratio = float(risk["per_trade_ratio"])
    tier_risk_mult = float(coin_policy["risk_mult"])
    news_position_mult = float(news_policy.get("position_mult", 1.0))

    base_target_notional = equity * base_ratio * tier_risk_mult * news_position_mult
    max_cap_ratio = float(risk["per_trade_ratio_max"]) * tier_risk_mult * news_position_mult
    max_cap_notional = round(equity * max_cap_ratio, 2)

    stop_distance_usd = max(atr * float(band_params["stop_mult"]), 0.01)
    stop_distance_ratio = stop_distance_usd / price
    target_stop_distance_ratio = float(risk.get("target_stop_distance_ratio", _DEFAULT_TARGET_STOP_DISTANCE_RATIO))
    volatility_scalar = target_stop_distance_ratio / (target_stop_distance_ratio + stop_distance_ratio)
    risk_adjusted_notional = base_target_notional * volatility_scalar
    capped_notional = min(risk_adjusted_notional, max_cap_notional)
    notional_usd = round(min(capped_notional, free_collateral), 2)

    min_notional_usd = float(risk.get("min_notional_usd", 0))
    size_blocked = min_notional_usd > 0 and notional_usd < min_notional_usd
    effective_ratio = 0.0 if equity <= 0 else capped_notional / equity
    risk_budget_usd = round(base_target_notional * stop_distance_ratio, 2)
    return {
        "notional_usd": notional_usd,
        "size_blocked": size_blocked,
        "reason": "below_min_notional" if size_blocked else "sizing_ok",
        "sizing_reason": {
            "base_ratio": round(base_ratio, 4),
            "tier_risk_mult": round(tier_risk_mult, 4),
            "news_position_mult": round(news_position_mult, 4),
            "final_ratio": round(effective_ratio, 4),
            "free_collateral_cap": round(free_collateral, 2),
            "min_notional_usd": round(min_notional_usd, 2),
            "max_cap_notional": max_cap_notional,
            "base_target_notional": round(base_target_notional, 2),
            "risk_budget_usd": risk_budget_usd,
            "stop_distance_usd": round(stop_distance_usd, 4),
            "stop_distance_ratio": round(stop_distance_ratio, 6),
            "target_stop_distance_ratio": round(target_stop_distance_ratio, 6),
            "volatility_scalar": round(volatility_scalar, 4),
        },
    }


def compute_scale_in_allowance(
    account: Dict[str, Any],
    market: Dict[str, Any],
    current_notional: float,
    coin_policy: Dict[str, Any],
    news_policy: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    base = compute_position_size(account, market, coin_policy, news_policy, config)
    max_cap_notional = base["sizing_reason"]["max_cap_notional"]
    remaining_capacity = round(max(max_cap_notional - float(current_notional), 0.0), 2)
    additional_notional = round(min(base["notional_usd"], remaining_capacity), 2)
    min_notional_usd = float(config["risk"].get("min_notional_usd", 0))
    size_blocked = additional_notional <= 0 or (min_notional_usd > 0 and additional_notional < min_notional_usd)
    reason = "remaining_capacity_exhausted" if additional_notional <= 0 else ("below_min_notional" if size_blocked else "sizing_ok")
    return {
        "additional_notional": additional_notional,
        "remaining_capacity": remaining_capacity,
        "size_blocked": size_blocked,
        "reason": reason,
        "sizing_reason": base["sizing_reason"],
    }
