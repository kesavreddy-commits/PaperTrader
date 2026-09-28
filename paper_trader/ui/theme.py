"""Visual theme: colour palettes, the Qt stylesheet, and chart colours.

The dark palette is sampled from Robinhood's web app (the reference the user
supplied): a pure-black page; the order card lifted to ``#1e2023`` behind a
``#3c3e3f`` rule; ``#272928`` hairlines; white type over a ``#9aa1a4`` secondary
tier; and one saturated green for everything actionable. The chart borrows the
reference's session colours — a past session in olive, the live extended
session in lime, the hovered session in the brand green. The light palette
mirrors the same structure on white.

Two palettes drive both the widget stylesheet (built with ``string.Template``
so QSS braces don't clash with substitution) and the pyqtgraph chart colours.
The active palette is cached module-side so any widget can ask for semantic
colours (gain/loss) without threading the palette through every constructor.

Robinhood tints a stock's page by how that stock is doing today: its pills,
tabs and links turn from green to orange on a down day. Widgets opt into that
with the ``accent`` dynamic property (``"up"`` / ``"down"``); see
:func:`set_accent`.
"""

from __future__ import annotations

from string import Template

# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#000000",
        "panel": "#000000",           # content sits straight on the page
        "card": "#1e2023",            # the order card, lifted off the page
        "card_border": "#3c3e3f",
        "card_rule": "#2d3335",       # the rule under the card's header
        "panel2": "#0b0c0e",
        "border": "#272928",          # page hairlines (search box, stat box)
        "border_strong": "#3c3e3f",
        "rule": "#2a2c2e",            # the long rule under tab rows
        "text": "#ffffff",
        "text_muted": "#9aa1a4",
        "text_faint": "#6b7275",
        "green": "#00c805",
        "green_hover": "#1fd624",
        "green_dim": "#0c3d10",
        "green_wash": "#08200b",      # hover fill behind an outlined green pill
        "red": "#ff5000",
        "red_hover": "#ff6a26",
        "red_dim": "#4a1a05",
        "red_wash": "#2a0f04",
        "lime": "#c2f434",            # extended hours (reference: "Overnight")
        "amber": "#ffa24c",           # extended hours on a down day
        "accent": "#00c805",
        "accent_text": "#000000",
        "selection": "#1e2023",
        "input_bg": "#1e2023",        # fields inside the card match the card
        "input_border": "#3d4849",
        "field_bg": "#000000",        # fields on the page (search) match the page
        "header_bg": "#000000",
        "hover": "#141618",
        "menu_hover": "#2c3034",
        "buy": "#00c805",
        "buy_text": "#000000",
        "sell": "#ff5000",
        "sell_text": "#000000",
        "disabled_bg": "#26292c",
        "disabled_text": "#6b7275",
        "scrollbar": "#2b2e31",
        "chart_bg": "#000000",
        "grid": "#1a1c1e",
        # Chart line, per the reference: the live session bright, past
        # sessions olive, the hovered session in the brand colour.
        "line_up": "#00c805",
        "line_down": "#ff5000",
        "line_up_dim": "#627721",
        "line_down_dim": "#7c3514",
        "line_up_ext": "#c2f434",
        "line_down_ext": "#ffa24c",
        "baseline": "#606060",
        "crosshair": "#6b7777",
        "time_text": "#94a0a5",
        "check_mark": "#000000",
        "toast_bg": "#1e2023",
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "card": "#ffffff",
        "card_border": "#dfe4e8",
        "card_rule": "#e8ecef",
        "panel2": "#f5f8fa",
        "border": "#e3e8ec",
        "border_strong": "#cdd5da",
        "rule": "#e3e8ec",
        "text": "#0b0d10",
        "text_muted": "#6a747c",
        "text_faint": "#9aa4ab",
        "green": "#00a806",
        "green_hover": "#13d618",
        "green_dim": "#c9f1cb",
        "green_wash": "#eefbef",
        "red": "#e64800",
        "red_hover": "#ff6a26",
        "red_dim": "#ffd9c7",
        "red_wash": "#fff2eb",
        "lime": "#6b9a00",
        "amber": "#c46a00",
        "accent": "#00a806",
        "accent_text": "#ffffff",
        "selection": "#eef2f5",
        "input_bg": "#ffffff",
        "input_border": "#cdd5da",
        "field_bg": "#ffffff",
        "header_bg": "#ffffff",
        "hover": "#f5f8fa",
        "menu_hover": "#eef2f5",
        "buy": "#00c805",
        "buy_text": "#000000",
        "sell": "#ff5000",
        "sell_text": "#000000",
        "disabled_bg": "#eef2f5",
        "disabled_text": "#9aa4ab",
        "scrollbar": "#cdd5da",
        "chart_bg": "#ffffff",
        "grid": "#eef1f4",
        "line_up": "#00b805",
        "line_down": "#f04b00",
        "line_up_dim": "#a9bd62",
        "line_down_dim": "#eba27f",
        "line_up_ext": "#6b9a00",
        "line_down_ext": "#c46a00",
        "baseline": "#b4babf",
        "crosshair": "#a3acb2",
        "time_text": "#6a747c",
        "check_mark": "#ffffff",
        "toast_bg": "#ffffff",
    },
}

