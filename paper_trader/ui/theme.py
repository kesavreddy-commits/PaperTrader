"""Visual theme: colour palettes, the Qt stylesheet, and chart colours.

The dark palette is modelled directly on Robinhood's web app: a pure-black page,
cards that are barely lifted off it with a hairline border, white type with a
grey secondary tier, and one saturated green for everything actionable. The light
palette mirrors the same structure on paper-white.

Two palettes drive both the widget stylesheet (built with ``string.Template`` so
QSS braces don't clash with substitution) and the pyqtgraph chart colours. The
active palette is cached module-side so any widget can ask for semantic colours
(gain/loss) without threading the palette through every constructor.
"""

from __future__ import annotations

from string import Template

# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #
PALETTES: dict[str, dict[str, str]] = {
    # Pure-black Robinhood aesthetic.
    "dark": {
        "bg": "#000000",
        "panel": "#000000",          # content sits straight on the page
        "card": "#08090a",           # the order card, barely lifted
        "panel2": "#0b0c0e",
        "border": "#1b1d20",         # hairline rules between regions
        "border_strong": "#2b2f34",  # card + input outlines
        "text": "#ffffff",
        "text_muted": "#9ca3af",
        "text_faint": "#6b7280",
        "green": "#00c805",
        "green_hover": "#00e606",
        "green_dim": "#0c5a17",
        "red": "#ff5000",
        "red_hover": "#ff6a26",
        "red_dim": "#6d2708",
        "accent": "#00c805",
        "accent_text": "#00140a",
        "selection": "#15181b",
        "input_bg": "#0d0f11",
        "header_bg": "#000000",
        "hover": "#121417",
        "buy": "#00c805",
        "buy_text": "#00140a",
        "sell": "#ff5000",
        "sell_text": "#1a0600",
        "scrollbar": "#26292d",
        "chart_bg": "#000000",
        "grid": "#1e2226",
        # The price line is drawn dim by default and lights up under the
        # crosshair — the two-tone look of Robinhood's own chart.
        "line_up": "#00e35c",
        "line_down": "#ff5000",
        "line_up_dim": "#a3b648",
        "line_down_dim": "#b8663c",
        "baseline": "#5a6068",
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#ffffff",
        "card": "#ffffff",
        "panel2": "#f7f8fa",
        "border": "#e6e9ee",
        "border_strong": "#d3d8e0",
        "text": "#0b0d10",
        "text_muted": "#5c636d",
        "text_faint": "#8b939e",
        "green": "#00a803",
        "green_hover": "#00bd04",
        "green_dim": "#a7e3ad",
        "red": "#e03c00",
        "red_hover": "#f04a0c",
        "red_dim": "#f5c0ac",
        "accent": "#00a803",
        "accent_text": "#ffffff",
        "selection": "#eef1f5",
        "input_bg": "#ffffff",
        "header_bg": "#ffffff",
        "hover": "#f2f4f7",
        "buy": "#00a803",
        "buy_text": "#ffffff",
        "sell": "#e03c00",
        "sell_text": "#ffffff",
        "scrollbar": "#ccd2da",
        "chart_bg": "#ffffff",
        "grid": "#eef1f5",
        "line_up": "#00a803",
        "line_down": "#e03c00",
        "line_up_dim": "#93c496",
        "line_down_dim": "#e8a88f",
        "baseline": "#a9b0ba",
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


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #
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
    border: 1px solid $border_strong;
    padding: 6px 9px;
    border-radius: 6px;
}

/* ------------------------------------------------------------------ layout */
/* A borderless region that simply sits on the page (chart column, tables). */
QFrame#Panel { background-color: $panel; border: none; }
/* The order card on the right, and any other lifted surface. */
QFrame#Card {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: 8px;
}
QFrame#Divider { background-color: $border; max-height: 1px; border: none; }
QFrame#NavBar { background-color: $bg; border-bottom: 1px solid $border; }
QFrame#Rail { background-color: $bg; border-right: 1px solid $border; }
QFrame#Strip { background-color: $bg; border-bottom: 1px solid $border; }

/* -------------------------------------------------------------------- type */
QLabel { background: transparent; }
QLabel#Ticker { font-size: 31px; font-weight: 700; letter-spacing: -0.5px; }
QLabel#BigPrice { font-size: 37px; font-weight: 700; letter-spacing: -1px; }
QLabel#H1 { font-size: 28px; font-weight: 700; }
QLabel#H2 { font-size: 17px; font-weight: 700; }
QLabel#H3 { font-size: 15px; font-weight: 700; }
QLabel#Muted { color: $text_muted; }
QLabel#Faint { color: $text_faint; }
QLabel#CardLabel { color: $text_muted; font-size: 13px; }
QLabel#CardValue { color: $text; font-size: 13px; font-weight: 600; }
QLabel#CardTotalKey { color: $text; font-size: 14px; font-weight: 700; }
QLabel#CardTotalVal { color: $text; font-size: 14px; font-weight: 700; }
QLabel#Body { color: $text_muted; font-size: 12px; }
QLabel#SectionTitle {
    color: $text_muted; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}
