from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from decision_engine import evaluate_market, scan_markets
from input_adapter import normalize_strategy_input_with_errors
from market_fetcher import fetch_public_market_data
from validators import (
    load_json,
    normalize_input_payload,
    validate_account,
    validate_position,
    validate_config_bundle,
)

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


def load_bundle(config_path: str | Path) -> Dict[str, Any]:
    config_path = Path(config_path).resolve()
    root = config_path.parent.parent
    config = load_json(config_path)
    coins = load_json(root / "config" / "coins.json")
    tiers = load_json(root / "config" / "risk-tiers.json")
    return {"config": config, "coins": coins, "tiers": tiers}


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _extract_coin(payload: Dict[str, Any]) -> str:
    coin = payload.get("coin")
    if coin is None and isinstance(payload.get("market"), dict):
        coin = payload["market"].get("coin")
    if coin is None:
        coin = payload.get("symbol")
    if not isinstance(coin, str) or not coin.strip():
        return ""
    return coin.strip().upper()


def _compute_atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    if not candles:
        return 0.0
    true_ranges: List[float] = []
    previous_close: Optional[float] = None
    for candle in candles:
        high = _safe_float(candle.get("high"))
        low = _safe_float(candle.get("low"))
        close = _safe_float(candle.get("close"))
        if high <= 0 or low <= 0 or close <= 0:
            continue
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(max(tr, 0.0))
        previous_close = close
    window = true_ranges[-period:] if true_ranges else []
    if not window:
        last_close = _safe_float(candles[-1].get("close"), default=0.0)
        return max(last_close * 0.01, 0.01)
    atr = sum(window) / len(window)
    return max(atr, 0.01)


def _compute_rsi(candles: List[Dict[str, Any]], period: int = 14) -> float:
    closes = [_safe_float(candle.get("close"), default=0.0) for candle in candles]
    closes = [value for value in closes if value > 0]
    if len(closes) < 2:
        return 50.0
    deltas = [current - previous for previous, current in zip(closes[:-1], closes[1:])]
    window = deltas[-period:] if deltas else []
    if not window:
        return 50.0
    gains = [delta for delta in window if delta > 0]
    losses = [-delta for delta in window if delta < 0]
    avg_gain = sum(gains) / len(window)
    avg_loss = sum(losses) / len(window)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(_clip(rsi, 0.0, 100.0), 4)


