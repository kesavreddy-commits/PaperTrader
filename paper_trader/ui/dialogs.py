"""Modal dialogs: create a session, open/manage sessions, API keys, analytics.

They share the page's language: a bold title, a quiet line saying what the
dialog is for, fields in the card style, and the green pill as the default
action. The analytics view draws its equity curve the way the main chart
draws a price — the line in the gain/loss colour over a dotted reference (the
starting balance) — and says plainly when there isn't enough history yet
rather than showing an empty plot with meaningless axes.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_STARTING_BALANCE, DEFAULT_WATCHLIST
from ..core.analytics import AnalyticsReport, compute_analytics
from ..core.models import Session
from ..credentials import is_paper_endpoint, load_alpaca, save_alpaca
from ..persistence.store import SessionInfo, Store, StoreError
from . import theme
from .format import (
    fmt_datetime,
    fmt_money,
    fmt_pct,
    fmt_signed_money,
    fmt_signed_pct,
)


def _header(root: QVBoxLayout, title: str, subtitle: str = "") -> None:
    """The title (and a line of what the dialog is for) atop every dialog."""
    head = QLabel(title)
    head.setObjectName("H2")
    root.addWidget(head)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(sub)
    root.addSpacing(6)


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return form


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    return label


# --------------------------------------------------------------------------- #
# New session
# --------------------------------------------------------------------------- #
class NewSessionDialog(QDialog):
    """Collects a name and starting balance for a fresh session."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Session")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        _header(root, "New local session",
                "A fresh simulated portfolio with its own cash, positions and history.")

        form = _form()
        self._name = QLineEdit("My Portfolio")
        self._balance = QDoubleSpinBox()
        self._balance.setRange(1.0, 1_000_000_000.0)
        self._balance.setDecimals(2)
        self._balance.setGroupSeparatorShown(True)
        self._balance.setPrefix("$ ")
        self._balance.setValue(DEFAULT_STARTING_BALANCE)
        self._balance.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        form.addRow(_field_label("Name"), self._name)
        form.addRow(_field_label("Starting balance"), self._balance)
        root.addLayout(form)
        root.addSpacing(8)
        root.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create session")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
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
        self.setMinimumWidth(500)
        creds = load_alpaca()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        _header(root, "Alpaca API keys",
                "Connect a free Alpaca <b>paper</b> account. Create keys at "
                "alpaca.markets → Paper Trading → API Keys. They're saved locally to "
                "~/.paper_trader/credentials.json (chmod 600) — never in the project.")

        form = _form()
        self._key = QLineEdit(creds.key_id if creds else "")
        self._key.setPlaceholderText("PK…")
        self._secret = QLineEdit(creds.secret_key if creds else "")
        self._secret.setPlaceholderText("Secret key")
        self._secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._endpoint = QLineEdit(creds.base_url if creds else DEFAULT_PAPER_ENDPOINT)
        form.addRow(_field_label("API key ID"), self._key)
        form.addRow(_field_label("API secret"), self._secret)
        form.addRow(_field_label("Endpoint"), self._endpoint)
        root.addLayout(form)
        root.addSpacing(8)
        root.addStretch(1)

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
_SESSION_ID = Qt.ItemDataRole.UserRole
_SESSION_NAME = Qt.ItemDataRole.UserRole + 1
_SESSION_DETAIL = Qt.ItemDataRole.UserRole + 2
_SESSION_CURRENT = Qt.ItemDataRole.UserRole + 3