QLabel#Wordmark { font-size: 18px; font-weight: 700; letter-spacing: -0.2px; }

/* ------------------------------------------------------------------ inputs */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    background-color: $input_bg;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 8px 10px;
    color: $text;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1px solid $green;
}
QLineEdit#SearchBox {
    background-color: $input_bg;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 9px 12px 9px 34px;
    font-size: 13px;
}
QLineEdit#SearchBox:focus { border: 1px solid $border_strong; background-color: $hover; }
QComboBox QAbstractItemView {
    background-color: $card;
    border: 1px solid $border_strong;
    selection-background-color: $selection;
    selection-color: $text;
    outline: 0;
    padding: 4px;
}

/* ----------------------------------------------------------------- buttons */
QPushButton {
    background-color: $panel2;
    border: 1px solid $border_strong;
    border-radius: 7px;
    padding: 8px 14px;
    color: $text;
}
QPushButton:hover { background-color: $hover; }
QPushButton:pressed { background-color: $selection; }
QPushButton:disabled { color: $text_faint; border-color: $border; }

/* The card's primary action: a full-width saturated pill. */
QPushButton#BuyButton {
    background-color: $buy; color: $buy_text; border: none;
    font-weight: 700; font-size: 15px; padding: 12px; border-radius: 22px;
}
QPushButton#BuyButton:hover { background-color: $green_hover; }
QPushButton#BuyButton:disabled { background-color: $selection; color: $text_faint; }
QPushButton#SellButton {
    background-color: $sell; color: $sell_text; border: none;
    font-weight: 700; font-size: 15px; padding: 12px; border-radius: 22px;
}
QPushButton#SellButton:hover { background-color: $red_hover; }
QPushButton#SellButton:disabled { background-color: $selection; color: $text_faint; }

/* Outlined green pill (secondary card actions). */
QPushButton#Outline {
    background: transparent; border: 1px solid $green; color: $green;
    border-radius: 22px; padding: 11px; font-weight: 700; font-size: 14px;
}
QPushButton#Outline:hover { background-color: $selection; }
QPushButton#Outline:disabled { border-color: $border_strong; color: $text_faint; }

/* Top-nav text links. */
QPushButton#NavLink {
    background: transparent; border: none; color: $text;
    padding: 8px 12px; font-size: 13px; font-weight: 600;
}
QPushButton#NavLink:hover { color: $green; }
QToolButton#NavLink {
    background: transparent; border: none; color: $text;
    padding: 8px 12px; font-size: 13px; font-weight: 600;
}
QToolButton#NavLink:hover { color: $green; }
QToolButton#NavLink::menu-indicator { image: none; width: 0; }

/* Chart range selector: plain text with an underline on the active range. */
QPushButton#RangeTab {
    background: transparent; border: none; color: $text_muted;
    padding: 6px 2px; margin-right: 18px;
    font-weight: 700; font-size: 13px;
    border-bottom: 2px solid transparent;
}
QPushButton#RangeTab:hover { color: $text; }
QPushButton#RangeTab:checked { color: $text; border-bottom: 2px solid $text; }

/* Segmented toggles (Buy/Sell, Market/Limit, Stock/Options, Line/Candles). */
QPushButton#Segment {
    background-color: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 7px 14px; color: $text_muted; font-weight: 700;
}
QPushButton#Segment:hover { color: $text; background-color: $hover; }
QPushButton#Segment:checked {
    background-color: $selection; color: $text; border: 1px solid $border_strong;
}
QPushButton#SegmentBuy:checked  { background-color: $selection; color: $green; border: 1px solid $green; }
QPushButton#SegmentSell:checked { background-color: $selection; color: $red;   border: 1px solid $red; }

QPushButton#Icon {
    background: transparent; border: none; padding: 4px 8px; color: $text_muted;
    font-size: 15px;
}
QPushButton#Icon:hover { color: $text; }
QPushButton#Ghost {
    background: transparent; border: 1px solid $border_strong; color: $text_muted;
    border-radius: 6px; padding: 6px 10px; font-weight: 600;
}
QPushButton#Ghost:hover { color: $text; background-color: $hover; }

/* Contracts +/- stepper (option ticket). */
QPushButton#Stepper {
    background-color: $input_bg; border: 1px solid $border_strong;
    border-radius: 6px; padding: 6px 0; color: $text; font-size: 16px; font-weight: 700;
}
QPushButton#Stepper:hover { background-color: $hover; }
QPushButton#Stepper:disabled { color: $text_faint; border-color: $border; }

/* --------------------------------------------------------------- checkbox */
QCheckBox { color: $text_muted; spacing: 8px; font-size: 12px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid $border_strong; background: $input_bg;
}
QCheckBox::indicator:hover { border-color: $green; }
QCheckBox::indicator:checked { background: $green; border-color: $green; }

