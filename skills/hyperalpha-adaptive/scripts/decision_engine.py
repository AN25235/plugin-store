from __future__ import annotations

from typing import Any, Dict, List, Optional

from execution_mapper import attach_execution_fields, build_order_command
from market_engine import compute_bands, determine_setup, score_setup
from position_engine import manage_existing_position
from risk_engine import compute_position_size, evaluate_session_guard, get_news_policy
from validators import validate_account, validate_market, validate_position


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


def _invalid_result(strategy_id: str, coin: str, errors: List[str]) -> Dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "coin": coin,
        "action": "none",
        "safe_result": "no_trade",
        "market_regime": "invalid_data",
        "signal_score": 0,
        "confidence": "none",
        "reason": "Market data validation failed.",
        "validation_errors": errors,
        "dry_run": True,
        "requires_user_confirmation": False,
    }


def _observe_result(strategy_id: str, coin: str, signal: Dict[str, Any], reason: str, safe_result: str = "threshold_not_met") -> Dict[str, Any]:
    result = {
        "strategy_id": strategy_id,
        "coin": coin,
        "action": "observe",
        "safe_result": safe_result,
        "reason": reason,
        "dry_run": True,
        "requires_user_confirmation": False,
    }
    result.update(_signal_context(signal))
    return result


def _halt_result(strategy_id: str, coin: str, signal: Dict[str, Any], reason: str) -> Dict[str, Any]:
    result = {
        "strategy_id": strategy_id,
        "coin": coin,
        "action": "halt_new_entries",
        "reason": reason,
        "dry_run": True,
        "requires_user_confirmation": False,
    }
    result.update(_signal_context(signal))
    return result


def evaluate_market(bundle: Dict[str, Any], account: Dict[str, Any], market: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = bundle["config"]
    coins = bundle["coins"]
    tiers = bundle["tiers"]
    strategy_id = config["strategy_id"]
    must_execute_via = config["must_execute_via"]
    coin = str(market.get("coin", "UNKNOWN")).upper()

    errors = validate_account(account) + validate_market(market)
    if position is not None:
        errors.extend(validate_position(position))
    if errors:
        return _invalid_result(strategy_id, coin, errors)

    coin_policy = coins.get(coin)
    if not coin_policy or not coin_policy.get("enabled", False):
        return {
            "strategy_id": strategy_id,
            "coin": coin,
            "action": "observe",
            "safe_result": "unsupported_coin",
            "market_regime": "range",
            "signal_score": 0,
            "confidence": "none",
            "reason": "Coin is outside the whitelist for live execution plans.",
            "dry_run": True,
            "requires_user_confirmation": False,
        }

    tier_policy = tiers[coin_policy["tier"]]
    news_policy = get_news_policy(market.get("news_risk", "none"), config)
    session_guard = evaluate_session_guard(account, config)
    bands = compute_bands(market, config)
    setup = determine_setup(market, bands, config)
    signal = score_setup(market, setup, config, news_policy)

    signal["upper_band"] = round(bands["upper_band"], 4)
    signal["lower_band"] = round(bands["lower_band"], 4)

    if position is not None:
        return manage_existing_position(
            strategy_id,
            must_execute_via,
            account,
            market,
            position,
            signal,
            coin_policy,
            tier_policy,
            news_policy,
            session_guard,
            config,
        )

    if not news_policy.get("allow_new_entries", True):
        return _halt_result(strategy_id, coin, signal, "Extreme news risk blocks new entries.")

    if session_guard["block_new_entries"]:
        return _halt_result(strategy_id, coin, signal, f"Session guard blocks new entries because {session_guard['reason']}.")

    min_signal_score = tier_policy["min_signal_score"] + int(news_policy.get("open_score_threshold_adjustment", 0))
    if signal["signal_score"] < 30:
        result = {
            "strategy_id": strategy_id,
            "coin": coin,
            "action": "none",
            "safe_result": "no_trade",
            "reason": "No tradable signal was detected.",
            "dry_run": True,
            "requires_user_confirmation": False,
        }
        result.update(_signal_context(signal))
        return result

    if signal["signal_score"] < min_signal_score or not signal.get("direction"):
        reason = f"Signal score {signal['signal_score']} is below the entry threshold {min_signal_score} for {coin_policy['tier']}."
        return _observe_result(strategy_id, coin, signal, reason)

    size_info = compute_position_size(account, market, coin_policy, news_policy, config)
    if size_info.get("size_blocked"):
        result = _observe_result(
            strategy_id,
            coin,
            signal,
            "Calculated notional is below the configured minimum order notional.",
            safe_result="below_min_notional",
        )
        result["sizing_reason"] = size_info.get("sizing_reason", {})
        return result
    action = "open_long" if signal["direction"] == "long" else "open_short"
    result = {
        "strategy_id": strategy_id,
        "coin": coin,
        "action": action,
        "reason": f"{coin} generated a {signal['market_regime']} {signal['direction']} setup with acceptable RSI and funding.",
        "notional_usd": size_info["notional_usd"],
        "sizing_reason": size_info["sizing_reason"],
        "take_profit": round(float(market["price"]) + float(market["atr"]) * config["band_params"]["take_profit_mult"] * (1 if signal["direction"] == "long" else -1), 4),
        "stop_loss": round(float(market["price"]) - float(market["atr"]) * config["band_params"]["stop_mult"] * (1 if signal["direction"] == "long" else -1), 4),
        "dry_run": True,
        "requires_user_confirmation": True,
        "post_execution_steps": [
            "This plan includes take_profit and stop_loss levels.",
            "After user confirmation, route execution through Hyperliquid Plugin with the required strategy ID.",
            "Set protective TP/SL through Hyperliquid Plugin if the installed plugin supports bracket or protective orders.",
            "If protective TP/SL is not supported by the execution flow, the user must manually confirm the risk before live execution."
        ],
    }
    result.update(_signal_context(signal))
    command = build_order_command(coin, signal["direction"], size_info["notional_usd"], strategy_id)
    return attach_execution_fields(result, command, must_execute_via)


_ACTION_PRIORITY = {
    "open_long": 5,
    "open_short": 5,
    "scale_in": 4,
    "reduce": 3,
    "close_all": 3,
    "halt_new_entries": 2,
    "observe": 1,
    "none": 0,
}


def scan_markets(bundle: Dict[str, Any], account: Dict[str, Any], markets: List[Dict[str, Any]], positions: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    positions = positions or {}
    results: List[Dict[str, Any]] = []
    for market in markets:
        coin = str(market.get("coin", "")).upper()
        result = evaluate_market(bundle, account, market, positions.get(coin))
        results.append(result)

    sorted_results = sorted(results, key=lambda item: (_ACTION_PRIORITY.get(item["action"], 0), item.get("signal_score", 0)), reverse=True)
    top_opportunities = [item for item in sorted_results if item["action"] in {"open_long", "open_short", "scale_in", "reduce", "close_all"}][:3]
    if not top_opportunities:
        top_opportunities = sorted_results[:3]
    return {
        "strategy_id": bundle["config"]["strategy_id"],
        "dry_run": True,
        "results": sorted_results,
        "top_opportunities": top_opportunities,
    }
