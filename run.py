#!/usr/bin/env python3
"""Launcher for Paper Trader.

Keeps startup friction minimal: just ``python run.py`` (add ``--demo`` for
offline data). Ensures the project root is importable no matter where it's run
from, then hands off to the app bootstrap.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_trader.app import main  # noqa: E402  (after sys.path setup)

if __name__ == "__main__":
    raise SystemExit(main())
