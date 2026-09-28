"""Vector icons, drawn from small inline SVGs in the active palette's colours.

Text glyphs (``⌕``, ``⌄``, ``◗``) render at the mercy of whatever font has them,
and never quite line up with the text beside them. These icons are drawn from
SVG at the screen's pixel density instead, so they stay crisp and sit where
they are put.

Two consumers:

* widgets ask for a :class:`QIcon` / :class:`QPixmap` via :func:`icon` and
  :func:`pixmap`;
* the stylesheet references a few of them by file (Qt style sheets can only
  load images from a path), which :func:`stylesheet_assets` writes out once per
  palette into a private temp directory.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

# Every icon is drawn on a 24x24 grid; ``{c}`` is the stroke/fill colour.
_SVG: dict[str, str] = {
    # A folded paper plane: the brand mark.
    "logo": (
        '<path d="M2.2 10.6 21.4 2.6c.5-.2 1 .3.8.8l-8 19.2c-.2.5-.9.5-1.1 0'
        'l-2.6-6.9-6.9-2.6c-.5-.2-.5-.9 0-1.1z" fill="{c}"/>'
        '<path d="M10.5 13.7 21.6 2.9" stroke="#000" stroke-opacity=".38" '
        'stroke-width="1.4" stroke-linecap="round" fill="none"/>'
    ),
    "search": (
        '<circle cx="10.5" cy="10.5" r="6.6" stroke="{c}" stroke-width="2.2" fill="none"/>'
        '<path d="m15.4 15.4 5.1 5.1" stroke="{c}" stroke-width="2.4" '
        'stroke-linecap="round"/>'
    ),
    "chevron_down": (
        '<path d="m6 9.5 6 6 6-6" stroke="{c}" stroke-width="2.4" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # A slim, quiet chevron for select fields: the field's border already says
    # "box", so the mark only has to say "opens" — no heavy up/down pair.
    "chevron_select": (
        '<path d="m7 10 5 5 5-5" stroke="{c}" stroke-width="1.9" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "check": (
        '<path d="m5.5 12.5 4.2 4.2 8.8-9.4" stroke="{c}" stroke-width="3" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "close": (
        '<path d="m7 7 10 10M17 7 7 17" stroke="{c}" stroke-width="2.2" '
        'stroke-linecap="round"/>'
    ),
    "check_circle": (
        '<circle cx="12" cy="12" r="10" fill="{c}"/>'
        '<path d="m7.6 12.3 3.1 3.1 5.8-6.2" stroke="#000" stroke-width="2.4" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "alert": (
        '<circle cx="12" cy="12" r="10" fill="{c}"/>'
        '<path d="M12 7v6.2" stroke="#000" stroke-width="2.4" stroke-linecap="round"/>'
        '<circle cx="12" cy="16.9" r="1.4" fill="#000"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="10" fill="{c}"/>'
        '<path d="M12 11v6" stroke="#000" stroke-width="2.4" stroke-linecap="round"/>'
        '<circle cx="12" cy="7.3" r="1.4" fill="#000"/>'
    ),
    "plus": (
        '<path d="M12 5v14M5 12h14" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>'
    ),
    "minus": (
        '<path d="M5 12h14" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>'
    ),
    "sidebar": (
        '<rect x="3" y="4.5" width="18" height="15" rx="2.5" stroke="{c}" '
        'stroke-width="2" fill="none"/><path d="M9 4.5v15" stroke="{c}" stroke-width="2"/>'
    ),
}


def _svg(name: str, color: str) -> bytes:
    body = _SVG[name].replace("{c}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24">{body}</svg>').encode()


def _device_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return max(1.0, screen.devicePixelRatio()) if screen is not None else 1.0


def pixmap(name: str, color: str, size: int, ratio: float | None = None) -> QPixmap:
    """``name`` drawn at ``size`` logical pixels, sharp at the screen's density."""
    ratio = ratio or _device_ratio()
    px = max(1, round(size * ratio))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str, size: int = 16) -> QIcon:
    """A :class:`QIcon` carrying 1x/2x/3x renditions of ``name``."""
    result = QIcon()
    for ratio in (1.0, 2.0, 3.0):
        result.addPixmap(pixmap(name, color, size, ratio))
    return result


def app_icon() -> QIcon:
    """The brand mark on a black, rounded tile — the window, Dock and app-switcher icon.

    Drawn rather than shipped as a file, so it stays in step with the mark in
    the nav bar and needs no asset on disk.
    """
    result = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        tile = QPixmap(size, size)
        tile.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tile)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0b0c0e"))
        margin = size * 0.06                       # leave the tile's own breathing room
        # Rounded like every other Dock icon — the one tile the boxy look leaves alone.
        painter.drawRoundedRect(QRectF(margin, margin, size - 2 * margin, size - 2 * margin),
                                size * 0.22, size * 0.22)
        mark = size * 0.52
        offset = (size - mark) / 2
        renderer = QSvgRenderer(QByteArray(_svg("logo", "#00c805")))
        renderer.render(painter, QRectF(offset, offset, mark, mark))
        painter.end()
        result.addPixmap(tile)
    return result


# --------------------------------------------------------------------------- #
# Files for the stylesheet
# --------------------------------------------------------------------------- #
# (file name, icon, palette key) — what the stylesheet refers to by path.
_STYLESHEET_ICONS = (
    ("select-arrows.svg", "chevron_select", "text_muted"),
    ("select-arrows-disabled.svg", "chevron_select", "text_faint"),
    ("check.svg", "check", "check_mark"),
    ("menu-check.svg", "check", "green"),
)


def stylesheet_assets(palette: dict[str, str]) -> dict[str, str]:
    """Write the stylesheet's icons for ``palette``; return ``{stem: url_path}``.

    Files live under a per-user temp directory keyed by the colours they were
    drawn in, so switching themes never overwrites a file Qt is showing, and a
    second launch reuses what the first one wrote.
    """
    colors = "".join(palette.get(key, "") for _f, _n, key in _STYLESHEET_ICONS)
    digest = hashlib.sha1(colors.encode()).hexdigest()[:10]
    user = "".join(ch for ch in os.environ.get("USER", os.environ.get("USERNAME", "user"))
                   if ch.isalnum()) or "user"
    folder = os.path.join(tempfile.gettempdir(), f"paper-trader-icons-{user}", digest)
    os.makedirs(folder, exist_ok=True)
    paths: dict[str, str] = {}
    for filename, name, key in _STYLESHEET_ICONS:
        path = os.path.join(folder, filename)
        data = _svg(name, palette.get(key, "#ffffff"))
        try:
            with open(path, "rb") as fh:
                current = fh.read()
        except OSError:
            current = b""
        if current != data:
            with open(path, "wb") as fh:
                fh.write(data)
        # Style sheets want forward slashes, on Windows too.
        paths[filename.rsplit(".", 1)[0].replace("-", "_")] = path.replace(os.sep, "/")
    return paths
