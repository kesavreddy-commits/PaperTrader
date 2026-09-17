"""The pre-rework GUI, kept runnable via ``python run.py --old``.

This package holds the views whose *shape* changed in the Robinhood-style
rework: the composition root, the chart, the price header, the portfolio bar,
the order ticket and the watchlist (which owned symbol search back then).
Everything else — the options chain, option ticket, position/history tables,
dialogs, the data and broker feeds — is shared with the current UI, so there is
one implementation of each and only the layout differs.

The legacy palette and stylesheet live alongside the current ones in
``ui.theme`` and are selected with ``apply_theme(app, name, legacy=True)``, which
keeps the shared widgets consistent with whichever window is on screen.
"""
