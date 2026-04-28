from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

ALLOWED_RISK_PREFERENCES = {"conservative", "balanced", "aggressive"}
ALLOWED_POSITION_SIDES = {"flat", "long", "short"}


class InputAdapterValidationError(ValueError):
    """Raised when raw input cannot be normalized safely."""


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _to_float(value: Any, field: str, *, allow_zero: bool = True, min_value: float | None = None) -> float:
    if value is None:
        raise InputAdapterValidationError(f"{field} is required")
    if not _is_finite_number(value):
        raise InputAdapterValidationError(f"{field} must be a finite number")
    number = float(value)
    if not allow_zero and number == 0:
        raise InputAdapterValidationError(f"{field} must be non-zero")
    if min_value is not None and number < min_value:
        raise InputAdapterValidationError(f"{field} must be >= {min_value}")
    return number


def _to_non_negative_float(value: Any, field: str, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    if not _is_finite_number(value):
        raise InputAdapterValidationError(f"{field} must be a finite number")
    number = float(value)
    if number < 0:
        raise InputAdapterValidationError(f"{field} must be >= 0")
    return number


def _to_int(value: Any, field: str, default: int = 0, min_value: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputAdapterValidationError(f"{field} must be an integer")
    if value < min_value:
        raise InputAdapterValidationError(f"{field} must be >= {min_value}")
    return value


def _first_present(payload: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _normalize_coin(raw: Dict[str, Any]) -> str:
    coin = _first_present(raw, ["coin", "symbol", "asset"])
    if not isinstance(coin, str) or not coin.strip():
        raise InputAdapterValidationError("coin must be a non-empty string")
    return coin.strip().upper()


def _normalize_account(raw: Dict[str, Any]) -> Dict[str, Any]:
    account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
    equity = _first_present(raw, ["equity"])
    if equity is None:
        equity = _first_present(account, ["equity", "account_value", "balance"])
    equity_value = _to_non_negative_float(equity, "account.equity", default=0.0)

    available = _first_present(raw, ["available_balance", "free_collateral"])
    if available is None:
        available = _first_present(account, ["available_balance", "free_collateral", "available", "withdrawable"])
    available_value = _to_non_negative_float(available, "account.available_balance", default=equity_value)

    session_loss_pct = _first_present(account, ["session_loss_pct", "daily_loss_pct"])
    session_loss = 0.0 if session_loss_pct is None else _to_float(session_loss_pct, "account.session_loss_pct")
    if session_loss < -1 or session_loss > 1:
        raise InputAdapterValidationError("account.session_loss_pct must be between -1 and 1")

    session_actions = _first_present(account, ["session_actions", "actions_taken"])

    return {
        "equity": equity_value,
        "free_collateral": available_value,
        "session_loss_pct": session_loss,
        "session_actions": _to_int(session_actions, "account.session_actions", default=0, min_value=0),
    }


def _normalize_position(raw: Dict[str, Any], market_price: float) -> Dict[str, Any] | None:
    position = raw.get("position")
    if position is None:
        return None
    if not isinstance(position, dict):
        raise InputAdapterValidationError("position must be an object when provided")

    side = _first_present(position, ["side", "direction"])
    if side is None:
        size = _first_present(position, ["size", "sz", "contracts"])
        if size is None:
            side = "flat"
        else:
            size_value = _to_float(size, "position.size")
            side = "flat" if size_value == 0 else ("long" if size_value > 0 else "short")
    if not isinstance(side, str):
        raise InputAdapterValidationError("position.side must be a string")
    side = side.strip().lower()
    if side not in ALLOWED_POSITION_SIDES:
        raise InputAdapterValidationError("position.side must be one of flat/long/short")
    if side == "flat":
        return None

    entry_price = _first_present(position, ["entry_price", "avg_entry_price", "average_entry_price"])
    notional = _first_present(position, ["notional_usd", "position_value_usd", "usd_value"])
    if notional is None:
        size = _first_present(position, ["size", "sz", "contracts"])
        if size is not None:
            notional = abs(_to_float(size, "position.size")) * market_price

    unrealized_pnl = _first_present(position, ["unrealized_pnl", "pnl", "unrealizedPnl"])
    peak_unrealized_pnl = _first_present(position, ["peak_unrealized_pnl", "max_unrealized_pnl", "peakPnl"])
    scale_ins_used = _first_present(position, ["scale_ins_used", "adds_count", "scaleInCount"])

    return {
        "side": side,
        "entry_price": _to_float(entry_price, "position.entry_price", min_value=0.0),
        "notional_usd": _to_non_negative_float(notional, "position.notional_usd", default=0.0),
        "unrealized_pnl": 0.0 if unrealized_pnl is None else _to_float(unrealized_pnl, "position.unrealized_pnl"),
        "peak_unrealized_pnl": 0.0 if peak_unrealized_pnl is None else _to_float(peak_unrealized_pnl, "position.peak_unrealized_pnl"),
        "scale_ins_used": _to_int(scale_ins_used, "position.scale_ins_used", default=0, min_value=0),
    }


def _normalize_market(raw: Dict[str, Any], coin: str) -> Dict[str, Any]:
    market = raw.get("market") if isinstance(raw.get("market"), dict) else {}

    price = _first_present(raw, ["price", "mark_price", "mid_price"])
    if price is None:
        price = _first_present(market, ["price", "mark_price", "mid_price", "last_price"])
    price_value = _to_float(price, "market.price", min_value=0.0)
    if price_value <= 0:
        raise InputAdapterValidationError("market.price must be > 0")

    recent_high = _first_present(market, ["recent_high", "high", "day_high"])
    recent_low = _first_present(market, ["recent_low", "low", "day_low"])
    atr = _first_present(market, ["atr", "atr_value"])
    rsi = _first_present(market, ["rsi"])
    funding_rate = _first_present(market, ["funding_rate", "funding"])
    trend_bias = _first_present(market, ["trend_bias", "bias"])
    trend_strength = _first_present(market, ["trend_strength", "strength"])
    news_risk = _first_present(market, ["news_risk", "event_risk"]) or "none"

    recent_high_value = _to_non_negative_float(recent_high, "market.recent_high", default=price_value)
    recent_low_value = _to_non_negative_float(recent_low, "market.recent_low", default=price_value)
    if recent_high_value < recent_low_value:
        raise InputAdapterValidationError("market.recent_high must be >= market.recent_low")

    atr_value = _to_non_negative_float(atr, "market.atr", default=max(price_value * 0.01, 0.0))
    rsi_value = 50.0 if rsi is None else _to_float(rsi, "market.rsi")
    if rsi_value < 0 or rsi_value > 100:
        raise InputAdapterValidationError("market.rsi must be between 0 and 100")

    funding_value = 0.0 if funding_rate is None else _to_float(funding_rate, "market.funding_rate")
    trend_bias_value = 0.0 if trend_bias is None else _to_float(trend_bias, "market.trend_bias")
    if trend_bias_value < -1 or trend_bias_value > 1:
        raise InputAdapterValidationError("market.trend_bias must be between -1 and 1")
    trend_strength_value = 0.0 if trend_strength is None else _to_float(trend_strength, "market.trend_strength")
    if trend_strength_value < 0 or trend_strength_value > 1:
        raise InputAdapterValidationError("market.trend_strength must be between 0 and 1")

    if not isinstance(news_risk, str) or not news_risk.strip():
        raise InputAdapterValidationError("market.news_risk must be a non-empty string")

    return {
        "coin": coin,
        "price": price_value,
        "recent_high": recent_high_value,
        "recent_low": recent_low_value,
        "atr": atr_value,
        "rsi": rsi_value,
        "funding_rate": funding_value,
        "trend_bias": trend_bias_value,
        "trend_strength": trend_strength_value,
        "news_risk": news_risk.strip().lower(),
    }


def _normalize_user_preferences(raw: Dict[str, Any]) -> Dict[str, Any]:
    preferences = raw.get("user_preferences")
    if preferences is None:
        preferences = raw.get("risk_preference")
    if preferences is None:
        return {"risk_preference": "balanced"}
    if isinstance(preferences, str):
        risk_preference = preferences.strip().lower()
        if risk_preference not in ALLOWED_RISK_PREFERENCES:
            raise InputAdapterValidationError("user risk preference must be conservative/balanced/aggressive")
        return {"risk_preference": risk_preference}
    if not isinstance(preferences, dict):
        raise InputAdapterValidationError("user_preferences must be an object or string")

    risk_preference = _first_present(preferences, ["risk_preference", "profile", "mode"]) or "balanced"
    if not isinstance(risk_preference, str) or risk_preference.strip().lower() not in ALLOWED_RISK_PREFERENCES:
        raise InputAdapterValidationError("user risk preference must be conservative/balanced/aggressive")

    max_leverage = _first_present(preferences, ["max_leverage", "leverage_cap"])
    per_trade_risk = _first_present(preferences, ["per_trade_risk", "risk_per_trade"])

    normalized = {
        "risk_preference": risk_preference.strip().lower(),
    }
    if max_leverage is not None:
        normalized["max_leverage"] = _to_non_negative_float(max_leverage, "user_preferences.max_leverage", default=0.0)
    if per_trade_risk is not None:
        value = _to_non_negative_float(per_trade_risk, "user_preferences.per_trade_risk", default=0.0)
        if value > 1:
            raise InputAdapterValidationError("user_preferences.per_trade_risk must be between 0 and 1")
        normalized["per_trade_risk"] = value
    return normalized


def normalize_strategy_input(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise InputAdapterValidationError("input payload must be an object")

    coin = _normalize_coin(raw)
    account = _normalize_account(raw)
    market = _normalize_market(raw, coin)
    position = _normalize_position(raw, market_price=market["price"])
    user_preferences = _normalize_user_preferences(raw)

    return {
        "coin": coin,
        "account": account,
        "position": position,
        "market": market,
        "user_preferences": user_preferences,
    }


def normalize_strategy_input_with_errors(raw: Dict[str, Any]) -> Tuple[Dict[str, Any] | None, List[str]]:
    try:
        return normalize_strategy_input(raw), []
    except InputAdapterValidationError as exc:
        return None, [str(exc)]