def _derive_probe_levels(closed_candles: List[Dict[str, Any]]) -> Dict[str, float]:
    if not closed_candles:
        return {}

    lookback = closed_candles[-min(len(closed_candles), 12):]
    probe_span = min(4, max(1, len(lookback) // 2))
    probe_window = lookback[-probe_span:]
    prior_window = lookback[:-probe_span]

    if not prior_window and len(lookback) > 1:
        prior_window = lookback[:-1]

    probe_high = max(_safe_float(candle.get("high"), default=0.0) for candle in probe_window)
    probe_low = min(_safe_float(candle.get("low"), default=0.0) for candle in probe_window)

    if prior_window:
        prior_high = max(_safe_float(candle.get("high"), default=0.0) for candle in prior_window)
        prior_low = min(_safe_float(candle.get("low"), default=0.0) for candle in prior_window)
    else:
        prior_high = probe_high
        prior_low = probe_low

    if prior_high <= 0:
        prior_high = probe_high
    if prior_low <= 0:
        prior_low = probe_low

    return {
        "prior_high": prior_high,
        "prior_low": prior_low,
        "probe_high": max(probe_high, prior_high),
        "probe_low": min(probe_low, prior_low),
    }


def _derive_participation_features(
    closed_candles: List[Dict[str, Any]],
    oi_history: List[Dict[str, Any]],
    trend_bias: float,
) -> Dict[str, float]:
    volumes = [_safe_float(candle.get("volume"), default=0.0) for candle in closed_candles]
    volumes = [value for value in volumes if value >= 0]
    if volumes:
        lookback = volumes[-min(len(volumes), 12):]
        latest_volume = lookback[-1]
        average_volume = sum(lookback) / len(lookback)
        volume_ratio = latest_volume / max(average_volume, 1e-9)
    else:
        volume_ratio = 1.0

    oi_values = [_safe_float(row.get("oi_usd"), default=0.0) for row in oi_history if _safe_float(row.get("oi_usd"), default=0.0) > 0]
    if len(oi_values) >= 2:
        oi_change_ratio = (oi_values[-1] - oi_values[0]) / max(oi_values[0], 1e-9)
    else:
        oi_change_ratio = 0.0

    volume_signal = _clip((volume_ratio - 1.0) / 1.2, -1.0, 1.0)
    oi_signal = _clip(oi_change_ratio / 0.15, -1.0, 1.0)
    participation_bias = _clip(0.6 * volume_signal + 0.4 * oi_signal, -1.0, 1.0)

    return {
        "volume_ratio": round(max(volume_ratio, 0.0), 4),
        "oi_change_ratio": round(oi_change_ratio, 4),
        "participation_bias": round(participation_bias, 4),
    }


def _derive_trend_features(closed_candles: List[Dict[str, Any]], price: float) -> Tuple[float, float]:
    closes = [_safe_float(candle.get("close"), default=0.0) for candle in closed_candles]
    highs = [_safe_float(candle.get("high"), default=0.0) for candle in closed_candles]
    lows = [_safe_float(candle.get("low"), default=0.0) for candle in closed_candles]
    closes = [value for value in closes if value > 0]
    highs = [value for value in highs if value > 0]
    lows = [value for value in lows if value > 0]
    if not closes or not highs or not lows or price <= 0:
        return 0.0, 0.0

    recent_high = max(highs)
    recent_low = min(lows)
    basis = max(recent_high - recent_low, price * 0.01, 1e-9)

    first_close = closes[0]
    last_close = closes[-1]
    short_span = min(4, len(closes))
    long_span = min(12, len(closes))
    short_sma = sum(closes[-short_span:]) / short_span
    long_sma = sum(closes[-long_span:]) / long_span

    move = (last_close - first_close) / basis
    spread = (short_sma - long_sma) / basis
    close_location = _clip(((price - recent_low) / basis) * 2.0 - 1.0, -1.0, 1.0)

    deltas = [current - previous for previous, current in zip(closes[:-1], closes[1:])]
    if deltas:
        positive_steps = sum(1 for delta in deltas if delta > 0)
        negative_steps = sum(1 for delta in deltas if delta < 0)
        consistency = (positive_steps - negative_steps) / len(deltas)
    else:
        consistency = 0.0

    raw_bias = 0.4 * move + 0.35 * (spread * 1.6) + 0.2 * close_location + 0.05 * consistency
    trend_bias = _clip(raw_bias, -1.0, 1.0)

    aligned_strength = 0.0
    for value, weight in ((move, 0.4), (spread * 1.6, 0.35), (close_location, 0.2), (consistency, 0.05)):
        if trend_bias == 0.0 or value == 0.0 or (value > 0) == (trend_bias > 0):
            aligned_strength += abs(value) * weight
        else:
            aligned_strength += max(0.0, 0.4 - abs(value)) * weight * 0.2

    trend_strength = _clip(aligned_strength, 0.0, 1.0)
    return trend_bias, trend_strength


def _build_market_from_fetch(fetch_result: Dict[str, Any], requested_coin: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not fetch_result.get("ok"):
        return None, str(fetch_result.get("reason", "no_trade_input_state"))
    candles = fetch_result.get("candles_1h")
    if not isinstance(candles, list) or not candles:
        return None, "no_trade_input_state"
    price = _safe_float(fetch_result.get("price"), default=0.0)
    if price <= 0:
        return None, "no_trade_input_state"
    # Exclude the last candle when enough candles are present; it may still be forming.
    closed_candles = candles[:-1] if len(candles) > 2 else candles
    highs = [_safe_float(candle.get("high"), default=0.0) for candle in closed_candles]
    lows = [_safe_float(candle.get("low"), default=0.0) for candle in closed_candles]
    highs = [value for value in highs if value > 0]
    lows = [value for value in lows if value > 0]
    if not highs or not lows:
        return None, "no_trade_input_state"
    recent_high = max(highs)
    recent_low = min(lows)
    if recent_high < recent_low:
        return None, "no_trade_input_state"
    trend_bias, trend_strength = _derive_trend_features(closed_candles, price)
    probe_levels = _derive_probe_levels(closed_candles)
    oi_history = fetch_result.get("oi_history_1h") if isinstance(fetch_result.get("oi_history_1h"), list) else []
    participation_features = _derive_participation_features(closed_candles, oi_history, trend_bias)
    market = {
        "coin": str(fetch_result.get("coin") or requested_coin).upper(),
        "price": price,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "atr": _compute_atr(candles),
        "rsi": _compute_rsi(candles),
        "funding_rate": _safe_float(fetch_result.get("funding"), default=0.0),
        "trend_bias": round(trend_bias, 4),
        "trend_strength": round(trend_strength, 4),
        "news_risk": "none",
        "timestamp": fetch_result.get("timestamp"),
        "source": fetch_result.get("source", "okx_swap_public"),
        "oi_usd": _safe_float(fetch_result.get("oi_usd"), default=0.0),
    }
    market.update(probe_levels)
    market.update(participation_features)
    return market, None


def _fetch_failure_result(strategy_id: str, coin: str, reason: str) -> Dict[str, Any]:
    normalized_coin = (coin or "").upper()
    if reason == "market_fetch_not_supported_for_coin":
        return {
            "strategy_id": strategy_id,
            "coin": normalized_coin,
            "action": "observe",
            "safe_result": "unsupported_market_fetch",
            "reason": reason,
            "dry_run": True,
            "requires_user_confirmation": False,
        }
    return {
        "strategy_id": strategy_id,
        "coin": normalized_coin,
        "action": "observe" if normalized_coin else "none",
        "safe_result": "no_trade",
        "market_regime": "market_fetch_unavailable",
        "signal_score": 0,
        "confidence": "none",
        "reason": reason,
        "dry_run": True,
        "requires_user_confirmation": False,
    }


def _validation_failure_result(strategy_id: str, errors: List[str]) -> Dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "action": "none",
        "safe_result": "no_trade",
        "market_regime": "invalid_data",
        "signal_score": 0,
        "confidence": "none",
        "reason": "Input payload validation failed.",
        "validation_errors": errors,
        "dry_run": True,
        "requires_user_confirmation": False,
    }


def _sort_scan_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        results,
        key=lambda item: (_ACTION_PRIORITY.get(item.get("action", "none"), 0), item.get("signal_score", 0)),
        reverse=True,
    )



def _normalize_evaluate_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Prefer flexible Hermes/agent input adapter, then fall back to legacy schema validation."""
    if normalize_strategy_input_with_errors is not None:
        adapted, adapter_errors = normalize_strategy_input_with_errors(payload)
        if adapted is not None:
            return {
                "account": adapted["account"],
                "market": adapted["market"],
                "position": adapted.get("position"),
            }, []
    return normalize_input_payload("evaluate", payload)



def _parse_coin_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    coins = []
    for item in value.split(","):
        coin = item.strip().upper()
        if coin:
            coins.append(coin)
    return list(dict.fromkeys(coins))


def _scan_requested_markets(payload: Dict[str, Any], coins_config: Dict[str, Any], coins_arg: Optional[str]) -> List[Dict[str, Any]]:
    requested = _parse_coin_list(coins_arg)
    if requested:
        return [{"coin": coin} for coin in requested]

    raw_markets = payload.get("markets")
    if isinstance(raw_markets, list) and raw_markets:
        return raw_markets

    return [
        {"coin": coin}
        for coin, info in coins_config.items()
        if isinstance(info, dict) and info.get("enabled", False)
    ]


def _write_audit_output(path: Optional[str], payload: Dict[str, Any]) -> None:
    if not path:
        return
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_validate_config(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.config)
    errors = validate_config_bundle(bundle["config"], bundle["coins"], bundle["tiers"])
    result = {"ok": not errors, "errors": errors}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.config)
    payload = load_json(args.input)
    audit_output = getattr(args, "audit_output", None)
    original_payload = payload
    audit_context: Dict[str, Any] = {}

    if args.fetch_market:
        coin = _extract_coin(payload)
        if not coin:
            result = _validation_failure_result(bundle["config"]["strategy_id"], ["coin must be provided when --fetch-market is used"])
            _write_audit_output(audit_output, {
                "mode": "evaluate",
                "coin": "",
                "fetch_status": "invalid_request",
                "input_payload": original_payload,
                "result": result,
            })
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        fetch_result = fetch_public_market_data(coin)
        market, fetch_error = _build_market_from_fetch(fetch_result, coin)
        audit_context = {
            "mode": "evaluate",
            "coin": coin,
            "input_payload": original_payload,
            "fetch_result": fetch_result,
        }
        if fetch_error:
            result = _fetch_failure_result(bundle["config"]["strategy_id"], coin, fetch_error)
            audit_context.update({
                "fetch_status": "error",
                "fetch_error": fetch_error,
                "result": result,
            })
            _write_audit_output(audit_output, audit_context)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        payload = dict(payload)
        # Explicit policy: when --fetch-market is set, fetched public market data overrides input market JSON.
        payload["market"] = market
        audit_context.update({
            "fetch_status": "ok",
            "derived_market": market,
        })

    normalized, errors = normalize_strategy_input_with_errors(payload)
    if errors:
        normalized, errors = _normalize_evaluate_payload(payload)

    if errors:
        result = _validation_failure_result(bundle["config"]["strategy_id"], errors)
    else:
        result = evaluate_market(bundle, normalized["account"], normalized["market"], normalized.get("position"))

    if args.fetch_market:
        audit_context["result"] = result
        _write_audit_output(audit_output, audit_context)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.config)
    payload = load_json(args.input)
    strategy_id = bundle["config"]["strategy_id"]
    audit_output = getattr(args, "audit_output", None)

    if not args.fetch_market:
        normalized, errors = normalize_input_payload("scan", payload)
        if errors:
            result = {
                "strategy_id": strategy_id,
                "dry_run": True,
                "results": [],
                "top_opportunities": [],
                "validation_errors": errors,
            }
        else:
            result = scan_markets(bundle, normalized["account"], normalized["markets"], normalized.get("positions"))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    raw_account = payload.get("account")
    raw_positions = payload.get("positions", {})
    raw_markets = _scan_requested_markets(payload, bundle["coins"], getattr(args, "coins", None))
    validation_errors: List[str] = []
    audit_entries: List[Dict[str, Any]] = []
    result_by_coin: Dict[str, Dict[str, Any]] = {}

    if not isinstance(raw_account, dict):
        validation_errors.append("account object is required")
        account = {}
    else:
        account = raw_account
        validation_errors.extend(validate_account(account))

    if raw_positions is None:
        raw_positions = {}
    if not isinstance(raw_positions, dict):
        validation_errors.append("positions must be an object when provided")
        positions: Dict[str, Dict[str, Any]] = {}
    else:
        positions = raw_positions
        for coin_key, position in positions.items():
            if not isinstance(position, dict):
                validation_errors.append(f"positions.{coin_key} must be an object")
                continue
            position_errors = validate_position(position)
            validation_errors.extend([f"positions.{coin_key}.{err}" for err in position_errors])

    if not isinstance(raw_markets, list) or not raw_markets:
        validation_errors.append("markets must be a non-empty list for scan")
        raw_markets = []

    fetched_markets: List[Dict[str, Any]] = []
    fetch_failures: List[Dict[str, Any]] = []
    for idx, raw_market in enumerate(raw_markets):
        if not isinstance(raw_market, dict):
            validation_errors.append(f"markets[{idx}] must be an object")
            continue
        coin = _extract_coin(raw_market)
        if not coin:
            validation_errors.append(f"markets[{idx}].coin must be a non-empty string")
            continue
        fetch_result = fetch_public_market_data(coin)
        market, fetch_error = _build_market_from_fetch(fetch_result, coin)
        audit_entry: Dict[str, Any] = {
            "coin": coin,
            "request": raw_market,
            "fetch_result": fetch_result,
        }
        if fetch_error:
            failure_result = _fetch_failure_result(strategy_id, coin, fetch_error)
            fetch_failures.append(failure_result)
            audit_entry.update({
                "fetch_status": "error",
                "fetch_error": fetch_error,
                "result": failure_result,
            })
            audit_entries.append(audit_entry)
            continue
        fetched_markets.append(market)
        audit_entry.update({
            "fetch_status": "ok",
            "derived_market": market,
        })
        audit_entries.append(audit_entry)

    if validation_errors:
        result = {
            "strategy_id": strategy_id,
            "dry_run": True,
            "results": fetch_failures,
            "top_opportunities": fetch_failures[:3],
            "validation_errors": validation_errors,
        }
        for entry in audit_entries:
            if "result" not in entry:
                entry["result"] = _fetch_failure_result(strategy_id, entry["coin"], "validation_blocked")
        _write_audit_output(audit_output, {
            "mode": "scan",
            "input_payload": payload,
            "validation_errors": validation_errors,
            "coins": audit_entries,
            "result": result,
        })
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if fetched_markets:
        result = scan_markets(bundle, account, fetched_markets, positions)
        merged_results = result["results"] + fetch_failures
        result_by_coin = {item.get("coin"): item for item in result["results"] if isinstance(item, dict)}
    else:
        merged_results = list(fetch_failures)
        result = {
            "strategy_id": strategy_id,
            "dry_run": True,
            "results": [],
            "top_opportunities": [],
        }

    sorted_results = _sort_scan_results(merged_results)
    actionable = [item for item in sorted_results if item.get("action") in {"open_long", "open_short", "scale_in", "reduce", "close_all"}]
    result["results"] = sorted_results
    result["top_opportunities"] = actionable[:3] if actionable else sorted_results[:3]

    for entry in audit_entries:
        if entry.get("fetch_status") == "ok":
            coin = entry.get("coin")
            entry["result"] = result_by_coin.get(coin, _fetch_failure_result(strategy_id, str(coin), "missing_scan_result"))

    _write_audit_output(audit_output, {
        "mode": "scan",
        "input_payload": payload,
        "validation_errors": validation_errors,
        "coins": audit_entries,
        "result": result,
    })
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive Hyperliquid Strategy CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-config", help="Validate config bundle")
    validate_parser.add_argument("--config", required=True)
    validate_parser.set_defaults(func=cmd_validate_config)

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate a single market input")
    evaluate_parser.add_argument("--config", required=True)
    evaluate_parser.add_argument("--input", required=True)
    evaluate_parser.add_argument("--fetch-market", action="store_true", help="Fetch public OKX swap market data and override input market JSON")
    evaluate_parser.add_argument("--audit-output", default=None, help="Optional path to write evaluate --fetch-market audit JSON")
    evaluate_parser.set_defaults(func=cmd_evaluate)

    scan_parser = subparsers.add_parser("scan", help="Scan multiple markets")
    scan_parser.add_argument("--config", required=True)
    scan_parser.add_argument("--input", required=True)
    scan_parser.add_argument("--fetch-market", action="store_true", help="Fetch public OKX swap market data per coin and override input market JSON")
    scan_parser.add_argument("--audit-output", default=None, help="Optional path to write scan --fetch-market audit JSON")
    scan_parser.add_argument("--coins", default=None, help="Optional comma-separated coin list for scan --fetch-market, e.g. BTC,ETH,SOL,HYPE")
    scan_parser.set_defaults(func=cmd_scan)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
