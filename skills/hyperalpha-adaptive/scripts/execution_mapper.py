from __future__ import annotations

from typing import Optional


def _format_number(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def validate_fraction(fraction: float) -> None:
    if not isinstance(fraction, (int, float)):
        raise ValueError("fraction must be numeric")
    if fraction <= 0 or fraction > 1:
        raise ValueError("fraction must be in the range (0, 1]")


def build_order_command(coin: str, side: str, size: float, strategy_id: str) -> str:
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if not isinstance(size, (int, float)) or size <= 0:
        raise ValueError("size must be positive")
    return (
        f"hyperliquid-plugin order --coin {coin.upper()} --side {side} "
        f"--size {_format_number(float(size))} --strategy-id {strategy_id}"
    )


def build_close_command(coin: str, fraction: float, strategy_id: str) -> str:
    validate_fraction(fraction)
    return (
        f"hyperliquid-plugin close --coin {coin.upper()} --fraction {_format_number(float(fraction))} "
        f"--reduce-only --strategy-id {strategy_id}"
    )


def attach_execution_fields(result: dict, command_template: Optional[str], must_execute_via: str) -> dict:
    if command_template:
        result["must_execute_via"] = must_execute_via
        result["hyperliquid_command_template"] = command_template
    return result
