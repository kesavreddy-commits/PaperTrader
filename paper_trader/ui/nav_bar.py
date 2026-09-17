"""The top navigation bar: wordmark, symbol search and account controls.

Mirrors the layout of the reference web app — brand on the left, a wide rounded
search field beside it, plain-text controls on the right — and owns the search
box's debounce plus the results dropdown. It is a view: queries go out as
:attr:`searchRequested` and the owner feeds results back through
:meth:`show_results`, which keeps all network work off this widget.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from ..data.models import SearchResult
from . import theme

_SEARCH_DEBOUNCE_MS = 280


class NavBar(QFrame):
    """Brand + search + account controls, pinned to the top of the window."""

    searchRequested = pyqtSignal(str)   # debounced free-text query
    symbolChosen = pyqtSignal(str)      # a result was picked from the dropdown
    symbolSubmitted = pyqtSignal(str)   # raw text submitted with Return

    def __init__(self, title: str = "Paper Trader", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavBar")
        self.setFixedHeight(64)
        self._results: QListWidget | None = None
        self._build(title)

    # ------------------------------------------------------------------ #
    def _build(self, title: str) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 0, 22, 0)
        root.setSpacing(18)

        mark = QLabel("◗")
        mark.setStyleSheet(
            f"color: {theme.color('green')}; font-size: 20px; font-weight: 700;")
        wordmark = QLabel(title)
        wordmark.setObjectName("Wordmark")
        root.addWidget(mark)
        root.addWidget(wordmark)
        root.addSpacing(10)

        # -- search --------------------------------------------------------- #
        self._search = QLineEdit()
        self._search.setObjectName("SearchBox")
        self._search.setPlaceholderText("Search")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(320)
        self._search.setMaximumWidth(700)
        self._search.textChanged.connect(self._on_search_text)
        self._search.returnPressed.connect(self._on_search_return)
        self._search.installEventFilter(self)
        # The magnifier sits inside the field, the way the reference does it.
        glass = QLabel("⌕", self._search)
        glass.setStyleSheet(
            f"color: {theme.color('text_muted')}; font-size: 15px; background: transparent;")
        glass.move(11, 8)
        glass.setFixedSize(20, 22)
        self._glass = glass
        root.addWidget(self._search, 1)

        root.addStretch(1)

        self._controls = QHBoxLayout()
        self._controls.setSpacing(2)
        holder = QWidget()
        holder.setLayout(self._controls)
        root.addWidget(holder)

        self._status = QLabel("")
        self._status.setStyleSheet("font-weight: 700; padding-left: 10px;")
        root.addWidget(self._status)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        """Close the dropdown once the field loses focus.

        Deferred a beat so a click *on* a result still lands before the list
        disappears out from under the cursor.
        """
        if obj is self._search and event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(150, self.hide_results)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # Controls the owner installs
    # ------------------------------------------------------------------ #
    def add_menu(self, label: str, menu: QMenu) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("NavLink")
        btn.setText(f"{label}  ⌄")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMenu(menu)
        self._controls.addWidget(btn)
        return btn

    def add_action(self, label: str, slot) -> QPushButton:
        btn = QPushButton(label, self)
        btn.setObjectName("NavLink")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False: slot())
        self._controls.addWidget(btn)
        return btn

    def set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color: {color}; font-weight: 700; padding-left: 10px;")

    def refresh_theme(self) -> None:
        self._glass.setStyleSheet(
            f"color: {theme.color('text_muted')}; font-size: 15px; background: transparent;")

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
            self._results = QListWidget(self.window())
            self._results.setObjectName("SearchResults")
            self._results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._results.setCursor(Qt.CursorShape.PointingHandCursor)
            self._results.itemClicked.connect(self._on_result_clicked)
            self._results.hide()
        return self._results

    def show_results(self, results: list[SearchResult]) -> None:
        box = self._ensure_results()
        box.clear()
        if not results or not self._search.text().strip():
            box.hide()
            return
        for r in results:
            label = r.symbol if not r.name else f"{r.symbol}   ·   {r.name}"
            meta = "  ".join(x for x in (r.type, r.exchange) if x)
            item = QListWidgetItem(f"{label}" + (f"    [{meta}]" if meta else ""))
            item.setData(Qt.ItemDataRole.UserRole, r.symbol)
            box.addItem(item)
        rows = min(len(results), 8)
        box.setFixedSize(self._search.width(), rows * 36 + 10)
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
        if box is not None and not box.isHidden() and box.count():
            self._pick(box.item(0))
            return
        text = self._search.text().strip()
        if text:
            self.symbolSubmitted.emit(text)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        self._pick(item)

    def _pick(self, item: QListWidgetItem) -> None:
        symbol = item.data(Qt.ItemDataRole.UserRole)
        self.clear_search()
        if symbol:
            self.symbolChosen.emit(symbol)
