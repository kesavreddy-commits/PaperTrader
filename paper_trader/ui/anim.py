"""Small, reusable animation helpers — the motion layer of the UI.

Robinhood's feel comes from a few well-chosen micro-animations: numbers that
*roll* to their new value instead of snapping, prices that flash green/red on a
tick, the price line that draws itself in when you switch symbols, and views that
cross-fade. This module packages those as tiny utilities the widgets opt into, so
the animation policy lives in one place and stays consistent (and easy to tune or
disable).

Everything here is GUI-thread only and degrades gracefully: if a value is set for
the first time, or motion is disabled, the target simply jumps to the final state.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QVariantAnimation,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

# One switch to disable all motion (e.g. for reduced-motion or tests).
ENABLED = True


# --------------------------------------------------------------------------- #
# Rolling numbers
# --------------------------------------------------------------------------- #
class NumberRoller(QObject):
    """Tweens a :class:`QLabel`'s numeric value between updates.

    The label's text is produced by ``formatter(value)`` on every animation
    frame, so ``$24,900 → $25,000`` counts up smoothly. The first value (and any
    change smaller than ``min_delta``) is applied instantly to avoid pointless
    motion on tiny ticks.
    """

    def __init__(
        self,
        label: QLabel,
        formatter: Callable[[float], str],
        duration: int = 520,
        min_delta: float = 0.01,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    ) -> None:
        super().__init__(label)
        self._label = label
        self._fmt = formatter
        self._min_delta = min_delta
        self._value: float | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(easing)
        self._anim.valueChanged.connect(self._on_step)

    def _on_step(self, v) -> None:
        self._label.setText(self._fmt(float(v)))

    def set_value(self, value: float | None, animate: bool = True) -> None:
        if value is None:
            self._anim.stop()
            self._value = None
            return
        value = float(value)
        if (self._value is None or not ENABLED or not animate
                or abs(value - self._value) < self._min_delta):
            self._anim.stop()
            self._label.setText(self._fmt(value))
            self._value = value
            return
        self._anim.stop()
        self._anim.setStartValue(float(self._value))
        self._anim.setEndValue(value)
        self._anim.start()
        self._value = value

    @property
    def value(self) -> float | None:
        return self._value


# --------------------------------------------------------------------------- #
# Colour flash (price ticks)
# --------------------------------------------------------------------------- #
class ColorFlash(QObject):
    """Briefly tints a label's text a flash colour, easing back to a base colour.

    Used on the live price: an up-tick flashes green and a down-tick red, then
    fades to the normal text colour over a few hundred milliseconds — the classic
    ticker feel. The label keeps whatever font styling it already had.
    """

    def __init__(self, label: QLabel, base_style: str = "", duration: int = 620) -> None:
        super().__init__(label)
        self._label = label
        self._base_style = base_style
        self._base_color = QColor("#ffffff")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._apply)
        self._anim.finished.connect(self._restore)

    def set_base_style(self, base_style: str, base_color: str) -> None:
        """Set the resting style, and adopt it immediately when idle.

        The label keeps the inline colour from its last flash, so a theme switch
        has to repaint it here — otherwise a label flashed white in dark mode
        stays white on the light theme's white background.
        """
        self._base_style = base_style
        self._base_color = QColor(base_color)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._restore()

    def _apply(self, color) -> None:
        self._label.setStyleSheet(f"{self._base_style} color: {QColor(color).name()};")

    def _restore(self) -> None:
        self._label.setStyleSheet(f"{self._base_style} color: {self._base_color.name()};")

    def flash(self, color: str) -> None:
        if not ENABLED:
            self._restore()
            return
        self._anim.stop()
        self._anim.setStartValue(QColor(color))
        self._anim.setEndValue(QColor(self._base_color))
        self._anim.start()


# --------------------------------------------------------------------------- #
# Opacity fade-in (view transitions)
# --------------------------------------------------------------------------- #
def fade_in(widget: QWidget, duration: int = 240,
            start: float = 0.0, end: float = 1.0) -> QPropertyAnimation | None:
    """Fade ``widget`` in via a temporary opacity effect. Returns the animation
    (kept alive on the widget) or ``None`` if motion is disabled."""
    if not ENABLED:
        return None
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    # Drop the effect when done so it never costs anything at rest.
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    widget._pt_fade_anim = anim  # keep a reference so it isn't GC'd mid-flight
    anim.start()
    return anim


def cross_fade_stack(stack, index: int, duration: int = 240) -> None:
    """Switch a :class:`QStackedWidget` to ``index`` with a fade-in on the page."""
    if stack.currentIndex() == index and stack.currentWidget() is not None:
        return
    stack.setCurrentIndex(index)
    page = stack.currentWidget()
    if page is not None:
        fade_in(page, duration)
