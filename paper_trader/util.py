"""Small numeric helpers shared across layers.

Money is represented as ``float`` throughout (market prices arrive as floats and
feed straight into numpy/pyqtgraph). To keep arithmetic honest we round at every
boundary using :func:`round_money` and :func:`round_shares` rather than trusting
raw binary-float results. Values are rounded half-to-even via Python's ``round``.
"""

from __future__ import annotations

from .config import MONEY_DECIMALS, SHARE_DECIMALS


def round_money(value: float) -> float:
    """Round a monetary amount to cents."""
    return round(float(value), MONEY_DECIMALS)


def round_shares(qty: float) -> float:
    """Round a share quantity to the supported fractional precision."""
    return round(float(qty), SHARE_DECIMALS)


def is_effectively_zero_shares(qty: float) -> bool:
    """True if ``qty`` is zero once rounded to share precision.

    Used when closing positions so a residual like 1e-12 doesn't linger.
    """
    return abs(round_shares(qty)) < 10 ** (-SHARE_DECIMALS)


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide, returning ``default`` when the denominator is zero."""
    return numerator / denominator if denominator else default
