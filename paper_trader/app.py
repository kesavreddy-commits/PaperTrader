"""Application bootstrap: parse arguments, load settings/session, show the window.

Run via the project-root launcher::

    python run.py            # live Yahoo Finance data
    python run.py --demo     # offline synthetic data (no network needed)
"""

from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="paper-trader",
        description="A local, real-time paper-trading simulator.",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use offline synthetic market data (no internet required).",
    )
    parser.add_argument(
        "--home", metavar="DIR",
        help="Directory for saved sessions/settings (default: ~/.paper_trader).",
    )
    parser.add_argument(
        "--old", "--classic", dest="old", action="store_true",
        help="Run the previous (pre-rework) interface.",
    )
    return parser.parse_args(argv)


def _load_or_create_session(store, settings):
    """Return the last-used session, the most recent one, or a fresh default."""
    from .config import DEFAULT_STARTING_BALANCE, DEFAULT_WATCHLIST
    from .core.models import Session
    from .persistence.store import StoreError

    last_id = settings.get("last_session_id")
    if last_id:
        try:
            return store.load_session(last_id)
        except StoreError:
            pass
    for info in store.list_sessions():
        try:
            return store.load_session(info.id)
        except StoreError:
            continue
    session = Session.new("My Portfolio", DEFAULT_STARTING_BALANCE, list(DEFAULT_WATCHLIST))
    store.save_session(session)
    store.set_setting("last_session_id", session.id)
    return session


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # PAPER_TRADER_HOME must be set before Store/config resolve any paths.
    if args.home:
        os.environ["PAPER_TRADER_HOME"] = args.home

    from PyQt6.QtWidgets import QApplication

    from .config import APP_NAME, DEFAULT_THEME
    from .credentials import load_alpaca
    from .persistence.store import Store
    from .ui import theme

    # The pre-rework interface is kept runnable side by side with the current
    # one; both drive the same session, broker and data layers.
    if args.old:
        from .ui_legacy.main_window import MainWindow
    else:
        from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    # Fusion gives our stylesheet a consistent base across platforms.
    app.setStyle("Fusion")

    store = Store()
    settings = store.load_settings()
    theme_name = settings.get("theme", DEFAULT_THEME)

    # Decide backends. --demo forces a fully offline session; otherwise, if
    # Alpaca keys exist we default to the live paper account + Alpaca data.
    have_keys = load_alpaca() is not None
    if args.demo:
        broker_mode, data_source = "local", "demo"
    elif have_keys:
        broker_mode = settings.get("broker", "alpaca")
        data_source = settings.get("data_source", "alpaca")
    else:
        broker_mode = "local"
        data_source = settings.get("data_source", "yahoo")

    theme.apply_theme(app, theme_name, legacy=args.old)

    session = _load_or_create_session(store, settings)
    window = MainWindow(session, store, broker_mode=broker_mode,
                        data_source=data_source, theme_name=theme_name)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