class OpenSessionDialog(QDialog):
    """Lists saved sessions to open or delete."""

    def __init__(self, store: Store, current_id: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Session")
        self.setMinimumSize(520, 380)
        self._store = store
        self._selected_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        _header(root, "Saved sessions", "Pick a local session to open. Double-click opens it.")
        self._list = QListWidget()
        self._list.setItemDelegate(_SessionDelegate(self._list))
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        return item.data(_SESSION_ID) if item else None

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
    detail = (f"Cash {fmt_money(info.cash)}  ·  {info.num_positions} positions  ·  "
              f"{info.num_trades} trades  ·  updated {fmt_datetime(info.updated_at)}")
    item = QListWidgetItem(info.name)
    item.setData(_SESSION_ID, info.id)
    item.setData(_SESSION_NAME, info.name)
    item.setData(_SESSION_DETAIL, detail)
    item.setData(_SESSION_CURRENT, is_current)
    item.setSizeHint(QSize(0, 58))
    return item


class _SessionDelegate(QStyledItemDelegate):
    """A saved session: its name (and whether it's open) over its figures."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(option.rect.width(), 58)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect.adjusted(2, 2, -2, -2))
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.color("menu_hover")))
            painter.drawRoundedRect(rect, 6, 6)
        inner = rect.adjusted(12, 8, -12, -8)
        base = QFont(option.font)
        bold = QFont(base)
        bold.setWeight(QFont.Weight.Bold)
        small = theme.resized(base, -1)
        half = inner.height() / 2
        name = index.data(_SESSION_NAME) or ""
        painter.setFont(bold)
        painter.setPen(QColor(theme.color("text")))
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), half),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        if index.data(_SESSION_CURRENT):
            x = inner.left() + QFontMetrics(bold).horizontalAdvance(name) + 10
            painter.setFont(small)
            painter.setPen(QColor(theme.color("green")))
            painter.drawText(QRectF(x, inner.top(), inner.width(), half),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             "Open now")
        painter.setFont(small)
        painter.setPen(QColor(theme.color("text_muted")))
        detail = QFontMetrics(small).elidedText(
            index.data(_SESSION_DETAIL) or "", Qt.TextElideMode.ElideRight, int(inner.width()))
        painter.drawText(QRectF(inner.left(), inner.top() + half, inner.width(), half),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail)
        painter.restore()


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
class AnalyticsDialog(QDialog):
    """Shows performance statistics and the equity curve for a session."""

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Analytics — {session.name}")
        self.setMinimumSize(680, 600)
        report = compute_analytics(session)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        _header(root, "Performance", session.name)

        root.addLayout(_metrics_grid(report))
        root.addSpacing(6)

        eq_title = QLabel("Equity curve")
        eq_title.setObjectName("H3")
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
    w = QFrame()
    w.setObjectName("Box")
    box = QVBoxLayout(w)
    box.setContentsMargins(14, 11, 14, 12)
    box.setSpacing(4)
    t = QLabel(title)
    t.setObjectName("StatTitle")
    v = QLabel(value)
    v.setFont(theme.tabular(v.font()))
    v.setStyleSheet("font-size: 20px; font-weight: 600;" + (f" color: {color};" if color else ""))
    box.addWidget(t)
    box.addWidget(v)
    return w


def _metrics_grid(r: AnalyticsReport) -> QGridLayout:
    grid = QGridLayout()
    grid.setSpacing(10)

    def pct(v):
        return fmt_signed_pct(v) if v is not None else "—"

    def money(v):
        return fmt_signed_money(v) if v is not None else "—"

    tiles = [
        ("Total return", money(r.total_return),
         theme.color_for(r.total_return or 0) if r.total_return is not None else None),
        ("Total return %", pct(r.total_return_pct),
         theme.color_for(r.total_return_pct or 0) if r.total_return_pct is not None else None),
        ("Realized P/L", fmt_signed_money(r.realized_pl), theme.color_for(r.realized_pl)),
        ("Sharpe ratio", f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "—", None),
        ("Volatility (annualized)",
         fmt_pct(r.annualized_volatility_pct) if r.annualized_volatility_pct is not None else "—",
         None),
        ("Max drawdown",
         fmt_pct(r.max_drawdown_pct) if r.max_drawdown_pct is not None else "—",
         theme.loss_color() if r.max_drawdown_pct else None),
        ("Trades", str(r.num_trades), None),
        ("Win rate", fmt_pct(r.win_rate) if r.win_rate is not None else "—", None),
        ("Avg win / loss",
         f"{fmt_money(r.avg_win) if r.avg_win else '—'} / "
         f"{fmt_money(r.avg_loss) if r.avg_loss else '—'}", None),
    ]
    for i, (title, value, color) in enumerate(tiles):
        grid.addWidget(_metric_tile(title, value, color), i // 3, i % 3)
    return grid


def _equity_plot(session: Session) -> QWidget:
    colors = theme.chart_colors()
    points = session.equity_curve
    if len(points) < 2:
        # An empty plot draws meaningless axes; say what's missing instead.
        empty = QFrame()
        empty.setObjectName("Box")
        empty.setMinimumHeight(180)
        lay = QVBoxLayout(empty)
        msg = QLabel("Not enough history yet — the curve fills in as your portfolio's "
                     "value is recorded over time.")
        msg.setObjectName("Muted")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(msg)
        return empty

    plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
    plot.setBackground(colors["background"])
    plot.setMenuEnabled(False)
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.setFrameShape(QFrame.Shape.NoFrame)
    tick_font = QFont(theme.ui_font_family())
    tick_font.setPixelSize(11)
    for axis in ("bottom", "left"):
        ax = plot.getAxis(axis)
        ax.setPen(pg.mkPen(colors["axis"]))
        ax.setTextPen(pg.mkPen(colors["text"]))
        ax.setStyle(tickFont=tick_font, tickLength=4)
    plot.getAxis("left").setWidth(70)

    xs = np.array([p.time.timestamp() for p in points], dtype=float)
    ys = np.array([p.value for p in points], dtype=float)
    color = colors["up"] if ys[-1] >= session.starting_balance else colors["down"]
    fill = pg.mkColor(color)
    fill.setAlpha(28)
    plot.plot(xs, ys, pen=pg.mkPen(color, width=2),
              fillLevel=float(min(ys.min(), session.starting_balance)), brush=fill)
    dots = pg.mkPen(colors["baseline"], width=1.6)
    dots.setCapStyle(Qt.PenCapStyle.RoundCap)
    dots.setDashPattern([0.01, 3.4])
    plot.addItem(pg.InfiniteLine(pos=session.starting_balance, angle=0, pen=dots))
    return plot