# The palette in effect right now (updated by apply_theme).
_active_name = "dark"
_active = PALETTES["dark"]
_legacy = False


def palette(name: str | None = None) -> dict[str, str]:
    return PALETTES.get(name, _active) if name else _active


def active_name() -> str:
    return _active_name


def color(key: str) -> str:
    return _active.get(key, "#ffffff")


def gain_color() -> str:
    return _active["green"]


def loss_color() -> str:
    return _active["red"]


def muted_color() -> str:
    return _active["text_muted"]


def color_for(value: float) -> str:
    """Green for positive, red for negative, muted for zero."""
    if value > 0:
        return _active["green"]
    if value < 0:
        return _active["red"]
    return _active["text_muted"]


def is_dark() -> bool:
    return _active_name == "dark"


def is_legacy() -> bool:
    return _legacy


def accent_name(change: float | None) -> str:
    """``"down"`` for a losing day, else ``"up"`` (flat reads as up, as on the web)."""
    return "down" if change is not None and change < 0 else "up"


def accent_color(change: float | None) -> str:
    return _active["red"] if accent_name(change) == "down" else _active["green"]


def set_accent(widget, name: str) -> None:
    """Point ``widget``'s ``accent`` property at up/down and restyle it if it moved."""
    if widget.property("accent") == name:
        return
    widget.setProperty("accent", name)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def tabular(font):
    """Return ``font`` with tabular (fixed-width) figures, where Qt supports it.

    Rolling and ticking numbers then change in place instead of shuffling
    sideways as their digits change width.
    """
    try:
        from PyQt6.QtGui import QFont

        font.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError, ValueError):
        pass
    return font


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #
# Pill heights are fixed in code and each radius is exactly half of that height:
# Qt silently squares off every corner of a box whose radius exceeds half its
# size, which is how the pills here used to render as plain rectangles.
PILL_HEIGHT = 44
CHIP_HEIGHT = 30
SEGMENT_TRACK_HEIGHT = 34

