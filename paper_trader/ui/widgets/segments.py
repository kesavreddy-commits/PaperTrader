"""A small segmented control: two or three pill buttons in an outlined track.

Used for every either/or switch on the page (Stock / Options, Line / Candles,
Calls / Puts) so they all look and behave the same: the chosen segment is a
filled pill, the others are quiet labels, and the whole control is one unit.
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
    box.setContentsMargins(3, 3, 3, 3)
    box.setSpacing(2)
    group = QButtonGroup(track)
    group.setExclusive(exclusive)
    buttons: list[QPushButton] = []
    for i, label in enumerate(labels):
        b = QPushButton(label)
        b.setObjectName("Segment")
        b.setCheckable(True)
        b.setChecked(i == checked)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        group.addButton(b)
        box.addWidget(b)
        buttons.append(b)
    track._segment_group = group      # keep the exclusivity alive with the track
    return track, buttons
