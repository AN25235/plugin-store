from __future__ import annotations

import math
from typing import Any, Dict, Optional


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not math.isfinite(value):
        return low if low <= 0 <= high else low
    return max(low, min(high, value))


def _optional_level(market: Dict[str, Any], key: str, fallback: float) -> float:
    value = market.get(key, fallback)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def compute_bands(market: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, float]:
    params = config["band_params"]
    atr = float(market["atr"])
    recent_high = float(market["recent_high"])
    recent_low = float(market["recent_low"])
    return {
        "upper_band": recent_high + atr * float(params["breakout_mult"]),
        "lower_band": recent_low - atr * float(params["breakdown_mult"]),
    }


def _atr_quality_score(price: float, atr: float, config: Dict[str, Any]) -> float:
    ratio = atr / price
    low = config["thresholds"]["atr_ratio_min"]
    high = config["thresholds"]["atr_ratio_max"]
    if ratio < low:
        return 0.35
    if ratio > high:
        return 0.45
    midpoint = (low + high) / 2
    spread = max((high - low) / 2, 1e-9)
    distance = abs(ratio - midpoint) / spread
    return clamp(1 - 0.4 * distance)


def _rsi_score(direction: str, regime: str, rsi: float, config: Dict[str, Any]) -> float:
    thresholds = config["thresholds"]
    if direction == "long":
        if regime == "rebound":
            return clamp((45 - rsi) / 20) if rsi <= 45 else 0.3
        if rsi > thresholds["rsi_overbought"]:
            return 0.15
        if rsi > thresholds["rsi_long_max"]:
            return 0.45
        return clamp((rsi - 40) / 30)
    if regime == "fade":
        return clamp((rsi - 55) / 20) if rsi >= 55 else 0.25
    if rsi < thresholds["rsi_oversold"]:
        return 0.15
    if rsi < thresholds["rsi_short_min"]:
        return 0.45
    return clamp((60 - rsi) / 30)


def _funding_score(direction: str, funding_rate: float, config: Dict[str, Any]) -> float:
    thresholds = config["thresholds"]
    if direction == "long":
        if funding_rate > thresholds["funding_hot_long"]:
            return 0.2
        if funding_rate < thresholds["funding_cold_long"]:
            return 0.8
        return 1.0 - min(abs(funding_rate) / max(thresholds["funding_hot_long"], 1e-9), 0.8)
    if funding_rate < thresholds["funding_cold_short"]:
        return 0.2
    if funding_rate > thresholds["funding_hot_short"]:
        return 0.75
    return 1.0 - min(abs(funding_rate) / max(abs(thresholds["funding_cold_short"]), 1e-9), 0.8)


def _participation_score(direction: str, market: Dict[str, Any]) -> float:
    participation_bias = _optional_level(market, "participation_bias", 0.0)
    volume_ratio = max(_optional_level(market, "volume_ratio", 1.0), 0.0)
    oi_change_ratio = _optional_level(market, "oi_change_ratio", 0.0)
    directional_participation = participation_bias
    directional_oi = oi_change_ratio

    bias_component = clamp(directional_participation, -1.0, 1.0)
    volume_component = clamp((volume_ratio - 1.0) / 0.8, -1.0, 1.0)
    oi_component = clamp(directional_oi / 0.15, -1.0, 1.0)
    return clamp(0.7 * bias_component + 0.2 * volume_component + 0.1 * oi_component, -1.0, 1.0)


