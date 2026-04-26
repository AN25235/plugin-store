from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

OKX_PUBLIC_BASE = "https://www.okx.com"
SUPPORTED_COINS = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LINK", "AVAX"}
UNSUPPORTED_PUBLIC_FETCH = {"HYPE"}
DEFAULT_TIMEOUT = 10
CANDLE_LIMIT = 24
OI_HISTORY_LIMIT = 24
ONE_HOUR_MS = 60 * 60 * 1000


class MarketFetchError(Exception):
    """Raised when public market data cannot be fetched safely."""


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _symbol_for_coin(coin: str) -> str:
    return f"{coin.upper()}-USDT-SWAP"


def _http_get_json(path: str, params: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{OKX_PUBLIC_BASE}{path}?{query}" if query else f"{OKX_PUBLIC_BASE}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "HermesMarketFetcher/1.0"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _okx_data_rows(payload: Any) -> List[Any]:
    if not isinstance(payload, dict):
        raise MarketFetchError("market_data_missing")
    if payload.get("code") != "0":
        raise MarketFetchError("market_data_missing")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise MarketFetchError("market_data_missing")
    return rows


def _to_positive_float(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketFetchError(f"{field}_missing_or_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise MarketFetchError(f"{field}_missing_or_invalid")
    return number


def _to_finite_float(value: Any, field: str, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketFetchError(f"{field}_missing_or_invalid") from exc
    if not math.isfinite(number):
        raise MarketFetchError(f"{field}_missing_or_invalid")
    return number


def _to_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MarketFetchError(f"{field}_missing_or_invalid")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MarketFetchError(f"{field}_missing_or_invalid") from exc


def _normalize_candle(row: List[Any]) -> Dict[str, Any]:
    if not isinstance(row, list) or len(row) < 5:
        raise MarketFetchError("candles_missing_or_invalid")
    open_time = _to_int(row[0], "candle_open_time")
    open_price = _to_positive_float(row[1], "candle_open")
    high_price = _to_positive_float(row[2], "candle_high")
    low_price = _to_positive_float(row[3], "candle_low")
    close_price = _to_positive_float(row[4], "candle_close")
    volume = _to_finite_float(row[5] if len(row) > 5 else None, "candle_volume", default=0.0)
    if low_price > high_price:
        raise MarketFetchError("candles_missing_or_invalid")
    return {
        "open_time": open_time,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "close_time": open_time + ONE_HOUR_MS,
    }


def _normalize_oi_history_row(row: List[Any]) -> Dict[str, Any]:
    if not isinstance(row, list) or len(row) < 2:
        raise MarketFetchError("open_interest_missing_or_invalid")
    timestamp = _to_int(row[0], "oi_timestamp")
    oi_usd = _to_positive_float(row[-1], "oi_usd")
    return {
        "timestamp": timestamp,
        "oi_usd": oi_usd,
    }


def _request_failed_result(coin: str, reason: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "coin": coin,
        "input_state": "no_trade",
        "safe_result": "no_trade",
        "reason": reason,
    }


def fetch_public_market_data(coin: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    normalized_coin = (coin or "").strip().upper()
    if not normalized_coin:
        return _request_failed_result("", "no_trade_input_state")
    if normalized_coin in UNSUPPORTED_PUBLIC_FETCH:
        return {
            "ok": False,
            "reason": "market_fetch_not_supported_for_coin",
            "coin": normalized_coin,
        }
    if normalized_coin not in SUPPORTED_COINS:
        return _request_failed_result(normalized_coin, "no_trade_input_state")

    symbol = _symbol_for_coin(normalized_coin)
    try:
        mark_rows = _okx_data_rows(
            _http_get_json("/api/v5/public/mark-price", {"instType": "SWAP", "instId": symbol}, timeout=timeout)
        )
        funding_rows = _okx_data_rows(
            _http_get_json("/api/v5/public/funding-rate", {"instId": symbol}, timeout=timeout)
        )
        candle_rows = _okx_data_rows(
            _http_get_json(
                "/api/v5/market/history-candles",
                {"instId": symbol, "bar": "1H", "limit": CANDLE_LIMIT},
                timeout=timeout,
            )
        )
        oi_rows = _okx_data_rows(
            _http_get_json(
                "/api/v5/rubik/stat/contracts/open-interest-history",
                {"instId": symbol, "period": "1H", "limit": OI_HISTORY_LIMIT},
                timeout=timeout,
            )
        )

        mark = mark_rows[0]
        funding_info = funding_rows[0]
        if not isinstance(mark, dict) or not isinstance(funding_info, dict):
            raise MarketFetchError("market_data_missing")

        price = _to_positive_float(mark.get("markPx"), "price")
        funding = _to_finite_float(funding_info.get("fundingRate"), "funding", default=0.0)
        timestamp = _to_int(mark.get("ts") or funding_info.get("ts"), "timestamp")
        candles = sorted((_normalize_candle(row) for row in candle_rows), key=lambda candle: candle["open_time"])
        if not candles:
            raise MarketFetchError("market_data_missing")
        oi_history = sorted((_normalize_oi_history_row(row) for row in oi_rows), key=lambda row: row["timestamp"])
        if not oi_history:
            raise MarketFetchError("open_interest_missing_or_invalid")

        return {
            "ok": True,
            "coin": normalized_coin,
            "symbol": symbol,
            "source": "okx_swap_public",
            "price": price,
            "funding": funding,
            "candles_1h": candles,
            "oi_history_1h": oi_history,
            "oi_usd": oi_history[-1]["oi_usd"],
            "timestamp": timestamp,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return _request_failed_result(normalized_coin, "no_trade_input_state")
    except (json.JSONDecodeError, MarketFetchError):
        return _request_failed_result(normalized_coin, "no_trade_input_state")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch public OKX swap market data")
    parser.add_argument("coin", help="Coin symbol, e.g. BTC")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    print(json.dumps(fetch_public_market_data(args.coin, timeout=args.timeout), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
