"""Modal dialogs: create a session, open/manage sessions, and view analytics."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_STARTING_BALANCE, DEFAULT_WATCHLIST
from ..core.analytics import AnalyticsReport, compute_analytics
from ..core.models import Session
from ..credentials import is_paper_endpoint, load_alpaca, save_alpaca
from ..persistence.store import SessionInfo, Store, StoreError
from ..ui import theme
from ..ui.format import (
    fmt_datetime,
    fmt_money,
    fmt_pct,
    fmt_signed_money,
    fmt_signed_pct,
)


# --------------------------------------------------------------------------- #
# New session
# --------------------------------------------------------------------------- #
class NewSessionDialog(QDialog):
    """Collects a name and starting balance for a fresh session."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Session")
        self.setMinimumWidth(360)

        form = QFormLayout()
        self._name = QLineEdit("My Portfolio")
        self._balance = QDoubleSpinBox()
        self._balance.setRange(1.0, 1_000_000_000.0)
        self._balance.setDecimals(2)
        self._balance.setGroupSeparatorShown(True)
        self._balance.setPrefix("$ ")
        self._balance.setValue(DEFAULT_STARTING_BALANCE)
        form.addRow("Name", self._name)
        form.addRow("Starting balance", self._balance)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)

    def result_session(self) -> Session:
        name = self._name.text().strip() or "My Portfolio"
        return Session.new(name, round(self._balance.value(), 2), list(DEFAULT_WATCHLIST))


# --------------------------------------------------------------------------- #
# Alpaca API keys
# --------------------------------------------------------------------------- #
DEFAULT_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


class AlpacaKeysDialog(QDialog):
    """Enter/edit Alpaca paper-trading API keys (stored locally, not in the repo)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Alpaca API Keys")
        self.setMinimumWidth(460)
        creds = load_alpaca()

        root = QVBoxLayout(self)
        blurb = QLabel(
            "Connect a free Alpaca <b>paper</b> account. Create keys at "
            "alpaca.markets → Paper Trading → API Keys. They're saved locally to "
            "~/.paper_trader/credentials.json (chmod 600) — never in the project.")
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        form = QFormLayout()
        self._key = QLineEdit(creds.key_id if creds else "")
        self._key.setPlaceholderText("PK…")
        self._secret = QLineEdit(creds.secret_key if creds else "")
        self._secret.setPlaceholderText("secret")
        self._secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._endpoint = QLineEdit(creds.base_url if creds else DEFAULT_PAPER_ENDPOINT)
        form.addRow("API Key ID", self._key)
        form.addRow("API Secret", self._secret)
        form.addRow("Endpoint", self._endpoint)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_save(self) -> None:
        key = self._key.text().strip()
        secret = self._secret.text().strip()
        if not key or not secret:
            QMessageBox.warning(self, "Alpaca Keys", "Both the key and secret are required.")
            return
        endpoint = self._endpoint.text().strip() or DEFAULT_PAPER_ENDPOINT
        # This app places real orders through whatever endpoint it is given. A
        # live-trading host would spend actual money, so it takes a deliberate
        # confirmation rather than a typo.
        if not is_paper_endpoint(endpoint):
            resp = QMessageBox.warning(
                self, "Not a paper endpoint",
                f"{endpoint}\n\nThis is not Alpaca's paper-trading endpoint. "
                "Orders placed from this app would be sent to that account for "
                "real.\n\nUse it anyway?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel)
            if resp != QMessageBox.StandardButton.Yes:
                return
        save_alpaca(key, secret, paper=is_paper_endpoint(endpoint), base_url=endpoint)
        self.accept()


# --------------------------------------------------------------------------- #
# Open / manage sessions
# --------------------------------------------------------------------------- #
class OpenSessionDialog(QDialog):
    """Lists saved sessions to open or delete."""

    def __init__(self, store: Store, current_id: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Session")
        self.setMinimumSize(460, 340)
        self._store = store
        self._selected_id: str | None = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Saved sessions"))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self._accept_selected())
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

        self._current_id = current_id
        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        for info in self._store.list_sessions():
            self._list.addItem(_session_item(info, info.id == self._current_id))
        if self._list.count():
            self._list.setCurrentRow(0)

    def _current_info_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _accept_selected(self) -> None:
        sid = self._current_info_id()
        if sid:
            self._selected_id = sid
            self.accept()

    def _delete_selected(self) -> None:
        sid = self._current_info_id()
        if not sid:
            return
        if sid == self._current_id:
            QMessageBox.information(self, "Delete Session",
                                    "You can't delete the session that's currently open.")
            return
        if QMessageBox.question(self, "Delete Session",
                                "Permanently delete this session?") == \
                QMessageBox.StandardButton.Yes:
            try:
                self._store.delete_session(sid)
            except StoreError as exc:
                QMessageBox.warning(self, "Delete Session", str(exc))
                return
            self._reload()

    def selected_id(self) -> str | None:
        return self._selected_id


def _session_item(info: SessionInfo, is_current: bool) -> QListWidgetItem:
    tag = "   (current)" if is_current else ""
    text = (f"{info.name}{tag}\n"
            f"    Cash {fmt_money(info.cash)}  ·  {info.num_positions} positions  ·  "
            f"{info.num_trades} trades  ·  updated {fmt_datetime(info.updated_at)}")
    item = QListWidgetItem(text)
    item.setData(Qt.ItemDataRole.UserRole, info.id)
    return item


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
class AnalyticsDialog(QDialog):
    """Shows performance statistics and the equity curve for a session."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Analytics — {session.name}")
        self.setMinimumSize(620, 560)
        report = compute_analytics(session)

        root = QVBoxLayout(self)
        root.setSpacing(14)

        title = QLabel("Performance")
        title.setObjectName("H2")
        root.addWidget(title)

        root.addLayout(_metrics_grid(report))

        eq_title = QLabel("EQUITY CURVE")
        eq_title.setObjectName("SectionTitle")
        root.addWidget(eq_title)
        root.addWidget(_equity_plot(session), 1)

        if report.note:
            note = QLabel(report.note)
            note.setObjectName("Muted")
            note.setWordWrap(True)
            root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        root.addWidget(buttons)