_QSS = Template(
    """
* { outline: 0; }
QWidget {
    background-color: $bg;
    color: $text;
    font-size: 13px;
}
QMainWindow, QDialog { background-color: $bg; }
QToolTip {
    background-color: $card;
    color: $text;
    border: 1px solid $card_border;
    padding: 6px 9px;
    border-radius: 6px;
}

/* ---------------------------------------------------------------- surfaces */
QFrame#Panel { background-color: $panel; border: none; }
/* The order card: lifted off the page behind a rule. */
QFrame#Card {
    background-color: $card;
    border: 1px solid $card_border;
    border-radius: 6px;
}
QFrame#CardRule { background-color: $card_rule; border: none; min-height: 1px; max-height: 1px; }
/* An outlined box that sits on the page (day stats, empty states). */
QFrame#Box {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
}
QFrame#Divider { background-color: $rule; border: none; min-height: 1px; max-height: 1px; }
QFrame#NavBar { background-color: $bg; border: none; }
QFrame#Strip { background-color: $bg; border: none; border-bottom: 1px solid $border; }
QFrame#Toast {
    background-color: $toast_bg;
    border: 1px solid $card_border;
    border-radius: 10px;
}
QWidget#Clear, QScrollArea#Clear, QWidget#CardViewport { background: transparent; }

/* -------------------------------------------------------------------- type */
QLabel { background: transparent; }
QLabel#Wordmark { font-size: 20px; font-weight: 600; letter-spacing: -0.3px; }
QLabel#Ticker { font-size: 32px; font-weight: 500; letter-spacing: -0.5px; }
QLabel#BigPrice { font-size: 32px; font-weight: 500; letter-spacing: -0.5px; }
QLabel#TickerChip { color: $text_muted; font-size: 13px; font-weight: 700; }
QLabel#H1 { font-size: 26px; font-weight: 600; letter-spacing: -0.4px; }
QLabel#H2 { font-size: 16px; font-weight: 700; }
QLabel#H3 { font-size: 14px; font-weight: 700; }
QLabel#Muted { color: $text_muted; }
QLabel#Faint { color: $text_faint; }
QLabel#Kicker { color: $text_muted; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; }
QLabel#SectionTitle { color: $text; font-size: 15px; font-weight: 700; }
QLabel#CardTitle { font-size: 16px; font-weight: 700; }
QLabel#CardLabel { color: $text; font-size: 13px; }
QLabel#CardValue { color: $text; font-size: 13px; font-weight: 700; }
QLabel#CardTotalKey { color: $text; font-size: 14px; font-weight: 700; }
QLabel#CardTotalVal { color: $text; font-size: 14px; font-weight: 700; }
QLabel#CardNote { color: $text_muted; font-size: 12px; }
QLabel#Body { color: $text_muted; font-size: 12px; }
QLabel#StatTitle { color: $text_muted; font-size: 12px; }
QLabel#StatValue { color: $text; font-size: 14px; font-weight: 600; }
QLabel#StatusChip {
    color: $text_muted; font-size: 12px; font-weight: 600;
    border: 1px solid $border; border-radius: 13px; padding: 4px 11px;
}
QLabel#ToastText { font-size: 13px; font-weight: 600; }
QLabel#Hint { color: $red; font-size: 12px; }

/* ------------------------------------------------------------------ fields */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    background-color: $input_bg;
    border: 1px solid $input_border;
    border-radius: 4px;
    padding: 7px 10px;
    color: $text;
    font-size: 13px;
    selection-background-color: $green;
    selection-color: $accent_text;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1px solid $text_muted;
}
QLineEdit:disabled, QComboBox:disabled { color: $text_faint; border-color: $border; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border: none; background: transparent;
}
QComboBox::down-arrow { image: url($select_arrows); width: 12px; height: 12px; }
QComboBox::down-arrow:disabled { image: url($select_arrows_disabled); }
QComboBox QAbstractItemView {
    background-color: $card;
    border: 1px solid $card_border;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: $menu_hover;
    selection-color: $text;
    outline: 0;
}
QLineEdit#SearchBox {
    background-color: $field_bg;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 8px 12px 8px 38px;
    font-size: 14px;
}
QLineEdit#SearchBox:focus { border: 1px solid $border_strong; }

/* ----------------------------------------------------------------- buttons */
QPushButton {
    background-color: transparent;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 7px 14px;
    color: $text;
    font-weight: 600;
}
QPushButton:hover { background-color: $hover; }
QPushButton:pressed { background-color: $selection; }
QPushButton:disabled { color: $text_faint; border-color: $border; }
QPushButton:default {
    background-color: $buy; color: $buy_text; border: 1px solid $buy;
}
QPushButton:default:hover { background-color: $green_hover; }

/* The card's primary action: a full-width saturated pill. It follows the
   stock's day, green or orange, the way the reference page does. */
QPushButton#BuyButton {
    background-color: $buy; color: $buy_text; border: none;
    border-radius: 22px; padding: 0 18px; font-size: 14px; font-weight: 700;
}
QPushButton#BuyButton:hover { background-color: $green_hover; }
QPushButton#BuyButton[accent="down"] { background-color: $sell; color: $sell_text; }
QPushButton#BuyButton[accent="down"]:hover { background-color: $red_hover; }
QPushButton#BuyButton:disabled { background-color: $disabled_bg; color: $disabled_text; }
QPushButton#SellButton {
    background-color: $sell; color: $sell_text; border: none;
    border-radius: 22px; padding: 0 18px; font-size: 14px; font-weight: 700;
}
QPushButton#SellButton:hover { background-color: $red_hover; }
QPushButton#SellButton:disabled { background-color: $disabled_bg; color: $disabled_text; }

/* Outlined pills under the card ("Trade AAPL Options", "Watch AAPL"). */
QPushButton#Outline {
    background: transparent; border: 1px solid $green; color: $green;
    border-radius: 22px; padding: 0 18px; font-size: 14px; font-weight: 700;
}
QPushButton#Outline:hover { background-color: $green_wash; }
QPushButton#Outline[accent="down"] { border-color: $red; color: $red; }
QPushButton#Outline[accent="down"]:hover { background-color: $red_wash; }
QPushButton#Outline:disabled { border-color: $border_strong; color: $text_faint; }

/* Top-nav text links. */
QPushButton#NavLink, QToolButton#NavLink {
    background: transparent; border: none; color: $text;
    padding: 6px 10px; font-size: 14px; font-weight: 700;
}
QPushButton#NavLink:hover, QToolButton#NavLink:hover { color: $green; }
QToolButton#NavLink::menu-indicator { image: none; width: 0; }
QPushButton#NavMenu {
    background: transparent; border: none; color: $text;
    padding: 6px 24px 6px 10px; font-size: 14px; font-weight: 700;
}
QPushButton#NavMenu:hover { color: $green; }
QPushButton#NavMenu::menu-indicator {
    image: url($chevron_down); subcontrol-origin: padding;
    subcontrol-position: center right; width: 11px; height: 11px; right: 8px;
}

/* Chart range tabs: bold, tracked caps with a short rule under the active one,
   set a few pixels below the label. */
QPushButton#RangeTab {
    background: transparent; border: none; border-radius: 0;
    color: $text; padding: 4px 0 9px 0; margin-right: 26px;
    font-size: 12px; font-weight: 700; letter-spacing: 1.4px;
    border-bottom: 2px solid transparent;
}
QPushButton#RangeTab:hover { color: $green; }
QPushButton#RangeTab[accent="down"]:hover { color: $red; }
QPushButton#RangeTab:checked { color: $text; border-bottom: 2px solid $text; }

/* Small segmented toggles (Stock/Options, Line/Candles, Calls/Puts). */
QPushButton#Segment {
    background: transparent; border: none; border-radius: 14px;
    padding: 0 13px; min-height: 28px; max-height: 28px;
    color: $text_muted; font-size: 12px; font-weight: 700;
}
QPushButton#Segment:hover { color: $text; }
QPushButton#Segment:checked { background-color: $selection; color: $text; }
QFrame#SegmentGroup {
    background-color: transparent; border: 1px solid $border; border-radius: 17px;
}

/* The card's header tabs (Buy AAPL / Sell AAPL). */
QPushButton#HeaderTab {
    background: transparent; border: none; border-radius: 0;
    padding: 0; margin-right: 20px;
    color: $text_muted; font-size: 16px; font-weight: 700;
}
QPushButton#HeaderTab:hover { color: $text; }
QPushButton#HeaderTab:checked { color: $green; }
QPushButton#HeaderTab[accent="down"]:checked { color: $red; }

/* Expiration chips (options). */
QPushButton#Chip {
    background: transparent; border: 1px solid $border_strong; border-radius: 15px;
    padding: 0 12px; min-height: 28px; max-height: 28px;
    color: $text; font-size: 12px; font-weight: 600;
}
QPushButton#Chip:hover { border-color: $text_muted; }
QPushButton#Chip:checked { background-color: $text; color: $bg; border-color: $text; }

QPushButton#Ghost {
    background: transparent; border: 1px solid $input_border; color: $text;
    border-radius: 4px; padding: 0 9px; font-size: 12px; font-weight: 700;
}
QPushButton#Ghost:hover { background-color: $menu_hover; }
QPushButton#Ghost:disabled { color: $text_faint; border-color: $border; }

/* Contracts +/- stepper (option ticket). */
QPushButton#Stepper {
    background-color: $input_bg; border: 1px solid $input_border;
    border-radius: 4px; padding: 0;
}
QPushButton#Stepper:hover { background-color: $menu_hover; }
QPushButton#Stepper:disabled { border-color: $border; }

QPushButton#IconButton, QToolButton#IconButton {
    background: transparent; border: none; border-radius: 6px; padding: 4px;
}
QPushButton#IconButton:hover, QToolButton#IconButton:hover { background-color: $menu_hover; }
QToolButton#IconButton::menu-indicator { image: none; width: 0; }

/* --------------------------------------------------------------- checkbox */
QCheckBox { color: $text; spacing: 8px; font-size: 12px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid $input_border; background: transparent;
}
QCheckBox::indicator:hover { border-color: $green; }
QCheckBox::indicator:checked { background: $green; border-color: $green; image: url($check); }

/* ------------------------------------------------------------------ tables */
QTableWidget, QTableView {
    background-color: transparent;
    alternate-background-color: transparent;
    gridline-color: transparent;
    border: none;
    selection-background-color: $selection;
    selection-color: $text;
}
QTableWidget::item, QTableView::item { padding: 0 10px; border: none; }
QTableWidget::item:selected, QTableView::item:selected { background-color: $selection; }
QHeaderView { background-color: transparent; border: none; }
QHeaderView::section {
    background-color: transparent;
    color: $text_muted;
    padding: 0 13px;
    min-height: 34px;
    border: none;
    border-bottom: 1px solid $rule;
    font-size: 12px; font-weight: 600;
}
QTableCornerButton::section { background-color: transparent; border: none; }

/* -------------------------------------------------------------------- tabs */
QTabWidget::pane { border: none; border-top: 1px solid $rule; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent; color: $text_muted;
    padding: 4px 0 10px 0; margin-right: 26px;
    border: none; border-bottom: 2px solid transparent;
    font-size: 13px; font-weight: 700;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $text; }

/* -------------------------------------------------------------------- list */
QListWidget {
    background-color: $card;
    border: 1px solid $card_border;
    border-radius: 8px;
    outline: 0;
    padding: 6px;
}
QListWidget::item { padding: 8px 10px; border-radius: 6px; }
QListWidget::item:selected { background-color: $menu_hover; color: $text; }
QListWidget::item:hover { background-color: $menu_hover; }
QListView#Watchlist { background: transparent; border: none; padding: 0; }

/* -------------------------------------------------------------- scrollbars */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 1px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $border_strong; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 1px 2px; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 3px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: $border_strong; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ------------------------------------------------------------------- menus */
QMenuBar { background-color: $bg; color: $text; }
QMenuBar::item { background: transparent; padding: 6px 10px; }
QMenuBar::item:selected { background: $selection; border-radius: 6px; }
QMenu {
    background-color: $card; border: 1px solid $card_border;
    border-radius: 8px; padding: 6px;
}
QMenu::item { padding: 7px 28px 7px 30px; border-radius: 5px; color: $text; }
QMenu::item:selected { background-color: $menu_hover; }
QMenu::item:disabled { color: $text_faint; }
QMenu::indicator { width: 14px; height: 14px; left: 9px; }
QMenu::indicator:checked { image: url($menu_check); }
QMenu::separator { height: 1px; background: $card_rule; margin: 5px 6px; }

/* ------------------------------------------------------------ dialog bits */
QGroupBox {
    border: 1px solid $border; border-radius: 6px; margin-top: 18px; padding: 10px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: $text_muted; }
QRadioButton { spacing: 8px; background: transparent; }

/* -------------------------------------------------------------- status bar */
QStatusBar { background-color: $bg; color: $text_muted; border-top: 1px solid $border; }
QStatusBar::item { border: none; }

/* ---------------------------------------------------------------- splitter */
/* A visible grip: an invisible handle reads as "this bar does nothing". */
QSplitter::handle { background-color: transparent; }
QSplitter::handle:horizontal { border-left: 1px solid $border; margin: 10px 4px; }
QSplitter::handle:vertical { border-top: 1px solid $border; margin: 4px 0; }
QSplitter::handle:hover { background-color: $selection; }
QSplitter::handle:pressed { background-color: $green_dim; }
"""
)


