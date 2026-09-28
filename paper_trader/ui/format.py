"""Presentation-only formatting helpers (money, percentages, shares, times).

Kept in the UI layer because these are display concerns; the core stores raw
floats. All functions are pure and side-effect free.
"""

from __future__ import annotations

from datetime import datetime


def _settle(value: float, decimals: int) -> float:
    """Collapse anything that rounds to zero — including float negative zero,
    which ``12 * 421.63 - 5059.56`` produces — to a plain ``0.0``, so nothing
    ever reads "$-0.00" or "+-0.00%"."""
    return 0.0 if round(value, decimals) == 0 else value


def fmt_money(value: float | None, decimals: int = 2) -> str:
    """Format an unsigned dollar amount: ``$1,234.56`` (negatives get ``-$``)."""
    if value is None:
        return "—"
    value = _settle(value, decimals)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def fmt_signed_money(value: float | None, decimals: int = 2) -> str:
    """Always show a sign: ``+$12.00`` / ``-$12.00`` / ``$0.00``."""
    if value is None:
        return "—"
    value = _settle(value, decimals)
    if value > 0:
        return f"+${value:,.{decimals}f}"
    if value < 0:
        return f"-${abs(value):,.{decimals}f}"
    return f"${value:,.{decimals}f}"


def fmt_price(value: float | None) -> str:
    """Price with adaptive precision (sub-dollar prices show more decimals)."""
    if value is None:
        return "—"
    if 0 < abs(value) < 1:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    value = _settle(value, decimals)
    return f"{value:.{decimals}f}%"


def fmt_signed_pct(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    raw = value
    value = _settle(value, decimals)
    if value == 0 and raw != 0:
        # A move too small to show still has a direction. Without this a
        # -$1.20 loss on a $25k book read "(+0.00%)" beside its own dollars.
        # Show one more digit so it reads as small, not as a sign on zero.
        finer = _settle(raw, decimals + 1)
        if finer != 0:
            return f"{finer:+.{decimals + 1}f}%"
        return f"{'-' if raw < 0 else '+'}{0:.{decimals}f}%"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def fmt_shares(qty: float | None) -> str:
    """Trim trailing zeros from a fractional share count: ``1.5``, ``0.125``, ``10``."""
    if qty is None:
        return "—"
    text = f"{qty:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def fmt_compact(value: float | None) -> str:
    """Abbreviate large numbers: 12.3K, 4.5M, 6.7B, 1.2T."""
    if value is None:
        return "—"
    n = float(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= threshold:
            return f"{n / threshold:.2f}{suffix}"
    return f"{n:,.0f}"


def fmt_time(dt: datetime) -> str:
    """Local wall-clock time, e.g. ``14:32:07``."""
    return dt.astimezone().strftime("%H:%M:%S")


def fmt_datetime(dt: datetime) -> str:
    """Local short date+time, e.g. ``Aug 08, 14:32``."""
    return dt.astimezone().strftime("%b %d, %H:%M")


def fmt_datetime_full(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
