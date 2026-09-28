"""A small segmented control: two or three buttons in an outlined track.

Used for every either/or switch on the page (Stock / Options, Line / Candles,
Calls / Puts) so they all look and behave the same: the chosen segment is a
filled, softly rounded box, the others are quiet labels, and the whole control is one unit.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton

from .. import theme


def segment_group(labels: Sequence[str], checked: int = 0,
                  exclusive: bool = True) -> tuple[QFrame, list[QPushButton]]:
    """Build the track and its buttons; the caller wires ``clicked``."""
    track = QFrame()
    track.setObjectName("SegmentGroup")
    track.setFixedHeight(theme.SEGMENT_TRACK_HEIGHT)
    box = QHBoxLayout(track)
    inset = theme.SEGMENT_INSET
    box.setContentsMargins(inset, inset, inset, inset)
    box.setSpacing(2)
    # The highlight fills the track less its 1px border and the inset on each
    # side, so it sits dead centre (a taller segment would spill downwards).
    height = theme.SEGMENT_TRACK_HEIGHT - 2 * (inset + 1)
    group = QButtonGroup(track)
    group.setExclusive(exclusive)
    buttons: list[QPushButton] = []
    for i, label in enumerate(labels):
        b = QPushButton(label)
        b.setObjectName("Segment")
        b.setFixedHeight(height)
        b.setCheckable(True)
        b.setChecked(i == checked)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        group.addButton(b)
        box.addWidget(b)
        buttons.append(b)
    track._segment_group = group      # keep the exclusivity alive with the track
    return track, buttons
