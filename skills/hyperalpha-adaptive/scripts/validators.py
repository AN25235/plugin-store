from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple


ALLOWED_NEWS_RISKS = {"none", "low", "medium", "high", "extreme"}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _require_positive(payload: dict, field: str, errors: List[str]) -> None:
    value = payload.get(field)
    if not _is_finite_number(value) or value <= 0:
        errors.append(f"{field} must be a positive finite number")


def validate_account(account: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    _require_positive(account, "equity", errors)
    free_collateral = account.get("free_collateral", account.get("equity"))
    if not _is_finite_number(free_collateral) or free_collateral < 0:
        errors.append("free_collateral must be a finite number >= 0")
    session_loss_pct = account.get("session_loss_pct", 0)
    if not _is_finite_number(session_loss_pct) or session_loss_pct < -1 or session_loss_pct > 1:
        errors.append("session_loss_pct must be a finite number between -1 and 1")
    session_actions = account.get("session_actions", 0)
    if not isinstance(session_actions, int) or session_actions < 0:
        errors.append("session_actions must be a non-negative integer")
    return errors


def validate_market(market: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    coin = market.get("coin")
    if not isinstance(coin, str) or not coin.strip():
        errors.append("coin must be a non-empty string")
    for field in ("price", "recent_high", "recent_low", "atr"):
        _require_positive(market, field, errors)
    if (
        _is_finite_number(market.get("recent_high"))
        and _is_finite_number(market.get("recent_low"))
        and market.get("recent_high") < market.get("recent_low")
    ):
        errors.append("recent_high must be greater than or equal to recent_low")
    rsi = market.get("rsi")
    if not _is_finite_number(rsi) or rsi < 0 or rsi > 100:
        errors.append("rsi must be in the range [0, 100]")
    funding_rate = market.get("funding_rate")
    if not _is_finite_number(funding_rate):
        errors.append("funding_rate must be finite")
    trend_bias = market.get("trend_bias")
    if not _is_finite_number(trend_bias) or trend_bias < -1 or trend_bias > 1:
        errors.append("trend_bias must be in the range [-1, 1]")
    trend_strength = market.get("trend_strength")
    if not _is_finite_number(trend_strength) or trend_strength < 0 or trend_strength > 1:
        errors.append("trend_strength must be in the range [0, 1]")
    news_risk = market.get("news_risk", "none")
    if news_risk not in ALLOWED_NEWS_RISKS:
        errors.append("news_risk must be one of none/low/medium/high/extreme")
    return errors


def validate_position(position: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    side = position.get("side")
    if side not in {"long", "short"}:
        errors.append("position.side must be 'long' or 'short'")
    _require_positive(position, "entry_price", errors)
    _require_positive(position, "notional_usd", errors)
    for field in ("unrealized_pnl", "peak_unrealized_pnl"):
        value = position.get(field, 0)
        if not _is_finite_number(value):
            errors.append(f"{field} must be finite")
    scale_ins_used = position.get("scale_ins_used", 0)
    if not isinstance(scale_ins_used, int) or scale_ins_used < 0:
        errors.append("scale_ins_used must be a non-negative integer")
    return errors


def validate_config_bundle(config: dict, coins: dict, tiers: dict) -> List[str]:
    errors: List[str] = []
    if not config.get("strategy_id"):
        errors.append("strategy_id missing")
    risk = config.get("risk", {})
    for key in (
        "per_trade_ratio",
        "per_trade_ratio_max",
        "soft_loss_pct",
        "emergency_loss_pct",
        "session_loss_limit_pct",
    ):
        if key not in risk:
            errors.append(f"risk.{key} missing")
    if risk.get("per_trade_ratio", 0) <= 0:
        errors.append("risk.per_trade_ratio must be positive")
    if risk.get("per_trade_ratio_max", 0) < risk.get("per_trade_ratio", 0):
        errors.append("risk.per_trade_ratio_max must be >= risk.per_trade_ratio")
    if not coins:
        errors.append("coins config must not be empty")
    if not tiers:
        errors.append("risk tiers config must not be empty")
    for coin, info in coins.items():
        tier = info.get("tier")
        if tier not in tiers:
            errors.append(f"coin {coin} references unknown tier {tier}")
        if info.get("risk_mult", 0) <= 0:
            errors.append(f"coin {coin} risk_mult must be positive")
    for tier_name, tier_info in tiers.items():
        if tier_info.get("min_signal_score", -1) < 0 or tier_info.get("min_signal_score", 101) > 100:
            errors.append(f"{tier_name}.min_signal_score must be in [0, 100]")
    return errors


def normalize_input_payload(mode: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    account = payload.get("account")
    if not isinstance(account, dict):
        errors.append("account object is required")
        account = {}
    else:
        errors.extend(validate_account(account))

    if mode == "evaluate":
        market = payload.get("market")
        if not isinstance(market, dict):
            errors.append("market object is required for evaluate")
        else:
            errors.extend(validate_market(market))
        position = payload.get("position")
        if position is not None:
            if not isinstance(position, dict):
                errors.append("position must be an object when provided")
            else:
                errors.extend(validate_position(position))
        return {"account": account, "market": market, "position": position}, errors

    if mode == "scan":
        markets = payload.get("markets")
        if not isinstance(markets, list) or not markets:
            errors.append("markets must be a non-empty list for scan")
            markets = []
        else:
            for idx, market in enumerate(markets):
                if not isinstance(market, dict):
                    errors.append(f"markets[{idx}] must be an object")
                    continue
                market_errors = validate_market(market)
                errors.extend([f"markets[{idx}].{err}" for err in market_errors])
        positions = payload.get("positions", {})
        if positions is None:
            positions = {}
        if not isinstance(positions, dict):
            errors.append("positions must be an object when provided")
            positions = {}
        else:
            for coin, position in positions.items():
                if not isinstance(position, dict):
                    errors.append(f"positions.{coin} must be an object")
                    continue
                pos_errors = validate_position(position)
                errors.extend([f"positions.{coin}.{err}" for err in pos_errors])
        return {"account": account, "markets": markets, "positions": positions}, errors

    errors.append(f"unsupported mode: {mode}")
    return {}, errors