# --------------------------------------------------------------------------- #
# Legacy palette + stylesheet (the pre-rework look, kept for `run.py --old`)
# --------------------------------------------------------------------------- #
# Card-on-grey surfaces rather than content sitting straight on black, and the
# older control shapes. Frozen as literals: the current palettes keep evolving
# and must not leak into the classic look.
LEGACY_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#000000",
        "panel": "#111417",
        "card": "#111417",
        "panel2": "#0a0c0e",
        "border": "#22262b",
        "border_strong": "#333a41",
        "text": "#ffffff",
        "text_muted": "#9ba1a6",
        "text_faint": "#6b7177",
        "green": "#00c805",
        "green_hover": "#00e606",
        "green_dim": "#0e7a2b",
        "red": "#ff5000",
        "red_hover": "#ff6a26",
        "red_dim": "#8f3312",
        "accent": "#00c805",
        "accent_text": "#04160a",
        "selection": "#1b1f24",
        "input_bg": "#000000",
        "header_bg": "#0a0c0e",
        "hover": "#181c20",
        "buy": "#00c805",
        "buy_text": "#04160a",
        "sell": "#ff5000",
        "sell_text": "#160500",
        "scrollbar": "#2a3036",
        "chart_bg": "#000000",
        "grid": "#15181c",
        "line_up": "#00c805",
        "line_down": "#ff5000",
        "line_up_dim": "#00c805",
        "line_down_dim": "#ff5000",
        "baseline": "#5a6068",
    },
    "light": {
        "bg": "#f6f8fa",
        "panel": "#ffffff",
        "card": "#ffffff",
        "panel2": "#f0f3f7",
        "border": "#e0e5eb",
        "border_strong": "#c7d0da",
        "text": "#0d1521",
        "text_muted": "#5b6774",
        "text_faint": "#98a4b2",
        "green": "#00a803",
        "green_hover": "#00bd04",
        "green_dim": "#8fe0a0",
        "red": "#e03c00",
        "red_hover": "#f04a0c",
        "red_dim": "#f4b4b0",
        "accent": "#2f6fed",
        "accent_text": "#ffffff",
        "selection": "#e4edff",
        "input_bg": "#ffffff",
        "header_bg": "#f0f3f7",
        "hover": "#eef2f7",
        "buy": "#00a803",
        "buy_text": "#ffffff",
        "sell": "#e03c00",
        "sell_text": "#ffffff",
        "scrollbar": "#c7d0da",
        "chart_bg": "#ffffff",
        "grid": "#e6ebf1",
        "line_up": "#00a803",
        "line_down": "#e5342b",
        "line_up_dim": "#00a803",
        "line_down_dim": "#e5342b",
        "baseline": "#a9b0ba",
    },
}
_LEGACY_QSS = Template(
    """
* { outline: 0; }
QWidget { background-color: $bg; color: $text; font-size: 13px; }
QMainWindow, QDialog { background-color: $bg; }
QToolTip {
    background-color: $panel; color: $text; border: 1px solid $border_strong;
    padding: 6px 8px; border-radius: 6px;
}

/* Cards / panels */
QFrame#Panel {
    background-color: $panel; border: 1px solid $border; border-radius: 12px;
}
QFrame#Card {
    background-color: $panel; border: 1px solid $border; border-radius: 12px;
}
QFrame#HeaderBar {
    background-color: $panel2; border: 1px solid $border; border-radius: 12px;
}
QFrame#Strip { background-color: $panel2; border-bottom: 1px solid $border; }
QFrame#Divider { background-color: $border; max-height: 1px; border: none; }

QLabel { background: transparent; }
QLabel#H1 { font-size: 30px; font-weight: 700; }
QLabel#H2 { font-size: 20px; font-weight: 700; }
QLabel#H3 { font-size: 15px; font-weight: 700; }
QLabel#Ticker { font-size: 20px; font-weight: 700; }
QLabel#BigPrice { font-size: 30px; font-weight: 700; }
QLabel#Muted { color: $text_muted; }
QLabel#Faint { color: $text_faint; }
QLabel#Body { color: $text_muted; font-size: 12px; }
QLabel#CardLabel { color: $text_muted; font-size: 13px; }
QLabel#CardValue { color: $text; font-size: 13px; font-weight: 600; }
QLabel#CardTotalKey { color: $text; font-weight: 700; }
QLabel#CardTotalVal { color: $text; font-weight: 700; }
QLabel#SectionTitle {
    color: $text_muted; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}

/* Inputs */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    background-color: $input_bg; border: 1px solid $border; border-radius: 8px;
    padding: 8px 10px; selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1px solid $accent;
}
QComboBox QAbstractItemView {
    background-color: $panel; border: 1px solid $border_strong;
    selection-background-color: $selection; selection-color: $text; outline: 0;
}

/* Buttons */
QPushButton {
    background-color: $panel2; border: 1px solid $border_strong; border-radius: 8px;
    padding: 8px 14px; color: $text;
}
QPushButton:hover { background-color: $hover; }
QPushButton:pressed { background-color: $selection; }
QPushButton:disabled { color: $text_faint; border-color: $border; }

QPushButton#BuyButton {
    background-color: $buy; color: $buy_text; border: none;
    font-weight: 700; font-size: 15px; padding: 13px; border-radius: 24px;
}
QPushButton#BuyButton:disabled { background-color: $green_dim; color: $panel; }
QPushButton#SellButton {
    background-color: $sell; color: $sell_text; border: none;
    font-weight: 700; font-size: 15px; padding: 13px; border-radius: 24px;
}
QPushButton#SellButton:disabled { background-color: $red_dim; color: $panel; }

QPushButton#Outline {
    background: transparent; border: 1px solid $green; color: $green;
    border-radius: 22px; padding: 11px; font-weight: 700;
}
QPushButton#Outline:hover { background-color: $selection; }

QPushButton#RangeTab {
    background: transparent; border: none; color: $text_muted;
    padding: 4px 6px; font-weight: 700; border-bottom: 2px solid transparent;
}
QPushButton#RangeTab:hover { color: $text; }
QPushButton#RangeTab:checked { color: $text; border-bottom: 2px solid $green; }

QPushButton#Segment {
    background-color: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 5px 12px; color: $text_muted; font-weight: 600;
}
QPushButton#Segment:hover { color: $text; background-color: $hover; }
QPushButton#Segment:checked {
    background-color: $selection; color: $text; border: 1px solid $border_strong;
}
QPushButton#SegmentBuy:checked, QPushButton#SegmentSell:checked {
    background-color: $selection; color: $text; border: 1px solid $border_strong;
}
QPushButton#Ghost {
    background: transparent; border: 1px solid $border_strong; color: $text_muted;
    border-radius: 6px; padding: 6px 10px; font-weight: 600;
}
QPushButton#Ghost:hover { color: $text; background-color: $hover; }
QPushButton#Icon {
    background: transparent; border: none; padding: 4px 8px; color: $text_muted; font-size: 15px;
}
QPushButton#Stepper {
    background-color: $panel2; border: 1px solid $border_strong; border-radius: 8px;
    padding: 6px 0; color: $text; font-size: 16px; font-weight: 700;
}
QPushButton#Stepper:hover { background-color: $hover; }
QPushButton#Stepper:disabled { color: $text_faint; border-color: $border; }

QCheckBox { color: $text_muted; spacing: 8px; font-size: 12px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid $border_strong; background: $input_bg;
}
QCheckBox::indicator:checked { background: $green; border-color: $green; }

/* Tables */
QTableWidget, QTableView {
    background-color: $panel; alternate-background-color: $panel2;
    gridline-color: transparent; border: none;
    selection-background-color: $selection; selection-color: $text;
}
QTableWidget::item, QTableView::item { padding: 6px 8px; border: none; }
QHeaderView::section {
    background-color: $header_bg; color: $text_muted; padding: 7px 8px; border: none;
    border-bottom: 1px solid $border; font-size: 11px; font-weight: 700;
}
QTableCornerButton::section { background-color: $header_bg; border: none; }

/* Tabs */
QTabWidget::pane { border: none; top: -1px; }
QTabBar::tab {
    background: transparent; color: $text_muted; padding: 8px 16px; margin-right: 4px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent; }
QTabBar::tab:hover { color: $text; }

/* Lists */
QListWidget {
    background-color: $panel; border: 1px solid $border_strong; border-radius: 8px; outline: 0;
}
QListWidget::item { padding: 8px 10px; border-radius: 6px; }
QListWidget::item:selected { background-color: $selection; color: $text; }
QListWidget::item:hover { background-color: $hover; }

/* Scrollbars */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 5px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* Menus / status / splitter */
QMenu { background-color: $panel; border: 1px solid $border_strong; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 7px 22px; border-radius: 6px; }
QMenu::item:selected { background-color: $selection; }
QToolBar { background-color: $bg; border: none; spacing: 4px; padding: 4px 8px; }
QStatusBar { background-color: $panel2; color: $text_muted; border-top: 1px solid $border; }
QStatusBar::item { border: none; }
QSplitter::handle { background-color: transparent; }
QSplitter::handle:hover { background-color: $border; }
"""
)