def determine_setup(market: Dict[str, Any], bands: Dict[str, float], config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    price = float(market["price"])
    recent_high = float(market["recent_high"])
    recent_low = float(market["recent_low"])
    atr = float(market["atr"])
    rsi = float(market["rsi"])
    trend_bias = float(market["trend_bias"])
    trend_strength = float(market["trend_strength"])
    thresholds = config["thresholds"]
    params = config["band_params"]

    if price >= bands["upper_band"] and trend_bias > 0 and trend_strength >= thresholds["trend_strength_min"]:
        if rsi > thresholds["rsi_overbought"]:
            return None
        return {"direction": "long", "market_regime": "breakout"}
    if price <= bands["lower_band"] and trend_bias < 0 and trend_strength >= thresholds["trend_strength_min"]:
        if rsi < thresholds["rsi_oversold"]:
            return None
        return {"direction": "short", "market_regime": "breakdown"}

    edge_strength_min = max(0.35, thresholds["trend_strength_min"] - 0.2)
    edge_trend_bias_min = 0.35
    edge_breakout_buffer = max(atr, atr * 3.0 * float(params["breakout_mult"]))
    edge_breakdown_buffer = max(atr, atr * 3.0 * float(params["breakdown_mult"]))

    if (
        price >= bands["upper_band"] - edge_breakout_buffer
        and trend_bias >= edge_trend_bias_min
        and trend_strength >= edge_strength_min
        and rsi <= thresholds["rsi_long_max"]
    ):
        return {"direction": "long", "market_regime": "breakout"}
    if (
        price <= bands["lower_band"] + edge_breakdown_buffer
        and trend_bias <= -edge_trend_bias_min
        and trend_strength >= edge_strength_min
        and rsi >= thresholds["rsi_short_min"]
    ):
        return {"direction": "short", "market_regime": "breakdown"}

    breakout_buffer = atr * float(params["breakout_mult"])
    breakdown_buffer = atr * float(params["breakdown_mult"])
    prior_high = _optional_level(market, "prior_high", recent_high)
    prior_low = _optional_level(market, "prior_low", recent_low)
    probe_high = _optional_level(market, "probe_high", recent_high)
    probe_low = _optional_level(market, "probe_low", recent_low)

    rejected_from_probe = probe_high >= prior_high + breakout_buffer and price <= probe_high - breakout_buffer
    rebounded_from_probe = probe_low <= prior_low - breakdown_buffer and price >= probe_low + breakdown_buffer

    if rejected_from_probe and price < bands["upper_band"] and rsi >= thresholds["rsi_overbought"] and trend_bias <= 0.2:
        return {"direction": "short", "market_regime": "fade"}
    if rebounded_from_probe and price > bands["lower_band"] and rsi <= thresholds["rsi_oversold"] and trend_bias >= -0.2:
        return {"direction": "long", "market_regime": "rebound"}
    return None


def score_setup(
    market: Dict[str, Any],
    setup: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    news_policy: Dict[str, Any],
) -> Dict[str, Any]:
    if not setup:
        return {
            "signal_score": 32,
            "confidence": "low",
            "market_regime": "range",
            "direction": None,
            "entry_profile": "no_trade",
            "component_scores": {},
        }

    direction = setup["direction"]
    regime = setup["market_regime"]
    scoring = config["scoring"]
    price = float(market["price"])
    atr = float(market["atr"])
    trend_bias = float(market["trend_bias"])
    trend_strength = float(market["trend_strength"])
    funding_rate = float(market["funding_rate"])
    rsi = float(market["rsi"])
    probe_high = _optional_level(market, "probe_high", float(market["recent_high"]))
    probe_low = _optional_level(market, "probe_low", float(market["recent_low"]))

    directional_bias = max(trend_bias, 0.0) if direction == "long" else max(-trend_bias, 0.0)
    trend_component = clamp((directional_bias + trend_strength) / 2.0) * scoring["trend"]

    bands = compute_bands(market, config)
    if regime == "breakout":
        breakout_ratio = (price - bands["upper_band"]) / max(atr, 1e-9)
    elif regime == "breakdown":
        breakout_ratio = (bands["lower_band"] - price) / max(atr, 1e-9)
    elif regime == "fade":
        breakout_ratio = (probe_high - price) / max(atr, 1e-9)
    else:
        breakout_ratio = (price - probe_low) / max(atr, 1e-9)
    breakout_component = clamp(breakout_ratio / 1.5) * scoring["breakout"]

    atr_component = _atr_quality_score(price, atr, config) * scoring["atr"]
    rsi_component = _rsi_score(direction, regime, rsi, config) * scoring["rsi"]
    funding_component = _funding_score(direction, funding_rate, config) * scoring["funding"]
    position_component = scoring["position"]
    participation_component = _participation_score(direction, market) * scoring.get("participation", 0)
    news_penalty = abs(float(news_policy.get("score_adjustment", 0)))

    raw_score = (
        trend_component
        + breakout_component
        + atr_component
        + rsi_component
        + funding_component
        + position_component
        + participation_component
        - news_penalty
    )
    signal_score = max(0, min(int(round(raw_score)), 100))
    if signal_score >= 90:
        confidence = "high"
        entry_profile = "strong_signal"
    elif signal_score >= 75:
        confidence = "medium-high"
        entry_profile = "normal_position"
    elif signal_score >= 60:
        confidence = "medium"
        entry_profile = "small_position"
    else:
        confidence = "low"
        entry_profile = "observe" if signal_score >= 40 else "no_trade"

    return {
        "signal_score": signal_score,
        "confidence": confidence,
        "market_regime": regime,
        "direction": direction,
        "entry_profile": entry_profile,
        "component_scores": {
            "trend": round(trend_component, 2),
            "breakout": round(breakout_component, 2),
            "atr": round(atr_component, 2),
            "rsi": round(rsi_component, 2),
            "funding": round(funding_component, 2),
            "position": round(position_component, 2),
            "participation": round(participation_component, 2),
            "news_penalty": round(news_penalty, 2),
        },
    }
