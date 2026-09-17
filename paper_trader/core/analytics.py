"""Performance analytics — pure functions over a Session.

Computes portfolio statistics from the recorded equity curve and trade log:
total return, annualised volatility and Sharpe ratio (from *daily* returns),
maximum drawdown, and win/loss trade statistics.

Time-series metrics need at least two distinct trading days of equity data; when
there isn't enough yet the relevant fields are ``None`` and ``note`` explains
why. This is deliberately honest — a Sharpe ratio computed from a single
afternoon of ticks would be meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from .models import Session, Side

TRADING_DAYS_PER_YEAR = 252


@dataclass(slots=True)
class AnalyticsReport:
    days: int
    total_return: float | None
    total_return_pct: float | None
    annualized_volatility_pct: float | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
    best_day_pct: float | None
    worst_day_pct: float | None
    num_trades: int
    num_buys: int
    num_sells: int
    realized_pl: float
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    daily_returns: list[tuple[date, float]] = field(default_factory=list)
    note: str = ""


def _daily_equity(session: Session) -> list[tuple[date, float]]:
    """Collapse the equity curve to one (date, last value) point per day."""
    by_day: dict[date, float] = {}
    for point in session.equity_curve:
        by_day[point.time.astimezone().date()] = point.value  # last write wins
    return sorted(by_day.items())


def compute_analytics(session: Session, risk_free_rate: float = 0.0) -> AnalyticsReport:
    """Return a full :class:`AnalyticsReport` for ``session``.

    ``risk_free_rate`` is an *annual* rate (e.g. 0.04 for 4%) used for the Sharpe
    excess return; it defaults to 0.
    """
    # ---- trade statistics (always available) ---------------------------- #
    trades = session.trades
    num_buys = sum(1 for t in trades if t.side is Side.BUY)
    num_sells = sum(1 for t in trades if t.side is Side.SELL)
    # Any trade that booked P/L counts as a closing trade — including option
    # buy-to-close and assignment, which are BUYs but still realise a result.
    realized = [t.realized_pl for t in trades if t.realized_pl]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    win_rate = (len(wins) / len(realized) * 100.0) if realized else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None

    # ---- time-series metrics (need >= 2 daily points) ------------------- #
    daily = _daily_equity(session)
    total_return = total_return_pct = None
    if daily and session.starting_balance:
        total_return = daily[-1][1] - session.starting_balance
        total_return_pct = total_return / session.starting_balance * 100.0

    vol_pct = sharpe = max_dd_pct = best_pct = worst_pct = None
    daily_returns: list[tuple[date, float]] = []
    note = ""

    if len(daily) >= 2:
        dates = [d for d, _ in daily]
        values = np.array([v for _, v in daily], dtype=float)
        # Simple daily returns between consecutive recorded days. A wiped-out
        # account (a zero equity point) would divide by zero, so guard the
        # denominator and treat that step as a flat day rather than an inf.
        prev = values[:-1]
        rets = np.divide(np.diff(values), prev, out=np.zeros(len(prev)),
                         where=prev != 0)
        daily_returns = list(zip(dates[1:], (rets * 100.0).tolist()))

        best_pct = float(rets.max() * 100.0)
        worst_pct = float(rets.min() * 100.0)

        std = float(rets.std(ddof=1)) if len(rets) >= 2 else 0.0
        vol_pct = std * np.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
        if std > 0:
            daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
            excess = rets - daily_rf
            sharpe = float(excess.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))

        # Maximum drawdown over the recorded equity curve.
        running_max = np.maximum.accumulate(values)
        drawdowns = np.divide(values - running_max, running_max,
                              out=np.zeros(len(values)), where=running_max != 0)
        max_dd_pct = float(drawdowns.min() * 100.0)
    else:
        note = "Collect at least two trading days of activity for risk metrics."

    return AnalyticsReport(
        days=len(daily),
        total_return=total_return,
        total_return_pct=total_return_pct,
        annualized_volatility_pct=vol_pct,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd_pct,
        best_day_pct=best_pct,
        worst_day_pct=worst_pct,
        num_trades=len(trades),
        num_buys=num_buys,
        num_sells=num_sells,
        realized_pl=session.realized_pl,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        daily_returns=daily_returns,
        note=note,
    )