def build_stylesheet(name: str, legacy: bool = False) -> str:
    if legacy:
        return _LEGACY_QSS.substitute(LEGACY_PALETTES[name])
    from .icons import stylesheet_assets

    values = dict(PALETTES[name])
    values.update(stylesheet_assets(PALETTES[name]))
    return _QSS.substitute(values)


# --------------------------------------------------------------------------- #
# Typeface
# --------------------------------------------------------------------------- #
# Qt matches font *families*, not CSS keywords: a stylesheet asking for
# "-apple-system" (or any family that isn't installed) sends it off building
# alias tables, warns, and then falls back to something generic. So we resolve a
# family that actually exists once and set it as the application font; the
# stylesheet only ever specifies size and weight.
_FONT_PREFERENCES = (
    "SF Pro Text", "SF Pro Display", "Inter",          # macOS / modern UI
    "Segoe UI Variable Text", "Segoe UI",              # Windows
    "Roboto", "Noto Sans", "DejaVu Sans",              # Linux
    "Helvetica Neue", "Helvetica", "Arial",            # last resorts
)
_resolved_font: str | None = None


def ui_font_family() -> str:
    """The best installed UI family: the platform's own, else a known fallback."""
    global _resolved_font
    if _resolved_font is not None:
        return _resolved_font
    try:
        from PyQt6.QtGui import QFontDatabase

        families = set(QFontDatabase.families())
        # The platform's own UI font first — but only if it is really installed;
        # some platform plugins report a placeholder name that would put us back
        # where we started.
        system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        if system in families:
            _resolved_font = system
            return _resolved_font
        for name in _FONT_PREFERENCES:
            if name in families:
                _resolved_font = name
                return _resolved_font
    except Exception:
        pass
    _resolved_font = "sans-serif"
    return _resolved_font