def _metric_tile(title: str, value: str, color: str | None = None) -> QWidget:
    w = QWidget()
    box = QVBoxLayout(w)
    box.setContentsMargins(12, 10, 12, 10)
    box.setSpacing(2)
    t = QLabel(title.upper())
    t.setObjectName("Faint")
    t.setStyleSheet("font-size: 10px; letter-spacing: 0.5px;")
    v = QLabel(value)
    v.setStyleSheet("font-size: 20px; font-weight: 700;" + (f" color: {color};" if color else ""))
    box.addWidget(t)
    box.addWidget(v)
    w.setObjectName("Card")
    return w


def _metrics_grid(r: AnalyticsReport) -> QGridLayout:
    grid = QGridLayout()
    grid.setSpacing(10)

    def pct(v):
        return fmt_signed_pct(v) if v is not None else "—"

    def money(v):
        return fmt_signed_money(v) if v is not None else "—"

    tiles = [
        ("Total Return", money(r.total_return),
         theme.color_for(r.total_return or 0) if r.total_return is not None else None),
        ("Total Return %", pct(r.total_return_pct),
         theme.color_for(r.total_return_pct or 0) if r.total_return_pct is not None else None),
        ("Realized P/L", fmt_signed_money(r.realized_pl), theme.color_for(r.realized_pl)),
        ("Sharpe Ratio", f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "—", None),
        ("Volatility (ann.)",
         fmt_pct(r.annualized_volatility_pct) if r.annualized_volatility_pct is not None else "—",
         None),
        ("Max Drawdown",
         fmt_pct(r.max_drawdown_pct) if r.max_drawdown_pct is not None else "—",
         theme.loss_color() if r.max_drawdown_pct else None),
        ("Trades", str(r.num_trades), None),
        ("Win Rate", fmt_pct(r.win_rate) if r.win_rate is not None else "—", None),
        ("Avg Win / Loss",
         f"{fmt_money(r.avg_win) if r.avg_win else '—'} / "
         f"{fmt_money(r.avg_loss) if r.avg_loss else '—'}", None),
    ]
    for i, (title, value, color) in enumerate(tiles):
        grid.addWidget(_metric_tile(title, value, color), i // 3, i % 3)
    return grid


def _equity_plot(session: Session) -> QWidget:
    colors = theme.chart_colors()
    plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
    plot.setBackground(colors["background"])
    plot.setMenuEnabled(False)
    plot.showGrid(x=True, y=True, alpha=0.15)
    for axis in ("bottom", "left"):
        plot.getAxis(axis).setPen(pg.mkPen(colors["axis"]))
        plot.getAxis(axis).setTextPen(pg.mkPen(colors["text"]))

    points = session.equity_curve
    if len(points) >= 2:
        xs = np.array([p.time.timestamp() for p in points], dtype=float)
        ys = np.array([p.value for p in points], dtype=float)
        up = ys[-1] >= ys[0]
        color = colors["up"] if up else colors["down"]
        fill = pg.mkColor(color); fill.setAlpha(40)
        plot.plot(xs, ys, pen=pg.mkPen(color, width=2),
                  fillLevel=float(ys.min()), brush=fill)
        base = pg.InfiniteLine(pos=session.starting_balance, angle=0,
                               pen=pg.mkPen(colors["text"], style=Qt.PenStyle.DashLine))
        plot.addItem(base)
    else:
        text = pg.TextItem("Not enough history yet — trade over multiple days to build a curve.",
                           color=colors["text"])
        plot.addItem(text)
    return plot
