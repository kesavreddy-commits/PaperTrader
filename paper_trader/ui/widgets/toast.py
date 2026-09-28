"""Transient notices that float over the bottom of the window.

Order fills, watchlist changes and errors used to scroll past in a status bar
at the foot of the window, easy to miss and styled like an afterthought. A
toast puts each one where the eye already is — a small card over the bottom
centre, with an icon for what kind of news it is — then fades away.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from .. import anim, icons, theme

_ICONS = {
    "success": ("check_circle", "green"),
    "error": ("alert", "red"),
    "info": ("info", "text_muted"),
}


class Toast(QFrame):
    """One notice at a time; a new message replaces the one on screen."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("Toast")
        self._host = host
        self._kind = "info"

        box = QHBoxLayout(self)
        box.setContentsMargins(14, 11, 18, 11)
        box.setSpacing(10)
        self._icon = QLabel()
        self._icon.setObjectName("Clear")
        self._icon.setFixedSize(18, 18)
        self._text = QLabel("")
        self._text.setObjectName("ToastText")
        self._text.setWordWrap(True)
        self._text.setMaximumWidth(560)
        box.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        box.addWidget(self._text, 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        host.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------------ #
    def show_message(self, text: str, kind: str = "info", msecs: int = 4000) -> None:
        """Show ``text``; ``msecs`` of 0 keeps it up until the next message."""
        self._kind = kind if kind in _ICONS else "info"
        self._paint_icon()
        self._text.setText(text)
        # A wrapping label asks for a narrow width; give it one line's worth.
        one_line = self._text.fontMetrics().horizontalAdvance(text) + 6
        self._text.setMinimumWidth(min(one_line, self._text.maximumWidth()))
        self.adjustSize()
        self._place()
        self._fade.stop()
        was_visible = self.isVisible()
        self.show()
        self.raise_()
        if anim.ENABLED and not was_visible:
            self._opacity.setOpacity(0.0)
            self._fade.setDuration(180)
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        else:
            self._opacity.setOpacity(1.0)
        if msecs > 0:
            self._timer.start(max(1800, msecs))
        else:
            self._timer.stop()

    def dismiss(self) -> None:
        self._timer.stop()
        if not self.isVisible():
            return
        if anim.ENABLED:
            self._fade.stop()
            self._fade.setDuration(260)
            self._fade.setStartValue(self._opacity.opacity())
            self._fade.setEndValue(0.0)
            self._fade.start()
        else:
            self.hide()

    def text(self) -> str:
        return self._text.text() if self.isVisible() else ""

    def kind(self) -> str:
        return self._kind

    def refresh_theme(self) -> None:
        self._paint_icon()

    # ------------------------------------------------------------------ #
    def _paint_icon(self) -> None:
        name, key = _ICONS[self._kind]
        self._icon.setPixmap(icons.pixmap(name, theme.color(key), 18))

    def _on_fade_done(self) -> None:
        if self._opacity.opacity() <= 0.01:
            self.hide()
            self._opacity.setOpacity(1.0)

    def _place(self) -> None:
        host = self._host
        x = max(16, (host.width() - self.width()) // 2)
        y = max(16, host.height() - self.height() - 28)
        self.move(x, y)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