def apply_theme(app, name: str, legacy: bool = False) -> None:
    """Set the active palette and apply the stylesheet to the whole app.

    ``legacy`` selects the pre-rework look used by ``run.py --old``; it swaps
    both the palette every widget reads from and the stylesheet, so shared
    widgets follow the legacy window rather than mixing the two.
    """
    global _active_name, _active, _legacy
    if name not in PALETTES:
        name = "dark"
    _active_name = name
    _legacy = legacy
    _active = (LEGACY_PALETTES if legacy else PALETTES)[name]
    font = app.font()
    font.setFamily(ui_font_family())
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(name, legacy))


# --------------------------------------------------------------------------- #
# Chart colours (consumed by the chart widget / pyqtgraph)
# --------------------------------------------------------------------------- #
def chart_colors() -> dict[str, str]:
    p = _active
    # .get() fallbacks keep the frozen legacy palettes (which predate the
    # session colours) rendering exactly as they always have.
    return {
        "background": p["chart_bg"],
        "grid": p["grid"],
        "axis": p["border"],
        "text": p["text_faint"],
        "up": p["line_up"],
        "down": p["line_down"],
        "up_dim": p["line_up_dim"],
        "down_dim": p["line_down_dim"],
        "up_ext": p.get("line_up_ext", p["line_up"]),
        "down_ext": p.get("line_down_ext", p["line_down"]),
        "neutral": p["accent"],
        "baseline": p["baseline"],
        "crosshair": p.get("crosshair", p["text_muted"]),
        "time_text": p.get("time_text", p["text_faint"]),
        "tag_fill": p.get("card", p["chart_bg"]),
        "tag_border": p.get("card_border", p["border"]),
    }
