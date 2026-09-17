"""Paper Trader — a fully local, real-time paper-trading desktop application.

The package is split into strictly separated layers:

    data/         Market-data access (the only network-facing layer).
    core/         Pure trading logic and portfolio math (no Qt, no network).
    persistence/  Local session storage (JSON on disk).
    ui/           PyQt6 presentation layer (no network, no business math).

Nothing in ``core`` imports Qt or ``requests``; nothing in ``data`` imports Qt.
This keeps the trading engine unit-testable in isolation and lets the data
provider be swapped without touching the UI.
"""

__version__ = "1.0.0"
