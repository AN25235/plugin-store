from __future__ import annotations

from typing import Any, Dict

from execution_mapper import attach_execution_fields, build_close_command, build_order_command
from risk_engine import compute_scale_in_allowance


def _signal_context(signal: Dict[str, Any]) -> Dict[str, Any]:
    context = {
        "market_regime": signal["market_regime"],
        "signal_score": signal["signal_score"],
        "confidence": signal["confidence"],
    }
    if signal.get("entry_profile") is not None:
        context["entry_profile"] = signal["entry_profile"]
    if signal.get("component_scores"):
        context["component_scores"] = signal["component_scores"]
    if "upper_band" in signal:
        context["upper_band"] = signal["upper_band"]
    if "lower_band" in signal:
        context["lower_band"] = signal["lower_band"]
    return context


def _base_result(strategy_id: str, coin: str, action: str, signal: Dict[str, Any], market_regime: str, reason: str) -> Dict[str, Any]:
    result = {
        "strategy_id": strategy_id,
        "coin": coin,
        "action": action,
        "market_regime": market_regime,
        "reason": reason,
    }
    result.update(_signal_context(signal))
    result["market_regime"] = market_regime
    return result


def manage_existing_position(
    strategy_id: str,
    must_execute_via: str,
    account: Dict[str, Any],
    market: Dict[str, Any],
    position: Dict[str, Any],
    signal: Dict[str, Any],
    coin_policy: Dict[str, Any],
    tier_policy: Dict[str, Any],
    news_policy: Dict[str, Any],
    session_guard: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    coin = market["coin"].upper()
    risk = config["risk"]
    position_rules = config["position_rules"]
    side = position["side"]
    equity = float(account["equity"])
    unrealized = float(position.get("unrealized_pnl", 0))
    peak_unrealized = float(position.get("peak_unrealized_pnl", 0))
    reduce_fraction = float(position_rules["reduce_fraction"])
    close_fraction = float(position_rules["close_fraction"])

    loss_pct_equity = max(0.0, -unrealized / max(equity, 1e-9))
    if loss_pct_equity >= risk["emergency_loss_pct"]:
        result = _base_result(strategy_id, coin, "close_all", signal, "position_risk", "Position loss exceeded emergency loss threshold.")
        result["fraction"] = close_fraction
        result["loss_pct_equity"] = round(loss_pct_equity, 4)
        result["dry_run"] = True
        result["requires_user_confirmation"] = True
        command = build_close_command(coin, close_fraction, strategy_id)
        return attach_execution_fields(result, command, must_execute_via)

    if loss_pct_equity >= risk["soft_loss_pct"]:
        result = _base_result(strategy_id, coin, "reduce", signal, "position_risk", "Position loss exceeded soft loss threshold.")
        result["fraction"] = reduce_fraction
        result["loss_pct_equity"] = round(loss_pct_equity, 4)
        result["dry_run"] = True
        result["requires_user_confirmation"] = True
        command = build_close_command(coin, reduce_fraction, strategy_id)
        return attach_execution_fields(result, command, must_execute_via)

    if peak_unrealized > 0:
        giveback = max(0.0, (peak_unrealized - unrealized) / peak_unrealized)
        if giveback >= position_rules["close_giveback_pct"]:
            result = _base_result(strategy_id, coin, "close_all", signal, "profit_giveback", "Position gave back more than 60% of peak unrealized profit.")
            result["fraction"] = close_fraction
            result["profit_giveback_pct"] = round(giveback, 4)
            result["dry_run"] = True
            result["requires_user_confirmation"] = True
            command = build_close_command(coin, close_fraction, strategy_id)
            return attach_execution_fields(result, command, must_execute_via)
        if giveback >= position_rules["reduce_giveback_pct"]:
            result = _base_result(strategy_id, coin, "reduce", signal, "profit_giveback", "Position gave back more than 35% of peak unrealized profit.")
            result["fraction"] = reduce_fraction
            result["profit_giveback_pct"] = round(giveback, 4)
            result["dry_run"] = True
            result["requires_user_confirmation"] = True
            command = build_close_command(coin, reduce_fraction, strategy_id)
            return attach_execution_fields(result, command, must_execute_via)

    rsi = float(market["rsi"])
    funding = float(market["funding_rate"])
    thresholds = config["thresholds"]
    if unrealized > 0 and side == "long" and rsi >= thresholds["rsi_overbought"] and funding >= thresholds["funding_hot_long"]:
        result = _base_result(strategy_id, coin, "reduce", signal, "position_risk", "Profitable long is overheated on both RSI and funding.")
        result["fraction"] = reduce_fraction
        result["dry_run"] = True
        result["requires_user_confirmation"] = True
        command = build_close_command(coin, reduce_fraction, strategy_id)
        return attach_execution_fields(result, command, must_execute_via)
    if unrealized > 0 and side == "short" and rsi <= thresholds["rsi_oversold"] and funding <= thresholds["funding_cold_short"]:
        result = _base_result(strategy_id, coin, "reduce", signal, "position_risk", "Profitable short is crowded on both RSI and funding.")
        result["fraction"] = reduce_fraction
        result["dry_run"] = True
        result["requires_user_confirmation"] = True
        command = build_close_command(coin, reduce_fraction, strategy_id)
        return attach_execution_fields(result, command, must_execute_via)

    side_alignment = (side == "long" and signal.get("direction") == "long") or (side == "short" and signal.get("direction") == "short")
    if (
        unrealized > 0
        and side_alignment
        and signal["signal_score"] >= tier_policy["min_signal_score"]
        and position.get("scale_ins_used", 0) < risk["max_scaleins"]
        and not session_guard["block_scale_in"]
        and news_policy.get("allow_scale_in", True)
        and not (
            (side == "long" and funding >= thresholds["funding_hot_long"]) or
            (side == "short" and funding <= thresholds["funding_cold_short"])
        )
    ):
        allowance = compute_scale_in_allowance(account, market, float(position["notional_usd"]), coin_policy, news_policy, config)
        if not allowance.get("size_blocked") and allowance["additional_notional"] > 0:
            current_notional = round(float(position["notional_usd"]), 2)
            result = _base_result(strategy_id, coin, "scale_in", signal, signal["market_regime"], "Existing position is profitable and trend confirmation allows one controlled scale-in.")
            result["notional_usd"] = allowance["additional_notional"]
            result["sizing_reason"] = allowance["sizing_reason"]
            result["current_notional_usd"] = current_notional
            result["post_scale_in_notional_usd"] = round(current_notional + allowance["additional_notional"], 2)
            result["dry_run"] = True
            result["requires_user_confirmation"] = True
            command = build_order_command(coin, side, allowance["additional_notional"], strategy_id)
            return attach_execution_fields(result, command, must_execute_via)

    reason = "Existing position is healthy; hold normalized to observe."
    if session_guard["block_scale_in"]:
        reason = f"Existing position is healthy; new adds blocked because {session_guard['reason']}."
    elif not news_policy.get("allow_scale_in", True):
        reason = "Existing position is healthy; scale-in blocked by news risk."

    result = _base_result(strategy_id, coin, "observe", signal, signal["market_regime"], reason)
    result["safe_result"] = "hold_normalized"
    result["dry_run"] = True
    result["requires_user_confirmation"] = False
    return result
