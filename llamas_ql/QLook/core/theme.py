"""Visual design system for the Quick Look GUI (light theme).

Centralizes the prototype's colors, fonts and a global Qt stylesheet so every
widget pulls from one source.  Values mirror the HTML/JS prototype at
``Observatory quick look interface`` (IBM Plex on the web; here we fall back to
the closest installed system sans/mono).
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

# -- palette ----------------------------------------------------------------

# Backgrounds / surfaces
BG = "#f4f4f2"
SURFACE = "#ffffff"
SURFACE_ALT = "#fbfbfa"
TOOLBAR_GROUP = "#eeede9"
CANVAS = "#06080c"          # dark image viewport

# Borders
BORDER = "#d9d8d3"
DIVIDER = "#ececea"
GRIDLINE = "#f0f0ee"
ROW_DIVIDER = "#f3f3f1"
CANVAS_BORDER = "#c9c8c2"

# Text
TEXT = "#1b1b19"
TEXT_SECONDARY = "#76746e"
TEXT_MUTED = "#a3a09a"
STATUSBAR_BG = "#2a2c30"
STATUSBAR_TEXT = "#c8cacd"
STATUSBAR_TEXT_DIM = "#7e8186"

# Accent / semantic
ACCENT = "#2f6fd6"
ACCENT_BG = "#eef3fc"
ACCENT_BORDER = "#cfe0fb"
GREEN = "#1f8a4c"
GREEN_DOT = "#2f9e54"
GREEN_BG = "#e7f5ec"
GREEN_BORDER = "#cfe7d6"
GREEN_SURFACE = "#f3faf5"
ORANGE = "#b07a1f"
ORANGE_TEXT = "#c0782d"
ORANGE_BG = "#fdeecb"
ORANGE_BORDER = "#f0d9b8"
ORANGE_SURFACE = "#fdf6ea"
NEW_BG = "#fffaf0"
REDUCE_DONE_BG = "#eef7f0"
REDUCE_DONE_BORDER = "#bfe3cb"
REDUCING_BG = "#fff7e8"
REDUCING_BORDER = "#e7c98f"

# Frame-type colors (status dot + dropdown text)
TYPE_COLORS = {
    "Science": "#2f6fd6",
    "Standard": "#0e8f8a",
    "Bias": "#64748b",
    "Dark": "#555a63",
    "Flat": "#c08a2d",
    "Sky Flat": "#b07a3a",
    "Arc": "#7c5cd6",
}
TYPE_ORDER = ("Science", "Standard", "Bias", "Dark", "Flat", "Sky Flat", "Arc")

# Spectrograph channel colors / bands (nm)
CHANNEL_COLORS = {"blue": "#2f6fd6", "green": "#2f9e54", "red": "#d6573f"}
CHANNEL_BANDS = {"blue": (350, 480), "green": (480, 690), "red": (690, 975)}

# Colorbar gradients (bottom -> top)
VIRIDIS_STOPS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
GRAYSCALE_STOPS = ["#0a0c12", "#7a7d82", "#eef0f3"]

# -- fonts ------------------------------------------------------------------

_SANS_CANDIDATES = ["IBM Plex Sans", "SF Pro Text", "Helvetica Neue", "Arial", "Helvetica"]
_MONO_CANDIDATES = ["IBM Plex Mono", "SF Mono", "Menlo", "Monaco", "Courier New"]

_sans_family: str | None = None
_mono_family: str | None = None


def _first_available(candidates: list[str], fallback: str) -> str:
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    return fallback


def sans_family() -> str:
    global _sans_family
    if _sans_family is None:
        _sans_family = _first_available(_SANS_CANDIDATES, "Helvetica")
    return _sans_family


def mono_family() -> str:
    global _mono_family
    if _mono_family is None:
        _mono_family = _first_available(_MONO_CANDIDATES, "Courier New")
    return _mono_family


def mono_font(size: int = 12, weight: int = QFont.Normal) -> QFont:
    f = QFont(mono_family(), size)
    f.setWeight(weight)
    return f


def sans_font(size: int = 12, weight: int = QFont.Normal) -> QFont:
    f = QFont(sans_family(), size)
    f.setWeight(weight)
    return f


# -- global stylesheet ------------------------------------------------------

def stylesheet() -> str:
    """The app-wide QSS.  Widgets add objectNames / properties to opt into bits."""
    sans = sans_family()
    return f"""
    QWidget {{
        background: {BG};
        color: {TEXT};
        font-family: "{sans}";
        font-size: 12px;
    }}
    QToolTip {{ color: {TEXT}; background: {SURFACE}; border: 1px solid {BORDER}; }}

    /* Columns */
    QWidget#headerBar {{ background: {SURFACE_ALT}; border-bottom: 1px solid {BORDER}; }}
    QWidget#leftColumn {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
    QWidget#centerColumn {{ background: {BG}; }}
    QWidget#rightColumn {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
    QWidget#viewerToolbar {{ background: {SURFACE_ALT}; border-bottom: 1px solid {DIVIDER}; }}
    QWidget#statusBar {{ background: {STATUSBAR_BG}; }}

    QScrollArea {{ border: none; background: {SURFACE}; }}
    QScrollArea > QWidget > QWidget {{ background: {SURFACE}; }}

    /* Section labels */
    QLabel[role="section"] {{
        color: {TEXT_SECONDARY}; font-size: 10px; font-weight: 600;
        letter-spacing: 1px;
    }}
    QLabel[role="count"] {{ color: {TEXT_MUTED}; font-family: "{mono_family()}"; font-size: 10px; }}
    QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
    QLabel[role="secondary"] {{ color: {TEXT_SECONDARY}; }}

    /* Generic combo / button baseline */
    QComboBox {{
        background: {SURFACE}; border: 1px solid #dcdbd6; border-radius: 5px;
        padding: 2px 6px; font-size: 11px;
    }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_BG};
        selection-color: {TEXT};
    }}
    QPushButton {{
        background: {SURFACE}; border: 1px solid #d4d3ce; border-radius: 6px;
        padding: 5px 11px; font-size: 12px; color: {TEXT_SECONDARY};
    }}
    QPushButton:hover {{ background: {SURFACE_ALT}; }}
    QPushButton:disabled {{ color: {TEXT_MUTED}; }}

    QSlider::groove:horizontal {{ height: 3px; background: #d9d8d3; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
    }}
    """


def linear_gradient_qss(stops: list[str], horizontal: bool = False) -> str:
    """A QSS linear-gradient string spanning the given color stops (start->end)."""
    x2, y2 = (1, 0) if horizontal else (0, 1)
    n = len(stops)
    parts = ", ".join(
        f"stop:{i/(n-1):.3f} {c}" for i, c in enumerate(stops)
    )
    return f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, {parts})"