/* ------------------------------------------------------------------ tables */
QTableWidget, QTableView {
    background-color: transparent;
    alternate-background-color: transparent;
    gridline-color: transparent;
    border: none;
    selection-background-color: $selection;
    selection-color: $text;
}
QTableWidget::item, QTableView::item { padding: 7px 8px; border: none; }
QTableWidget::item:selected { background-color: $selection; }
QHeaderView { background-color: transparent; }
QHeaderView::section {
    background-color: transparent;
    color: $text_faint;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid $border;
    font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
}
QTableCornerButton::section { background-color: transparent; border: none; }

/* -------------------------------------------------------------------- tabs */
QTabWidget::pane { border: none; top: -1px; }
QTabBar::tab {
    background: transparent; color: $text_muted;
    padding: 9px 2px; margin-right: 22px;
    border: none; border-bottom: 2px solid transparent; font-weight: 700;
}
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $green; }
QTabBar::tab:hover { color: $text; }

/* -------------------------------------------------------------------- list */
QListWidget {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: 8px;
    outline: 0;
    padding: 4px;
}
QListWidget::item { padding: 9px 10px; border-radius: 6px; }
QListWidget::item:selected { background-color: $selection; color: $text; }
QListWidget::item:hover { background-color: $hover; }

QListWidget#SearchResults {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: 10px;
}

/* -------------------------------------------------------------- scrollbars */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $border_strong; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ------------------------------------------------------------------- menus */
QMenuBar { background-color: $bg; color: $text; }
QMenuBar::item { background: transparent; padding: 6px 10px; }
QMenuBar::item:selected { background: $selection; border-radius: 6px; }
QMenu { background-color: $card; border: 1px solid $border_strong; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 8px 24px; border-radius: 6px; }
QMenu::item:selected { background-color: $selection; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }

/* -------------------------------------------------------------- status bar */
QStatusBar { background-color: $bg; color: $text_muted; border-top: 1px solid $border; }
QStatusBar::item { border: none; }

/* ---------------------------------------------------------------- splitter */
/* A visible grip: an invisible handle reads as "this bar does nothing". */
QSplitter::handle { background-color: transparent; }
QSplitter::handle:horizontal { border-left: 1px solid $border; margin: 6px 4px; }
QSplitter::handle:vertical { border-top: 1px solid $border; margin: 4px 6px; }
QSplitter::handle:hover { background-color: $selection; }
QSplitter::handle:pressed { background-color: $green_dim; }
"""
)


# --------------------------------------------------------------------------- #
# Legacy palette + stylesheet (the pre-rework look, kept for `run.py --old`)
# --------------------------------------------------------------------------- #
# Card-on-grey surfaces rather than content sitting straight on black, and the
# older control shapes. Shared widgets read their colours from this module, so
# selecting the legacy theme restyles the whole window, not just the legacy
# views.
LEGACY_PALETTES: dict[str, dict[str, str]] = {
    "dark": dict(PALETTES["dark"], **{
        "panel": "#111417",
        "card": "#111417",
        "panel2": "#0a0c0e",
        "border": "#22262b",
        "border_strong": "#333a41",
        "text_muted": "#9ba1a6",
        "text_faint": "#6b7177",
        "selection": "#1b1f24",
        "input_bg": "#000000",
        "header_bg": "#0a0c0e",
        "hover": "#181c20",
        "scrollbar": "#2a3036",
        "grid": "#15181c",
        "line_up": "#00c805",
        "line_down": "#ff5000",
        "line_up_dim": "#00c805",
        "line_down_dim": "#ff5000",
        "accent_text": "#04160a",
        "buy_text": "#04160a",
        "sell_text": "#160500",
        "green_dim": "#0e7a2b",
        "red_dim": "#8f3312",
    }),
    "light": dict(PALETTES["light"], **{
        "bg": "#f6f8fa",
        "panel": "#ffffff",
        "card": "#ffffff",
        "panel2": "#f0f3f7",
        "border": "#e0e5eb",
        "border_strong": "#c7d0da",
        "text": "#0d1521",
        "text_muted": "#5b6774",
        "text_faint": "#98a4b2",
        "accent": "#2f6fed",
        "accent_text": "#ffffff",
        "selection": "#e4edff",
        "header_bg": "#f0f3f7",
        "hover": "#eef2f7",
        "scrollbar": "#c7d0da",
        "grid": "#e6ebf1",
        "line_up": "#00a803",
        "line_down": "#e5342b",
        "line_up_dim": "#00a803",
        "line_down_dim": "#e5342b",
        "green_dim": "#8fe0a0",
        "red_dim": "#f4b4b0",
    }),
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
    return _QSS.substitute(PALETTES[name])


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
    return {
        "background": p["chart_bg"],
        "grid": p["grid"],
        "axis": p["border"],
        "text": p["text_faint"],
        "up": p["line_up"],
        "down": p["line_down"],
        "up_dim": p["line_up_dim"],
        "down_dim": p["line_down_dim"],
        "neutral": p["accent"],
        "baseline": p["baseline"],
        "crosshair": p["text_muted"],
    }
