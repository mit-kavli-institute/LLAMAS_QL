"""A single frame row in the left run panel (science or calibration).

Layout mirrors the prototype: a type-colored status dot, the filename (mono) with
an optional NEW badge, an ``OBJECT · EXP`` subtitle, a Type dropdown, and a
trailing control — a per-file Reduce button (science) or a per-role "Use" radio
(calibration).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core import theme

# Reduce-button visual states.
REDUCE_READY = "ready"
REDUCE_RUNNING = "running"
REDUCE_DONE = "done"


@dataclass
class FrameItem:
    """One scanned frame, science or calibration."""

    path: str
    filename: str
    obj: str
    exptime: float | None
    ftype: str          # one of theme.TYPE_ORDER
    is_calib: bool
    is_new: bool = False

    @property
    def exp_str(self) -> str:
        return "—" if self.exptime is None else f"{self.exptime:g}s"


class FrameRow(QWidget):
    clicked = Signal(str)            # path
    type_changed = Signal(str, str)  # path, new type
    reduce_requested = Signal(str)   # path  (science only)
    use_toggled = Signal(str, bool)  # path, checked  (calibration only)

    def __init__(self, item: FrameItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("frameRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._selected = False
        self._reduce_state = REDUCE_READY

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(9)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)

        # Filename + NEW badge, with subtitle below.
        content = QVBoxLayout()
        content.setSpacing(1)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self._name = QLabel(item.filename)
        self._name.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:12px; font-weight:500;"
        )
        name_row.addWidget(self._name)
        self._new_badge = QLabel("NEW")
        self._new_badge.setStyleSheet(
            f"color:{theme.ORANGE}; background:{theme.ORANGE_BG}; font-size:9px; font-weight:700;"
            " border-radius:3px; padding:1px 4px;"
        )
        self._new_badge.setVisible(item.is_new)
        name_row.addWidget(self._new_badge)
        name_row.addStretch(1)
        content.addLayout(name_row)

        self._subtitle = QLabel(self._subtitle_text())
        self._subtitle.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:11px;"
        )
        content.addWidget(self._subtitle)
        row.addLayout(content, 1)

        # Type dropdown.
        self._type = QComboBox()
        self._type.addItems(list(theme.TYPE_ORDER))
        self._type.setCurrentText(item.ftype)
        self._type.setFixedSize(86, 25)
        self._type.currentTextChanged.connect(self._on_type_changed)
        self._apply_type_color()
        row.addWidget(self._type, 0, Qt.AlignVCenter)

        # Trailing control.
        if item.is_calib:
            self._use = QRadioButton()
            self._use.setAutoExclusive(False)  # exclusivity managed per role
            self._use.toggled.connect(
                lambda on: self.use_toggled.emit(self.item.path, on)
            )
            row.addWidget(self._use, 0, Qt.AlignVCenter)
            self._reduce = None
        else:
            self._reduce = QPushButton()
            self._reduce.setFixedSize(27, 27)
            self._reduce.setCursor(Qt.PointingHandCursor)
            self._reduce.clicked.connect(
                lambda: self.reduce_requested.emit(self.item.path)
            )
            row.addWidget(self._reduce, 0, Qt.AlignVCenter)
            self._use = None
            self.set_reduce_state(REDUCE_READY)

        self._apply_style()

    # -- public API ----------------------------------------------------------

    @property
    def ftype(self) -> str:
        return self._type.currentText()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def set_use_checked(self, checked: bool) -> None:
        if self._use is not None:
            self._use.blockSignals(True)
            self._use.setChecked(checked)
            self._use.blockSignals(False)

    def set_type(self, ftype: str) -> None:
        self._type.blockSignals(True)
        self._type.setCurrentText(ftype)
        self._type.blockSignals(False)
        self.item.ftype = ftype
        self._apply_type_color()
        self._apply_dot()

    def set_reduce_state(self, state: str) -> None:
        if self._reduce is None:
            return
        self._reduce_state = state
        glyph, bg, fg, border, tip = {
            REDUCE_READY: ("▶", theme.ACCENT_BG, theme.ACCENT, theme.ACCENT_BORDER,
                           "Run quick reduction & display"),
            REDUCE_RUNNING: ("•", theme.REDUCING_BG, theme.ORANGE_TEXT, theme.REDUCING_BORDER,
                             "Reducing…"),
            REDUCE_DONE: ("✓", theme.REDUCE_DONE_BG, theme.GREEN, theme.REDUCE_DONE_BORDER,
                          "Displayed — click to re-reduce"),
        }[state]
        self._reduce.setText(glyph)
        self._reduce.setToolTip(tip)
        self._reduce.setStyleSheet(
            f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {border};"
            f" border-radius:6px; font-size:12px; font-weight:700; padding:0; }}"
        )

    # -- internals -----------------------------------------------------------

    def _subtitle_text(self) -> str:
        obj = self.item.obj or "—"
        return f"{obj} · {self.item.exp_str}"

    def _on_type_changed(self, text: str) -> None:
        self.item.ftype = text
        self._apply_type_color()
        self._apply_dot()
        self.type_changed.emit(self.item.path, text)

    def _apply_type_color(self) -> None:
        color = theme.TYPE_COLORS.get(self._type.currentText(), theme.TEXT)
        self._type.setStyleSheet(
            f"QComboBox {{ background:{theme.SURFACE}; border:1px solid #dcdbd6;"
            f" border-radius:5px; padding:0 4px; font-size:11px; font-weight:600; color:{color}; }}"
            "QComboBox::drop-down { border:none; width:14px; }"
        )

    def _apply_dot(self) -> None:
        color = theme.TYPE_COLORS.get(self._type.currentText(), theme.TEXT_MUTED)
        self._dot.setStyleSheet(f"background:{color}; border-radius:2px;")

    def _apply_style(self) -> None:
        self._apply_dot()
        if self._selected:
            bg = theme.ACCENT_BG
            border = f"border-left:3px solid {theme.ACCENT};"
        elif self.item.is_new:
            bg = theme.NEW_BG
            border = "border-left:3px solid transparent;"
        else:
            bg = theme.SURFACE
            border = "border-left:3px solid transparent;"
        self.setStyleSheet(
            f"QWidget#frameRow {{ background:{bg}; {border}"
            f" border-bottom:1px solid {theme.ROW_DIVIDER}; }}"
        )

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.item.path)
        super().mousePressEvent(event)
