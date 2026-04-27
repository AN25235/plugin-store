from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from adaptive_hyperliquid_strategy import (  # type: ignore
    cmd_evaluate,
    cmd_scan,
    load_bundle,
    _scan_requested_markets,
    _build_market_from_fetch,
)
from decision_engine import evaluate_market, scan_markets  # type: ignore
from execution_mapper import build_close_command  # type: ignore
from market_engine import compute_bands  # type: ignore
from market_fetcher import fetch_public_market_data  # type: ignore


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(ROOT / "config" / "default.json")

    def account(self, **overrides):
        data = {
            "equity": 1000,
            "free_collateral": 900,
            "session_loss_pct": 0.0,
            "session_actions": 0,
        }
        data.update(overrides)
        return data

    def breakout_market(self, coin="BTC", **overrides):
        if coin == "DOGE":
            data = {
                "coin": "DOGE",
                "price": 0.198,
                "recent_high": 0.192,
                "recent_low": 0.17,
                "atr": 0.0025,
                "rsi": 68,
                "funding_rate": 0.00005,
                "trend_bias": 0.99,
                "trend_strength": 0.99,
                "news_risk": "none",
            }
        elif coin == "ETH":
            data = {
                "coin": "ETH",
                "price": 3260,
                "recent_high": 3225,
                "recent_low": 3090,
                "atr": 28,
                "rsi": 63,
                "funding_rate": 0.0002,
                "trend_bias": 0.84,
                "trend_strength": 0.79,
                "news_risk": "low",
            }
        else:
            data = {
                "coin": coin,
                "price": 70520,
                "recent_high": 70460,
                "recent_low": 69340,
                "atr": 60,
                "rsi": 64,
                "funding_rate": 0.0002,
                "trend_bias": 0.91,
                "trend_strength": 0.86,
                "news_risk": "none",
            }
        data.update(overrides)
        return data

    def position(self, side="long", **overrides):
        data = {
            "side": side,
            "entry_price": 69000,
            "notional_usd": 300,
            "unrealized_pnl": 40,
            "peak_unrealized_pnl": 50,
            "scale_ins_used": 0,
        }
        data.update(overrides)
        return data

    def test_invalid_negative_price_no_trade(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market(price=-1))
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["safe_result"], "no_trade")
        self.assertEqual(result["market_regime"], "invalid_data")

    def test_nan_market_data_no_trade(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market(price=float("nan")))
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["market_regime"], "invalid_data")

    def test_unknown_coin_rejected(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market(coin="ADA", price=1.2, recent_high=1.1, recent_low=1.0, atr=0.02))
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "unsupported_coin")

    def test_btc_open_long_contains_strategy_id(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"))
        self.assertEqual(result["action"], "open_long")
        self.assertIn("--strategy-id an25hlq1", result["hyperliquid_command_template"])

    def test_same_window_fade_predicate_is_structurally_unreachable_with_positive_breakout_buffer(self):
        for market in (
            self.breakout_market("BTC", price=70490, rsi=78, trend_bias=0.05, trend_strength=0.4),
            self.breakout_market("ETH", price=3210, rsi=81, trend_bias=0.1, trend_strength=0.35),
            self.breakout_market("DOGE", price=0.191, rsi=79, trend_bias=0.0, trend_strength=0.3),
        ):
            bands = compute_bands(market, self.bundle["config"])
            self.assertGreater(bands["upper_band"], market["recent_high"])
            self.assertFalse(market["recent_high"] > bands["upper_band"])

    def test_same_window_rebound_predicate_is_structurally_unreachable_with_positive_breakdown_buffer(self):
        for market in (
            self.breakout_market("BTC", price=69410, rsi=24, trend_bias=-0.05, trend_strength=0.4),
            self.breakout_market("ETH", price=3095, rsi=22, trend_bias=-0.1, trend_strength=0.35),
            self.breakout_market("DOGE", price=0.171, rsi=25, trend_bias=0.0, trend_strength=0.3),
        ):
            bands = compute_bands(market, self.bundle["config"])
            self.assertLess(bands["lower_band"], market["recent_low"])
            self.assertFalse(market["recent_low"] < bands["lower_band"])

    def test_failed_breakout_fade_short_can_open_short_with_explicit_probe_levels(self):
        market = self.breakout_market(
            "BTC",
            price=70410,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            rsi=80,
            funding_rate=0.0001,
            trend_bias=-0.15,
            trend_strength=0.82,
            prior_high=70460,
            probe_high=70600,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_short")
        self.assertEqual(result["market_regime"], "fade")

    def test_failed_breakdown_rebound_long_can_open_long_with_explicit_probe_levels(self):
        market = self.breakout_market(
            "BTC",
            price=69390,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            rsi=22,
            funding_rate=-0.0001,
            trend_bias=0.15,
            trend_strength=0.82,
            prior_low=69340,
            probe_low=69180,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_long")
        self.assertEqual(result["market_regime"], "rebound")

    def test_deeper_failed_breakout_ranks_above_shallower_failed_breakout(self):
        deeper = self.breakout_market(
            "BTC",
            price=70410,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            rsi=80,
            funding_rate=0.0001,
            trend_bias=-0.15,
            trend_strength=0.82,
            prior_high=70460,
            probe_high=70620,
        )
        shallower = self.breakout_market(
            "ETH",
            price=3212,
            recent_high=3225,
            recent_low=3090,
            atr=28,
            rsi=79,
            funding_rate=0.0001,
            trend_bias=-0.12,
            trend_strength=0.8,
            prior_high=3225,
            probe_high=3250,
        )
        scan = scan_markets(self.bundle, self.account(), [shallower, deeper])
        self.assertEqual(scan["top_opportunities"][0]["coin"], "BTC")
        self.assertGreater(scan["top_opportunities"][0]["signal_score"], scan["top_opportunities"][1]["signal_score"])

    def test_existing_position_with_zero_free_collateral_is_treated_as_defensive_hold(self):
        result = evaluate_market(
            self.bundle,
            self.account(free_collateral=0),
            self.breakout_market("BTC"),
            self.position(unrealized_pnl=50, peak_unrealized_pnl=50, notional_usd=120),
        )
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "hold_normalized")
        self.assertNotIn("hyperliquid_command_template", result)

    def test_open_long_exposes_band_and_component_diagnostics(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"))
        self.assertIn("upper_band", result)
        self.assertIn("lower_band", result)
        self.assertIn("component_scores", result)
        self.assertIn("trend", result["component_scores"])

    def test_eth_reduce_contains_strategy_id(self):
        market = self.breakout_market("ETH", rsi=80, funding_rate=0.001)
        position = self.position(entry_price=3180, unrealized_pnl=65, peak_unrealized_pnl=80)
        result = evaluate_market(self.bundle, self.account(), market, position)
        self.assertEqual(result["action"], "reduce")
        self.assertIn("--strategy-id an25hlq1", result["hyperliquid_command_template"])

    def test_close_all_contains_reduce_only(self):
        market = self.breakout_market("BTC")
        position = self.position(unrealized_pnl=-45, peak_unrealized_pnl=0)
        result = evaluate_market(self.bundle, self.account(), market, position)
        self.assertEqual(result["action"], "close_all")
        self.assertIn("--reduce-only", result["hyperliquid_command_template"])

    def test_position_risk_close_all_includes_loss_metric(self):
        market = self.breakout_market("BTC")
        position = self.position(unrealized_pnl=-45, peak_unrealized_pnl=0)
        result = evaluate_market(self.bundle, self.account(), market, position)
        self.assertEqual(result["market_regime"], "position_risk")
        self.assertIn("loss_pct_equity", result)
        self.assertGreater(result["loss_pct_equity"], 0)

    def test_profit_giveback_reduce_includes_giveback_metric(self):
        market = self.breakout_market("ETH", rsi=65, funding_rate=0.0002)
        position = self.position(entry_price=3180, unrealized_pnl=48, peak_unrealized_pnl=80)
        result = evaluate_market(self.bundle, self.account(), market, position)
        self.assertEqual(result["action"], "reduce")
        self.assertEqual(result["market_regime"], "profit_giveback")
        self.assertIn("profit_giveback_pct", result)
        self.assertGreaterEqual(result["profit_giveback_pct"], 0.35)

    def test_tier3_coin_position_size_reduced(self):
        btc = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"))
        doge = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("DOGE", price=0.6, recent_high=0.59, recent_low=0.54),
        )
        self.assertEqual(doge["action"], "open_long")
        self.assertLess(doge["notional_usd"], btc["notional_usd"])

    def test_failed_market_fetch_no_trade(self):
        market = self.breakout_market("BTC")
        del market["atr"]
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["market_regime"], "invalid_data")

    def test_fetch_public_market_data_parses_okx_public_responses(self):
        responses = [
            {
                "code": "0",
                "data": [{"instId": "BTC-USDT-SWAP", "markPx": "100.5", "ts": "1700000000000"}],
                "msg": "",
            },
            {
                "code": "0",
                "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.00012", "ts": "1700000000001"}],
                "msg": "",
            },
            {
                "code": "0",
                "data": [
                    ["1700000000000", "99", "101", "98", "100", "1", "1", "100", "1"],
                    ["1700003600000", "100", "102", "99", "101", "4", "1", "101", "1"],
                    ["1700007200000", "101", "103", "100", "102", "7", "1", "102", "1"],
                ],
                "msg": "",
            },
            {
                "code": "0",
                "data": [
                    ["1700000000000", "2500000000"],
                    ["1700003600000", "2550000000"],
                    ["1700007200000", "2650000000"],
                ],
                "msg": "",
            },
        ]
        with patch("market_fetcher._http_get_json", side_effect=responses):
            result = fetch_public_market_data("BTC")
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(result["source"], "okx_swap_public")
        self.assertEqual(result["price"], 100.5)
        self.assertEqual(result["funding"], 0.00012)
        self.assertEqual(len(result["candles_1h"]), 3)
        self.assertEqual(len(result["oi_history_1h"]), 3)
        self.assertEqual(result["oi_usd"], 2650000000.0)

    def test_build_market_from_fetch_derives_participation_metrics(self):
        fetch_result = {
            "ok": True,
            "coin": "BTC",
            "price": 102.0,
            "funding": 0.0001,
            "oi_usd": 2650000000.0,
            "oi_history_1h": [
                {"timestamp": 1700000000000, "oi_usd": 2500000000.0},
                {"timestamp": 1700003600000, "oi_usd": 2550000000.0},
                {"timestamp": 1700007200000, "oi_usd": 2650000000.0},
            ],
            "timestamp": 1700000000000,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 100.0, "low": 95.0, "close": 97.0, "volume": 1.0},
                {"high": 101.0, "low": 95.5, "close": 98.0, "volume": 1.2},
                {"high": 102.0, "low": 96.0, "close": 99.0, "volume": 1.4},
                {"high": 103.0, "low": 96.5, "close": 100.0, "volume": 1.6},
                {"high": 104.0, "low": 97.0, "close": 101.0, "volume": 2.2},
                {"high": 105.0, "low": 97.5, "close": 102.0, "volume": 3.8},
            ],
        }
        market, error = _build_market_from_fetch(fetch_result, "BTC")
        self.assertIsNone(error)
        self.assertGreater(market["volume_ratio"], 1.0)
        self.assertGreater(market["oi_change_ratio"], 0.0)
        self.assertGreater(market["participation_bias"], 0.0)

    def test_build_market_from_fetch_derives_probe_levels_for_reversal_setups(self):
        fetch_result = {
            "ok": True,
            "coin": "BTC",
            "price": 100.0,
            "funding": 0.0001,
            "timestamp": 1700000000000,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 100.0, "low": 95.0, "close": 97.0},
                {"high": 101.0, "low": 95.5, "close": 98.0},
                {"high": 102.0, "low": 96.0, "close": 99.0},
                {"high": 103.0, "low": 96.5, "close": 100.0},
                {"high": 104.0, "low": 97.0, "close": 101.0},
                {"high": 108.0, "low": 97.5, "close": 102.0},
                {"high": 105.0, "low": 94.0, "close": 99.5},
                {"high": 104.5, "low": 95.0, "close": 100.0},
            ],
        }
        market, error = _build_market_from_fetch(fetch_result, "BTC")
        self.assertIsNone(error)
        self.assertEqual(market["source"], "okx_swap_public")
        self.assertIn("prior_high", market)
        self.assertIn("prior_low", market)
        self.assertIn("probe_high", market)
        self.assertIn("probe_low", market)
        self.assertGreaterEqual(market["probe_high"], market["prior_high"])
        self.assertLessEqual(market["probe_low"], market["prior_low"])

    def test_fetch_market_trend_features_promote_breakout_candidate_over_old_sma_bias(self):
        fetch_result = {
            "ok": True,
            "coin": "BTC",
            "price": 104.95,
            "funding": 0.0001,
            "oi_usd": 2650000000.0,
            "oi_history_1h": [
                {"timestamp": 1, "oi_usd": 2500000000.0},
                {"timestamp": 2, "oi_usd": 2550000000.0},
                {"timestamp": 3, "oi_usd": 2650000000.0},
            ],
            "timestamp": 1,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 101.35, "low": 100.09, "close": 100.58, "volume": 1.0},
                {"high": 101.7, "low": 100.6, "close": 101.03, "volume": 1.1},
                {"high": 102.49, "low": 101.1, "close": 101.73, "volume": 1.2},
                {"high": 102.3, "low": 100.66, "close": 101.51, "volume": 1.15},
                {"high": 102.33, "low": 101.23, "close": 101.68, "volume": 1.2},
                {"high": 102.46, "low": 101.21, "close": 101.59, "volume": 1.18},
                {"high": 102.86, "low": 101.4, "close": 102.25, "volume": 1.22},
                {"high": 102.94, "low": 101.22, "close": 102.1, "volume": 1.24},
                {"high": 103.75, "low": 102.34, "close": 102.99, "volume": 1.35},
                {"high": 103.36, "low": 102.2, "close": 102.85, "volume": 1.4},
                {"high": 103.43, "low": 101.75, "close": 102.7, "volume": 1.38},
                {"high": 102.77, "low": 101.5, "close": 102.33, "volume": 1.32},
                {"high": 103.78, "low": 102.18, "close": 102.93, "volume": 1.55},
                {"high": 103.62, "low": 102.18, "close": 102.91, "volume": 1.62},
                {"high": 104.13, "low": 102.83, "close": 103.4, "volume": 1.78},
                {"high": 104.3, "low": 102.9, "close": 103.61, "volume": 2.2},
                {"high": 104.09, "low": 102.79, "close": 103.39, "volume": 2.6},
            ],
        }
        market, error = _build_market_from_fetch(fetch_result, "BTC")
        self.assertIsNone(error)
        self.assertGreaterEqual(market["trend_strength"], 0.55)
        self.assertGreater(market["trend_bias"], 0)
        self.assertGreater(market["participation_bias"], 0)
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["market_regime"], "breakout")
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "threshold_not_met")

    def test_fetch_market_breakout_entry_still_rejected_when_rsi_is_overheated(self):
        fetch_result = {
            "ok": True,
            "coin": "BTC",
            "price": 112.6,
            "funding": 0.0001,
            "oi_usd": 2750000000.0,
            "oi_history_1h": [
                {"timestamp": 1, "oi_usd": 2500000000.0},
                {"timestamp": 2, "oi_usd": 2620000000.0},
                {"timestamp": 3, "oi_usd": 2750000000.0},
            ],
            "timestamp": 1,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 100.8, "low": 99.2, "close": 100.0, "volume": 1.0},
                {"high": 102.0, "low": 100.0, "close": 101.4, "volume": 1.1},
                {"high": 101.8, "low": 100.6, "close": 101.0, "volume": 1.0},
                {"high": 103.4, "low": 101.0, "close": 102.8, "volume": 1.2},
                {"high": 103.0, "low": 101.8, "close": 102.2, "volume": 1.18},
                {"high": 104.6, "low": 102.0, "close": 104.0, "volume": 1.28},
                {"high": 104.4, "low": 103.0, "close": 103.6, "volume": 1.26},
                {"high": 106.0, "low": 103.4, "close": 105.2, "volume": 1.4},
                {"high": 105.8, "low": 104.2, "close": 105.0, "volume": 1.42},
                {"high": 107.4, "low": 104.8, "close": 106.8, "volume": 1.52},
                {"high": 107.0, "low": 105.8, "close": 106.2, "volume": 1.5},
                {"high": 108.8, "low": 106.0, "close": 108.0, "volume": 1.7},
                {"high": 108.4, "low": 107.0, "close": 107.8, "volume": 1.74},
                {"high": 110.2, "low": 107.6, "close": 109.4, "volume": 1.95},
                {"high": 110.0, "low": 108.6, "close": 109.0, "volume": 1.9},
                {"high": 111.8, "low": 108.8, "close": 111.0, "volume": 2.3},
            ],
        }
        market, error = _build_market_from_fetch(fetch_result, "BTC")
        self.assertIsNone(error)
        self.assertGreaterEqual(market["trend_strength"], 0.55)
        self.assertGreater(market["rsi"], self.bundle["config"]["thresholds"]["rsi_long_max"])
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["market_regime"], "range")

    def test_fetch_market_bearish_edge_is_not_flattened_to_range32(self):
        fetch_result = {
            "ok": True,
            "coin": "ETH",
            "price": 2317.77,
            "funding": -0.0001209814456434,
            "oi_usd": 1584215981.5857656,
            "oi_history_1h": [
                {"timestamp": 1, "oi_usd": 1695022364.3391643},
                {"timestamp": 2, "oi_usd": 1596312115.8738647},
                {"timestamp": 3, "oi_usd": 1586164811.9152546},
                {"timestamp": 4, "oi_usd": 1584215981.5857656},
            ],
            "timestamp": 1777290418234,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 2366.48, "low": 2358.34, "close": 2366.48, "volume": 1558729.05},
                {"high": 2371.95, "low": 2361.08, "close": 2361.24, "volume": 605276.99},
                {"high": 2369.87, "low": 2345.01, "close": 2363.09, "volume": 1416870.1},
                {"high": 2379.99, "low": 2351.62, "close": 2360.26, "volume": 1781760.54},
                {"high": 2378.68, "low": 2355.5, "close": 2368.22, "volume": 1282763.89},
                {"high": 2392.0, "low": 2356.32, "close": 2389.0, "volume": 2179486.5},
                {"high": 2404.0, "low": 2376.25, "close": 2393.62, "volume": 2925961.61},
                {"high": 2395.77, "low": 2385.56, "close": 2390.88, "volume": 837718.5},
                {"high": 2399.99, "low": 2389.22, "close": 2393.56, "volume": 1018418.72},
                {"high": 2394.92, "low": 2379.59, "close": 2382.05, "volume": 1207315.18},
                {"high": 2382.71, "low": 2316.84, "close": 2321.94, "volume": 7855181.5},
                {"high": 2327.78, "low": 2310.57, "close": 2319.45, "volume": 2062900.79},
                {"high": 2322.28, "low": 2313.48, "close": 2314.58, "volume": 912070.52},
                {"high": 2318.58, "low": 2307.47, "close": 2318.57, "volume": 1431885.29},
                {"high": 2324.43, "low": 2314.0, "close": 2320.73, "volume": 867193.02},
                {"high": 2322.92, "low": 2316.83, "close": 2318.51, "volume": 406774.24},
                {"high": 2321.3, "low": 2315.35, "close": 2317.81, "volume": 286755.66},
            ],
        }
        market, error = _build_market_from_fetch(fetch_result, "ETH")
        self.assertIsNone(error)
        self.assertLess(market["participation_bias"], 0)
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "threshold_not_met")
        self.assertEqual(result["market_regime"], "breakdown")
        self.assertGreaterEqual(result["signal_score"], 50)

    def test_edge_breakout_uses_breakout_multiplier_not_breakdown_multiplier(self):
        config = json.loads(json.dumps(self.bundle["config"]))
        config["band_params"]["breakout_mult"] = 0.1
        config["band_params"]["breakdown_mult"] = 0.8
        custom_bundle = {
            "config": config,
            "coins": self.bundle["coins"],
            "tiers": self.bundle["tiers"],
        }
        result = evaluate_market(
            custom_bundle,
            self.account(),
            self.breakout_market(
                "BTC",
                price=70350,
                trend_bias=0.91,
                trend_strength=0.86,
                rsi=64,
                funding_rate=0.0002,
                news_risk="none",
            ),
        )
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["market_regime"], "range")
        self.assertEqual(result["signal_score"], 32)

    def test_breakout_with_rising_open_interest_and_volume_clears_entry_threshold(self):
        market = self.breakout_market(
            "BTC",
            trend_bias=0.92,
            trend_strength=0.88,
            funding_rate=0.0001,
            rsi=63,
            news_risk="none",
            volume_ratio=1.75,
            oi_change_ratio=0.12,
            participation_bias=0.7,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_long")
        self.assertIn("participation", result["component_scores"])
        self.assertGreater(result["component_scores"]["participation"], 0)

    def test_breakout_without_participation_confirmation_stays_below_entry_threshold(self):
        market = self.breakout_market(
            "BTC",
            trend_bias=0.92,
            trend_strength=0.88,
            funding_rate=0.0001,
            rsi=63,
            news_risk="none",
            volume_ratio=0.65,
            oi_change_ratio=-0.08,
            participation_bias=-0.7,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "threshold_not_met")
        self.assertIn("participation", result["component_scores"])
        self.assertLess(result["component_scores"]["participation"], 6)

    def test_breakdown_with_rising_open_interest_and_volume_clears_entry_threshold(self):
        market = self.breakout_market(
            "BTC",
            price=69320,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            trend_bias=-0.96,
            trend_strength=0.92,
            funding_rate=0.0001,
            rsi=33,
            news_risk="none",
            volume_ratio=1.75,
            oi_change_ratio=0.12,
            participation_bias=0.7,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_short")
        self.assertEqual(result["market_regime"], "breakdown")
        self.assertGreater(result["component_scores"]["participation"], 0)

    def test_breakdown_without_participation_confirmation_stays_below_entry_threshold(self):
        market = self.breakout_market(
            "BTC",
            price=69320,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            trend_bias=-0.96,
            trend_strength=0.92,
            funding_rate=0.0001,
            rsi=33,
            news_risk="none",
            volume_ratio=0.65,
            oi_change_ratio=-0.08,
            participation_bias=-0.7,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "threshold_not_met")
        self.assertEqual(result["market_regime"], "breakdown")
        self.assertLess(result["component_scores"]["participation"], 0)

    def test_nan_participation_inputs_are_neutralized_instead_of_boosting_signal(self):
        baseline = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market(
                "BTC",
                trend_bias=0.92,
                trend_strength=0.88,
                funding_rate=0.0001,
                rsi=63,
                news_risk="none",
            ),
        )
        corrupted = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market(
                "BTC",
                trend_bias=0.92,
                trend_strength=0.88,
                funding_rate=0.0001,
                rsi=63,
                news_risk="none",
                volume_ratio=float("nan"),
                oi_change_ratio=float("nan"),
                participation_bias=float("nan"),
            ),
        )
        self.assertEqual(corrupted["component_scores"]["participation"], 0.0)
        self.assertEqual(corrupted["signal_score"], baseline["signal_score"])

    def test_breakout_with_moderately_hot_rsi_still_can_open_when_technical_signal_is_strong(self):
        market = self.breakout_market(
            "BTC",
            price=70580,
            trend_bias=0.96,
            trend_strength=0.92,
            funding_rate=0.0001,
            rsi=72,
            news_risk="none",
            volume_ratio=1.95,
            oi_change_ratio=0.14,
            participation_bias=0.85,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_long")
        self.assertEqual(result["market_regime"], "breakout")

    def test_breakout_with_high_news_risk_can_still_open_when_technical_signal_is_strong(self):
        market = self.breakout_market(
            "BTC",
            price=70540,
            trend_bias=0.97,
            trend_strength=0.95,
            funding_rate=0.0001,
            rsi=64,
            news_risk="high",
            volume_ratio=2.0,
            oi_change_ratio=0.14,
            participation_bias=0.9,
        )
        result = evaluate_market(self.bundle, self.account(), market)
        self.assertEqual(result["action"], "open_long")
        self.assertLess(result["notional_usd"], 300)

    def test_signal_score_range(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"))
        self.assertGreaterEqual(result["signal_score"], 0)
        self.assertLessEqual(result["signal_score"], 100)

    def test_supported_coin_scan(self):
        scan = scan_markets(
            self.bundle,
            self.account(),
            [self.breakout_market("BTC"), self.breakout_market("ETH"), self.breakout_market("DOGE")],
        )
        self.assertTrue(len(scan["top_opportunities"]) >= 1)
        self.assertIn(scan["top_opportunities"][0]["coin"], {"BTC", "ETH", "DOGE"})

    def test_scan_markets_returns_results_sorted_by_action_priority_and_signal_score(self):
        deeper = self.breakout_market(
            "BTC",
            price=70410,
            recent_high=70460,
            recent_low=69340,
            atr=60,
            rsi=80,
            funding_rate=0.0001,
            trend_bias=-0.15,
            trend_strength=0.82,
            prior_high=70460,
            probe_high=70620,
        )
        shallower = self.breakout_market(
            "ETH",
            price=3212,
            recent_high=3225,
            recent_low=3090,
            atr=28,
            rsi=79,
            funding_rate=0.0001,
            trend_bias=-0.12,
            trend_strength=0.8,
            prior_high=3225,
            probe_high=3250,
        )
        scan = scan_markets(self.bundle, self.account(), [shallower, deeper])
        self.assertEqual([item["coin"] for item in scan["results"][:2]], ["BTC", "ETH"])

    def test_every_live_command_contains_strategy_id(self):
        open_result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"))
        reduce_result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("ETH", rsi=80, funding_rate=0.001),
            self.position(entry_price=3180),
        )
        close_result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"), self.position(unrealized_pnl=-45, peak_unrealized_pnl=0))
        scale_result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC"), self.position(unrealized_pnl=50, peak_unrealized_pnl=50, notional_usd=120, trend_bias=0.91, trend_strength=0.86))
        for result in (open_result, reduce_result, close_result, scale_result):
            self.assertIn("--strategy-id an25hlq1", result["hyperliquid_command_template"])

    def test_non_whitelist_coin_observe_or_reject(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market(coin="PEPE", price=0.00001, recent_high=0.000009, recent_low=0.000008, atr=0.0000005))
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "unsupported_coin")

    def test_rsi_out_of_range_no_trade(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC", rsi=101))
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["market_regime"], "invalid_data")

    def test_fraction_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            build_close_command("ETH", 1.5, "adaptive-hyperliquid-strategy")

    def test_session_loss_limit_blocks_new_entries(self):
        result = evaluate_market(self.bundle, self.account(session_loss_pct=0.07), self.breakout_market("BTC"))
        self.assertEqual(result["action"], "halt_new_entries")
        self.assertNotIn("hyperliquid_command_template", result)

    def test_max_actions_per_session_blocks_scalein(self):
        result = evaluate_market(
            self.bundle,
            self.account(session_actions=3),
            self.breakout_market("BTC"),
            self.position(unrealized_pnl=55, peak_unrealized_pnl=55, notional_usd=120),
        )
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "hold_normalized")

    def test_extreme_news_risk_halts_new_entries_but_allows_reduce(self):
        halted = evaluate_market(self.bundle, self.account(), self.breakout_market("BTC", news_risk="extreme"))
        self.assertEqual(halted["action"], "halt_new_entries")
        reduced = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("ETH", news_risk="extreme", rsi=80, funding_rate=0.001),
            self.position(entry_price=3180, unrealized_pnl=65, peak_unrealized_pnl=80),
        )
        self.assertEqual(reduced["action"], "reduce")

    def test_profitable_long_is_not_forced_to_reduce_on_single_mild_overheat_signal(self):
        result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC", rsi=75, funding_rate=0.0002),
            self.position(unrealized_pnl=35, peak_unrealized_pnl=35, notional_usd=300, scale_ins_used=1),
        )
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "hold_normalized")

    def test_hold_is_normalized_to_observe(self):
        result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC", trend_bias=0.3, trend_strength=0.35, price=70000, recent_high=70500, recent_low=69400, rsi=55),
            self.position(unrealized_pnl=10, peak_unrealized_pnl=12, notional_usd=300, scale_ins_used=1),
        )
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "hold_normalized")

    def test_non_whitelist_coin_has_no_command_template(self):
        result = evaluate_market(self.bundle, self.account(), self.breakout_market(coin="ARB", price=2.1, recent_high=2.0, recent_low=1.8, atr=0.05))
        self.assertNotIn("hyperliquid_command_template", result)

    def test_scale_in_respects_per_trade_ratio_max(self):
        result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC"),
            self.position(unrealized_pnl=60, peak_unrealized_pnl=60, notional_usd=780),
        )
        self.assertEqual(result["action"], "scale_in")
        self.assertLessEqual(result["notional_usd"], 20)

    def test_scale_in_includes_post_scale_notional(self):
        result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC"),
            self.position(unrealized_pnl=50, peak_unrealized_pnl=50, notional_usd=120),
        )
        self.assertEqual(result["action"], "scale_in")
        self.assertIn("current_notional_usd", result)
        self.assertIn("post_scale_in_notional_usd", result)
        self.assertAlmostEqual(
            result["post_scale_in_notional_usd"],
            result["current_notional_usd"] + result["notional_usd"],
        )

    def test_scale_in_below_min_notional_is_blocked_to_observe(self):
        result = evaluate_market(
            self.bundle,
            self.account(free_collateral=5),
            self.breakout_market("BTC"),
            self.position(unrealized_pnl=50, peak_unrealized_pnl=50, notional_usd=120),
        )
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["safe_result"], "hold_normalized")
        self.assertNotIn("hyperliquid_command_template", result)

    def test_wider_stop_distance_reduces_open_notional_for_same_market_and_account(self):
        tighter = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC", atr=30),
        )
        wider = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC", atr=60),
        )
        self.assertEqual(tighter["action"], "open_long")
        self.assertEqual(wider["action"], "open_long")
        self.assertLess(wider["notional_usd"], tighter["notional_usd"])
        self.assertIn("stop_distance_usd", tighter["sizing_reason"])
        self.assertIn("risk_budget_usd", tighter["sizing_reason"])

    def test_risk_based_sizing_reason_includes_stop_ratio_diagnostics(self):
        result = evaluate_market(
            self.bundle,
            self.account(),
            self.breakout_market("BTC", atr=60),
        )
        self.assertEqual(result["action"], "open_long")
        self.assertIn("stop_distance_usd", result["sizing_reason"])
        self.assertIn("stop_distance_ratio", result["sizing_reason"])
        self.assertIn("volatility_scalar", result["sizing_reason"])

    def test_scan_requested_markets_prefers_payload_markets_when_no_coin_arg(self):
        payload = {
            "markets": [
                {"coin": "BTC", "price": 1},
                {"coin": "ETH", "price": 2},
            ]
        }
        result = _scan_requested_markets(payload, self.bundle["coins"], None)
        self.assertEqual(result, payload["markets"])

    def test_cmd_scan_fetch_market_prefers_payload_markets_over_enabled_coin_list(self):
        payload = {
            "account": self.account(),
            "markets": [
                {"coin": "BTC"},
                {"coin": "ETH"},
            ],
            "positions": {},
        }
        fetch_calls = []

        def fake_fetch(coin):
            fetch_calls.append(coin)
            return {
                "ok": False,
                "coin": coin,
                "reason": "market_fetch_not_supported_for_coin",
            }

        args = argparse.Namespace(
            config=str(ROOT / "config" / "default.json"),
            input=str(ROOT / "examples" / "scan_input.json"),
            fetch_market=True,
            coins=None,
        )

        with patch("adaptive_hyperliquid_strategy.load_bundle", return_value=self.bundle), patch(
            "adaptive_hyperliquid_strategy.load_json", return_value=payload
        ), patch("adaptive_hyperliquid_strategy.fetch_public_market_data", side_effect=fake_fetch):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cmd_scan(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch_calls, ["BTC", "ETH"])
        result = json.loads(stdout.getvalue())
        self.assertEqual([item["coin"] for item in result["results"]], ["BTC", "ETH"])

    def test_cmd_scan_fetch_market_sorts_results_by_action_priority_and_signal_score(self):
        payload = {
            "account": self.account(),
            "markets": [
                {"coin": "BTC"},
                {"coin": "ETH"},
                {"coin": "DOGE"},
            ],
            "positions": {},
        }

        fake_results = {
            "BTC": {
                "coin": "BTC",
                "action": "observe",
                "signal_score": 88,
                "safe_result": "threshold_not_met",
            },
            "ETH": {
                "coin": "ETH",
                "action": "open_long",
                "signal_score": 76,
                "safe_result": "tradable",
            },
            "DOGE": {
                "coin": "DOGE",
                "action": "open_long",
                "signal_score": 91,
                "safe_result": "tradable",
            },
        }

        args = argparse.Namespace(
            config=str(ROOT / "config" / "default.json"),
            input=str(ROOT / "examples" / "scan_input.json"),
            fetch_market=True,
            coins=None,
        )

        def fake_fetch(coin):
            return {
                "ok": True,
                "coin": coin,
                "price": 1.0,
                "funding": 0.0,
                "candles_1h": [
                    {"high": 1.1, "low": 0.9, "close": 1.0},
                    {"high": 1.1, "low": 0.9, "close": 1.0},
                    {"high": 1.1, "low": 0.9, "close": 1.0},
                ],
                "timestamp": 1,
                "source": "okx_swap_public",
            }

        def fake_scan_markets(bundle, account, markets, positions):
            return {
                "strategy_id": bundle["config"]["strategy_id"],
                "dry_run": True,
                "results": [fake_results[market["coin"]] for market in markets],
                "top_opportunities": [],
            }

        with patch("adaptive_hyperliquid_strategy.load_bundle", return_value=self.bundle), patch(
            "adaptive_hyperliquid_strategy.load_json", return_value=payload
        ), patch("adaptive_hyperliquid_strategy.fetch_public_market_data", side_effect=fake_fetch), patch(
            "adaptive_hyperliquid_strategy.scan_markets", side_effect=fake_scan_markets
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cmd_scan(args)

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual([item["coin"] for item in result["results"]], ["DOGE", "ETH", "BTC"])
        self.assertEqual([item["coin"] for item in result["top_opportunities"]], ["DOGE", "ETH"])
        self.assertTrue(all(item["action"] in {"open_long", "open_short", "scale_in", "reduce", "close_all"} for item in result["top_opportunities"]))
    def test_cmd_evaluate_fetch_market_can_write_audit_file(self):
        payload = {
            "account": self.account(),
            "market": {"coin": "BTC"},
        }
        args = argparse.Namespace(
            config=str(ROOT / "config" / "default.json"),
            input=str(ROOT / "examples" / "single_evaluate_input.json"),
            fetch_market=True,
            audit_output=str(ROOT / "tmp-evaluate-audit.json"),
        )
        fetch_result = {
            "ok": True,
            "coin": "BTC",
            "price": 100.0,
            "funding": 0.0001,
            "oi_usd": 2650000000.0,
            "oi_history_1h": [
                {"timestamp": 1, "oi_usd": 2500000000.0},
                {"timestamp": 2, "oi_usd": 2550000000.0},
                {"timestamp": 3, "oi_usd": 2650000000.0},
            ],
            "timestamp": 1,
            "source": "okx_swap_public",
            "candles_1h": [
                {"high": 100.0, "low": 95.0, "close": 97.0, "volume": 1.0},
                {"high": 101.0, "low": 95.5, "close": 98.0, "volume": 1.2},
                {"high": 102.0, "low": 96.0, "close": 99.0, "volume": 1.4},
                {"high": 103.0, "low": 96.5, "close": 100.0, "volume": 1.6},
                {"high": 104.0, "low": 97.0, "close": 101.0, "volume": 2.2},
                {"high": 105.0, "low": 97.5, "close": 102.0, "volume": 3.8},
            ],
        }
        audit_captures = []

        def fake_write_text(self, content, encoding="utf-8"):
            audit_captures.append({"path": str(self), "content": content, "encoding": encoding})
            return len(content)

        with patch("adaptive_hyperliquid_strategy.load_bundle", return_value=self.bundle), patch(
            "adaptive_hyperliquid_strategy.load_json", return_value=payload
        ), patch("adaptive_hyperliquid_strategy.fetch_public_market_data", return_value=fetch_result), patch(
            "pathlib.Path.write_text", new=fake_write_text
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cmd_evaluate(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(audit_captures), 1)
        self.assertTrue(audit_captures[0]["path"].endswith("tmp-evaluate-audit.json"))
        audit_payload = json.loads(audit_captures[0]["content"])
        self.assertEqual(audit_payload["mode"], "evaluate")
        self.assertEqual(audit_payload["coin"], "BTC")
        self.assertIn("fetch_result", audit_payload)
        self.assertIn("derived_market", audit_payload)
        self.assertIn("result", audit_payload)

    def test_cmd_scan_fetch_market_can_write_audit_file(self):
        payload = {
            "account": self.account(),
            "markets": [
                {"coin": "BTC"},
                {"coin": "ETH"},
            ],
            "positions": {},
        }
        args = argparse.Namespace(
            config=str(ROOT / "config" / "default.json"),
            input=str(ROOT / "examples" / "scan_input.json"),
            fetch_market=True,
            coins=None,
            audit_output=str(ROOT / "tmp-scan-audit.json"),
        )

        def fake_fetch(coin):
            if coin == "BTC":
                return {
                    "ok": True,
                    "coin": coin,
                    "price": 100.0,
                    "funding": 0.0001,
                    "oi_usd": 2650000000.0,
                    "oi_history_1h": [
                        {"timestamp": 1, "oi_usd": 2500000000.0},
                        {"timestamp": 2, "oi_usd": 2550000000.0},
                        {"timestamp": 3, "oi_usd": 2650000000.0},
                    ],
                    "timestamp": 1,
                    "source": "okx_swap_public",
                    "candles_1h": [
                        {"high": 100.0, "low": 95.0, "close": 97.0, "volume": 1.0},
                        {"high": 101.0, "low": 95.5, "close": 98.0, "volume": 1.2},
                        {"high": 102.0, "low": 96.0, "close": 99.0, "volume": 1.4},
                        {"high": 103.0, "low": 96.5, "close": 100.0, "volume": 1.6},
                        {"high": 104.0, "low": 97.0, "close": 101.0, "volume": 2.2},
                        {"high": 105.0, "low": 97.5, "close": 102.0, "volume": 3.8},
                    ],
                }
            return {
                "ok": False,
                "coin": coin,
                "reason": "market_fetch_not_supported_for_coin",
            }

        audit_captures = []

        def fake_write_text(self, content, encoding="utf-8"):
            audit_captures.append({"path": str(self), "content": content, "encoding": encoding})
            return len(content)

        with patch("adaptive_hyperliquid_strategy.load_bundle", return_value=self.bundle), patch(
            "adaptive_hyperliquid_strategy.load_json", return_value=payload
        ), patch("adaptive_hyperliquid_strategy.fetch_public_market_data", side_effect=fake_fetch), patch(
            "pathlib.Path.write_text", new=fake_write_text
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = cmd_scan(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(audit_captures), 1)
        self.assertTrue(audit_captures[0]["path"].endswith("tmp-scan-audit.json"))
        audit_payload = json.loads(audit_captures[0]["content"])
        self.assertEqual(audit_payload["mode"], "scan")
        self.assertEqual([item["coin"] for item in audit_payload["coins"]], ["BTC", "ETH"])
        self.assertEqual(audit_payload["coins"][0]["fetch_status"], "ok")
        self.assertEqual(audit_payload["coins"][1]["fetch_status"], "error")
        self.assertIn("derived_market", audit_payload["coins"][0])
        self.assertIn("result", audit_payload["coins"][0])
        self.assertEqual(audit_payload["coins"][1]["fetch_error"], "market_fetch_not_supported_for_coin")


if __name__ == "__main__":
    unittest.main()
