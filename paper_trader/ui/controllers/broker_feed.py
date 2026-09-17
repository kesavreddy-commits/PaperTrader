"""Background poller for remote brokers.

A :class:`BrokerFeed` lives on its own ``QThread`` and periodically calls
``broker.refresh()`` (network) so the account, positions and orders stay current
without blocking the UI. It emits :attr:`updated` after each successful refresh;
the main window then re-reads the broker's snapshot on the GUI thread.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QMetaObject,
    QObject,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)

from ...broker.base import Broker, BrokerError
from ...config import BROKER_POLL_SECONDS


class BrokerFeed(QObject):
    updated = pyqtSignal()             # a refresh succeeded
    errorOccurred = pyqtSignal(str)    # a refresh failed

    def __init__(self, broker: Broker) -> None:
        super().__init__()
        self._broker = broker
        self._timer: QTimer | None = None
        self._running = False

    @pyqtSlot()
    def start(self) -> None:
        self._timer = QTimer()
        self._timer.setInterval(int(BROKER_POLL_SECONDS * 1000))
        self._timer.timeout.connect(self._tick)
        self._running = True
        self._timer.start()
        self._tick()  # first refresh immediately

    @pyqtSlot()
    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.stop()

    def request_immediate(self) -> None:
        QMetaObject.invokeMethod(self, "_tick", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _tick(self) -> None:
        if not self._running:
            return
        try:
            self._broker.refresh()
        except BrokerError as exc:
            self.errorOccurred.emit(str(exc))
            return
        except Exception as exc:  # never let the poll thread die
            self.errorOccurred.emit(f"Broker error: {exc}")
            return
        self.updated.emit()
