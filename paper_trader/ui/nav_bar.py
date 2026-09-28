"""The top navigation bar: brand, symbol search and account controls.

Mirrors the reference web app — brand on the left, a wide search field beside
it, bold text controls on the right — and owns the search box's debounce plus
the results dropdown. It is a view: queries go out as :attr:`searchRequested`
and the owner feeds results back through :meth:`show_results`, which keeps all
network work off this widget.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from ..data.models import SearchResult
from . import icons, theme

_SEARCH_DEBOUNCE_MS = 280
_RESULT_ROW = 46          # px per dropdown row
_MAX_RESULTS = 8

# Item data roles for the dropdown rows.
_SYMBOL = Qt.ItemDataRole.UserRole
_NAME = Qt.ItemDataRole.UserRole + 1
_META = Qt.ItemDataRole.UserRole + 2


class NavBar(QFrame):
    """Brand + search + account controls, pinned to the top of the window."""

    searchRequested = pyqtSignal(str)   # debounced free-text query
    symbolChosen = pyqtSignal(str)      # a result was picked from the dropdown
    symbolSubmitted = pyqtSignal(str)   # raw text submitted with Return

    HEIGHT = 72

    def __init__(self, title: str = "Paper Trader", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavBar")
        self.setFixedHeight(self.HEIGHT)
        self._results: QListWidget | None = None
        self._build(title)

    # ------------------------------------------------------------------ #
    def _build(self, title: str) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 0, 24, 0)
        root.setSpacing(0)

        self._sidebar_btn = QPushButton()
        self._sidebar_btn.setObjectName("IconButton")
        self._sidebar_btn.setFixedSize(32, 32)
        self._sidebar_btn.setIconSize(QSize(18, 18))
        self._sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_btn.setToolTip("Show or hide the watchlist (Ctrl+L)")
        self._sidebar_btn.hide()                   # shown once an owner wires it
        root.addWidget(self._sidebar_btn)
        root.addSpacing(10)

        self._mark = QLabel()
        self._mark.setFixedSize(24, 24)
        wordmark = QLabel(title)
        wordmark.setObjectName("Wordmark")
        theme.display_cut(wordmark, 20)
        root.addWidget(self._mark)
        root.addSpacing(9)
        root.addWidget(wordmark)
        root.addSpacing(34)

        # -- search --------------------------------------------------------- #
        self._search = QLineEdit()
        self._search.setObjectName("SearchBox")
        self._search.setPlaceholderText("Search")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(40)
        self._search.setMinimumWidth(280)
        self._search.setMaximumWidth(560)
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_return)
        self._search.installEventFilter(self)
        # The magnifier sits inside the field's left padding, as in the reference.
        self._glass = QLabel(self._search)
        self._glass.setFixedSize(18, 18)
        self._glass.move(13, 11)
        self._glass.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        root.addWidget(self._search, 3)

        root.addStretch(2)

        self._controls = QHBoxLayout()
        self._controls.setSpacing(6)
        holder = QWidget()
        holder.setObjectName("Clear")
        holder.setLayout(self._controls)
        root.addWidget(holder)
        root.addSpacing(14)

        self._status = QLabel("")
        self._status.setObjectName("StatusChip")
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setFixedHeight(28)
        root.addWidget(self._status, 0, Qt.AlignmentFlag.AlignVCenter)

        self.refresh_theme()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        """Arrow keys move through the dropdown; the field keeps focus.

        Losing focus closes the list — deferred a beat so a click *on* a
        result still lands before it disappears out from under the cursor.
        """
        if obj is self._search:
            kind = event.type()
            if kind == QEvent.Type.FocusOut:
                QTimer.singleShot(150, self.hide_results)
            elif kind == QEvent.Type.KeyPress and self._results_visible():
                key = event.key()
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    box = self._results
                    step = 1 if key == Qt.Key.Key_Down else -1
                    row = max(0, min(box.count() - 1, box.currentRow() + step))
                    box.setCurrentRow(row)
                    return True
                if key == Qt.Key.Key_Escape:
                    self.hide_results()
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # Controls the owner installs
    # ------------------------------------------------------------------ #
    def add_menu(self, label: str, menu: QMenu) -> QPushButton:
        # No arrow beside the label: the stylesheet suppresses the menu
        # indicator and shows hover / open states instead (see #NavMenu).
        menu.setMinimumWidth(200)
        btn = QPushButton(label, self)
        btn.setObjectName("NavMenu")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMenu(menu)
        # ``:open`` is not reliably matched for a button's popup on every
        # style, so the pill for "menu is showing" rides on a plain property.
        menu.aboutToShow.connect(lambda b=btn: _set_open(b, True))
        menu.aboutToHide.connect(lambda b=btn: _set_open(b, False))
        self._controls.addWidget(btn)
        return btn

    def add_action(self, label: str, slot) -> QPushButton:
        btn = QPushButton(label, self)
        btn.setObjectName("NavLink")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False: slot())
        self._controls.addWidget(btn)
        return btn

    def set_sidebar_toggle(self, slot) -> None:
        """Show the watchlist toggle at the far left and route it to ``slot``."""
        self._sidebar_btn.clicked.connect(lambda _=False: slot())
        self._sidebar_btn.show()

    def set_status(self, text: str, color: str) -> None:
        self._status.setText(
            f"<span style='color:{color}'>●</span>&nbsp;&nbsp;{text}")

    def refresh_theme(self) -> None:
        self._mark.setPixmap(icons.pixmap("logo", theme.color("green"), 24))
        self._glass.setPixmap(icons.pixmap("search", theme.color("text"), 18))
        self._sidebar_btn.setIcon(icons.icon("sidebar", theme.color("text_muted"), 18))
        if self._results is not None:
            self._results.setGraphicsEffect(_dropdown_shadow(self._results))

    def focus_search(self) -> None:
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    # ------------------------------------------------------------------ #
    # Results dropdown
    # ------------------------------------------------------------------ #
    def _ensure_results(self) -> QListWidget:
        if self._results is None:
            # Parented to the window so the dropdown floats over the page
            # instead of resizing the nav bar.
            box = QListWidget(self.window())
            box.setObjectName("SearchResults")
            box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            box.setMouseTracking(True)
            box.setUniformItemSizes(True)
            box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            box.setItemDelegate(_ResultDelegate(box))
            box.itemClicked.connect(self._on_result_clicked)
            box.setGraphicsEffect(_dropdown_shadow(box))
            box.hide()
            self._results = box
        return self._results

    def _results_visible(self) -> bool:
        return self._results is not None and not self._results.isHidden() \
            and self._results.count() > 0

    def show_results(self, results: list[SearchResult]) -> None:
        box = self._ensure_results()
        box.clear()
        if not results or not self._search.text().strip():
            box.hide()
            return
        for r in results[:_MAX_RESULTS]:
            item = QListWidgetItem(r.symbol)
            item.setData(_SYMBOL, r.symbol)
            item.setData(_NAME, r.name or "")
            item.setData(_META, " · ".join(x for x in (r.type, r.exchange) if x))
            item.setSizeHint(QSize(0, _RESULT_ROW))
            box.addItem(item)
        rows = min(len(results), _MAX_RESULTS)
        box.setFixedSize(max(self._search.width(), 420), rows * _RESULT_ROW + 14)
        box.setCurrentRow(0)
        top_left = self._search.mapTo(self.window(), QPoint(0, self._search.height() + 6))
        box.move(top_left)
        box.show()
        box.raise_()

    def clear_search(self) -> None:
        self._search.clear()
        if self._results is not None:
            self._results.clear()
            self._results.hide()

    def hide_results(self) -> None:
        if self._results is not None:
            self._results.hide()

    # -- signal handlers ----------------------------------------------- #
    def _on_search_text(self, text: str) -> None:
        if not hasattr(self, "_debounce"):
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(_SEARCH_DEBOUNCE_MS)
            self._debounce.timeout.connect(self._emit_search)
        if text.strip():
            self._debounce.start()
        else:
            self.hide_results()

    def _emit_search(self) -> None:
        query = self._search.text().strip()
        if query:
            self.searchRequested.emit(query)

    def _on_search_return(self) -> None:
        box = self._results
        if self._results_visible():
            self._pick(box.currentItem() or box.item(0))
            return
        text = self._search.text().strip()
        if text:
            self.symbolSubmitted.emit(text)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        self._pick(item)

    def _pick(self, item: QListWidgetItem) -> None:
        symbol = item.data(_SYMBOL)
        self.clear_search()
        if symbol:
            self.symbolChosen.emit(symbol)


def _set_open(button: QPushButton, is_open: bool) -> None:
    """Flag ``button`` as holding an open menu and restyle it."""
    button.setProperty("open", is_open)
    style = button.style()
    style.unpolish(button)
    style.polish(button)
    button.update()


def _dropdown_shadow(parent: QWidget) -> QGraphicsDropShadowEffect:
    shadow = QGraphicsDropShadowEffect(parent)
    shadow.setBlurRadius(28)
    shadow.setOffset(0, 10)
    shadow.setColor(QColor(0, 0, 0, 170 if theme.is_dark() else 60))
    return shadow


class _ResultDelegate(QStyledItemDelegate):
    """One search hit: bold symbol, the company beside it, a quiet tag on the right."""

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 1, -2, -1)
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.color("menu_hover")))
            painter.drawRoundedRect(QRectF(rect), 5, 5)    # a dropdown row: soft, like menus

        symbol = index.data(_SYMBOL) or ""
        name = index.data(_NAME) or ""
        meta = index.data(_META) or ""
        inner = rect.adjusted(12, 0, -12, 0)

        base = QFont(option.font)
        bold = theme.resized(base, 1)
        bold.setWeight(QFont.Weight.DemiBold)
        painter.setFont(bold)
        painter.setPen(QColor(theme.color("text")))
        sym_w = max(QFontMetrics(bold).horizontalAdvance(symbol), 58)
        painter.drawText(QRect(inner.left(), inner.top(), sym_w, inner.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, symbol)

        small = theme.resized(base, -2)
        meta_w = QFontMetrics(small).horizontalAdvance(meta) if meta else 0
        if meta:
            painter.setFont(small)
            painter.setPen(QColor(theme.color("text_faint")))
            painter.drawText(QRect(inner.right() - meta_w, inner.top(), meta_w, inner.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, meta)

        name_left = inner.left() + sym_w + 14
        name_w = inner.right() - name_left - (meta_w + 16 if meta else 0)
        if name and name_w > 20:
            painter.setFont(base)
            painter.setPen(QColor(theme.color("text_muted")))
            elided = QFontMetrics(base).elidedText(name, Qt.TextElideMode.ElideRight, name_w)
            painter.drawText(QRect(name_left, inner.top(), name_w, inner.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(option.rect.width(), _RESULT_ROW)
