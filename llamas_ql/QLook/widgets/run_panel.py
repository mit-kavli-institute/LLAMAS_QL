"""Left column: Tonight's Run header, science + calibration frame lists, status.

Scans the night folder, classifies each frame (reusing the calib detector), and
lays the frames out as :class:`FrameRow`s under sticky SCIENCE FRAMES and
CALIBRATION section headers.  A CALIBRATION STATUS footer shows BIAS/FLAT/ARC
count tiles and a READY/INCOMPLETE badge.  Emits high-level signals the main
window wires to the controllers.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.controllers.calib_detect import (
    CalibFrame,
    CalibPlan,
    detect_calibrations,
)
from llamas_ql.QLook.core import theme
from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.widgets.frame_row import FrameItem, FrameRow

# calib_detect role  ->  (prototype Type label, is_calibration)
_ROLE_TO_PROTO = {
    "bias": ("Bias", True),
    "dome_flat": ("Flat", True),
    "twilight": ("Sky Flat", True),
    "arc": ("Arc", True),
    "science": ("Science", False),
    "unknown": ("Science", False),
}
# prototype Type label -> calib_detect role (for rebuilding a CalibPlan)
_PROTO_TO_ROLE = {
    "Bias": "bias",
    "Flat": "dome_flat",
    "Sky Flat": "twilight",
    "Arc": "arc",
    "Dark": "bias",        # treated as a bias-like dark for counting
    "Science": "science",
    "Standard": "science",
}


def _section_header(title: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    w.setAttribute(Qt.WA_StyledBackground, True)
    w.setStyleSheet(
        f"background:{theme.SURFACE_ALT}; border-bottom:1px solid {theme.GRIDLINE};"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 9, 14, 5)
    label = QLabel(title)
    label.setProperty("role", "section")
    count = QLabel("0")
    count.setProperty("role", "count")
    lay.addWidget(label)
    lay.addStretch(1)
    lay.addWidget(count)
    return w, count


class _Tile(QFrame):
    """A BIAS/FLAT/ARC count tile."""

    def __init__(self, label: str):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(2)
        self._count = QLabel("0")
        self._count.setAlignment(Qt.AlignCenter)
        self._count.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:16px; font-weight:600;"
        )
        cap = QLabel(label)
        cap.setAlignment(Qt.AlignCenter)
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:10px; letter-spacing:1px;"
        )
        lay.addWidget(self._count)
        lay.addWidget(cap)
        self.set_ready(False)

    def set_count(self, n: int) -> None:
        self._count.setText(str(n))

    def set_ready(self, ready: bool) -> None:
        if ready:
            border, bg = theme.GREEN_BORDER, theme.GREEN_SURFACE
        else:
            border, bg = theme.ORANGE_BORDER, theme.ORANGE_SURFACE
        self.setStyleSheet(
            f"_Tile {{ border:1px solid {border}; background:{bg}; border-radius:6px; }}"
        )


class RunPanel(QWidget):
    frame_selected = Signal(object)   # FrameItem
    reduce_requested = Signal(str)    # path
    process_requested = Signal(object)  # CalibPlan

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("leftColumn")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(336)

        self._rows: dict[str, FrameRow] = {}
        self._selected_path: str | None = None
        self._plan = CalibPlan()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_mode_bar())
        root.addWidget(self._build_scroll(), 1)
        root.addWidget(self._build_status())

        if self.session.paths.data_dir:
            self.scan(self.session.paths.data_dir)

    # -- header --------------------------------------------------------------

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE}; border-bottom:1px solid {theme.DIVIDER};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 11, 14, 9)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel("Tonight's Run")
        title.setStyleSheet("background:transparent; font-size:12px; font-weight:600;")
        self._night_label = QLabel("(no folder selected)")
        self._night_label.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:11px; color:{theme.TEXT_MUTED};"
        )
        col.addWidget(title)
        col.addWidget(self._night_label)
        lay.addLayout(col, 1)

        choose = QPushButton("Choose…")
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self._choose_dir)
        lay.addWidget(choose, 0, Qt.AlignVCenter)
        return w

    def _build_mode_bar(self) -> QWidget:
        """Selector for what the per-file Reduce button produces."""
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(f"background:{theme.SURFACE}; border-bottom:1px solid {theme.GRIDLINE};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 6, 14, 6)
        cap = QLabel("REDUCE AS")
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:10px; letter-spacing:1px;"
        )
        self._mode_combo = QComboBox()
        # (label, mode key)
        self._mode_combo.addItem("Quick look (fast, progressive)", "quicklook")
        self._mode_combo.addItem("White-light (binary)", "whitelight")
        self._mode_combo.addItem("Simple cube", "cube")
        self._mode_combo.addItem("Extract-only RSS", "extract")
        self._mode_combo.setCurrentIndex(0)  # progressive quick look is the default
        lay.addWidget(cap)
        lay.addWidget(self._mode_combo, 1)
        return w

    def reduction_mode(self) -> str:
        """Return the selected reduction-mode key
        ('quicklook'|'whitelight'|'cube'|'extract')."""
        return self._mode_combo.currentData()

    def _build_scroll(self) -> QWidget:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setAttribute(Qt.WA_StyledBackground, True)
        inner.setStyleSheet(f"background:{theme.SURFACE};")
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)

        self._sci_header, self._sci_count = _section_header("SCIENCE FRAMES")
        self._sci_box = QVBoxLayout()
        self._sci_box.setContentsMargins(0, 0, 0, 0)
        self._sci_box.setSpacing(0)
        self._cal_header, self._cal_count = _section_header("CALIBRATION")
        self._cal_box = QVBoxLayout()
        self._cal_box.setContentsMargins(0, 0, 0, 0)
        self._cal_box.setSpacing(0)

        self._list.addWidget(self._sci_header)
        self._list.addLayout(self._sci_box)
        self._list.addWidget(self._cal_header)
        self._list.addLayout(self._cal_box)
        self._list.addStretch(1)
        self._scroll.setWidget(inner)
        return self._scroll

    def _build_status(self) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE_ALT}; border-top:1px solid {theme.DIVIDER};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(8)

        top = QHBoxLayout()
        cap = QLabel("CALIBRATION STATUS")
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:11px; letter-spacing:1px;"
        )
        self._ready_badge = QLabel("INCOMPLETE")
        top.addWidget(cap)
        top.addStretch(1)
        top.addWidget(self._ready_badge)
        lay.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(7)
        self._tile_bias = _Tile("BIAS")
        self._tile_flat = _Tile("FLAT")
        self._tile_arc = _Tile("ARC")
        for t in (self._tile_bias, self._tile_flat, self._tile_arc):
            tiles.addWidget(t, 1)
        lay.addLayout(tiles)

        proc = QPushButton("Process calibrations")
        proc.setCursor(Qt.PointingHandCursor)
        proc.clicked.connect(self._on_process)
        self._process_btn = proc
        lay.addWidget(proc)

        self._update_status()
        return w

    # -- scanning / population ----------------------------------------------

    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select night data folder", self.session.paths.data_dir or ""
        )
        if path:
            self.session.set_data_dir(path)
            self.scan(path)

    def scan(self, directory: str) -> None:
        self._night_label.setText(directory)
        self._clear_rows()
        if not directory or not os.path.isdir(directory):
            self._update_counts()
            self._update_status()
            return
        self._plan = detect_calibrations(directory)
        for frame in self._plan.frames:
            proto, is_cal = _ROLE_TO_PROTO.get(frame.role, ("Science", False))
            item = FrameItem(
                path=frame.path, filename=frame.filename, obj=frame.obj,
                exptime=frame.exptime, ftype=proto, is_calib=is_cal,
            )
            self._add_row(item)
        self._refresh_use_radios()
        self._update_counts()
        self._update_status()

    def _add_row(self, item: FrameItem) -> None:
        row = FrameRow(item)
        row.clicked.connect(self._on_row_clicked)
        row.type_changed.connect(self._on_type_changed)
        row.reduce_requested.connect(self.reduce_requested)
        row.use_toggled.connect(self._on_use_toggled)
        self._rows[item.path] = row
        (self._cal_box if item.is_calib else self._sci_box).addWidget(row)

    def _clear_rows(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._selected_path = None

    # -- interactions --------------------------------------------------------

    def _on_row_clicked(self, path: str) -> None:
        self._selected_path = path
        for p, row in self._rows.items():
            row.set_selected(p == path)
        row = self._rows.get(path)
        if row is not None:
            self.frame_selected.emit(row.item)

    def _on_type_changed(self, path: str, new_type: str) -> None:
        # Reclassify in the underlying plan, then refresh radios/tiles/status.
        role = _PROTO_TO_ROLE.get(new_type, "unknown")
        for f in self._plan.frames:
            if f.path == path:
                f.role = role
                break
        # Move the row between sections if calib-ness changed.
        row = self._rows.get(path)
        if row is not None:
            is_cal = new_type not in ("Science", "Standard")
            if is_cal != row.item.is_calib:
                self._rebuild_row(path, new_type, is_cal)
        self._refresh_use_radios()
        self._update_counts()
        self._update_status()

    def _rebuild_row(self, path: str, new_type: str, is_cal: bool) -> None:
        old = self._rows.pop(path)
        item = old.item
        item.ftype = new_type
        item.is_calib = is_cal
        old.setParent(None)
        old.deleteLater()
        self._add_row(item)
        if self._selected_path == path:
            self._rows[path].set_selected(True)

    def _on_use_toggled(self, path: str, checked: bool) -> None:
        if not checked:
            return
        role = None
        for f in self._plan.frames:
            if f.path == path:
                role = f.role
                break
        if role is None:
            return
        self._plan.set_chosen(role, path)
        # Clear other radios in the same role.
        for p, row in self._rows.items():
            if p == path or not row.item.is_calib:
                continue
            if _PROTO_TO_ROLE.get(row.item.ftype) == role:
                row.set_use_checked(False)

    def _refresh_use_radios(self) -> None:
        seen: set[str] = set()
        for f in self._plan.frames:
            row = self._rows.get(f.path)
            if row is None or not row.item.is_calib:
                continue
            chosen = self._plan.chosen.get(f.role) or self._plan._first(f.role)
            want = f.role not in seen and f.path == chosen
            row.set_use_checked(want)
            if want:
                seen.add(f.role)
                self._plan.set_chosen(f.role, f.path)

    def _on_process(self) -> None:
        self.process_requested.emit(self._plan)

    # -- status --------------------------------------------------------------

    def _counts(self) -> tuple[int, int, int, int]:
        nb = nf = na = nsci = 0
        for row in self._rows.values():
            t = row.item.ftype
            if t in ("Science", "Standard"):
                nsci += 1
            elif t in ("Bias", "Dark"):
                nb += 1
            elif t in ("Flat", "Sky Flat"):
                nf += 1
            elif t == "Arc":
                na += 1
        return nb, nf, na, nsci

    def _update_counts(self) -> None:
        nb, nf, na, nsci = self._counts()
        ncal = sum(1 for r in self._rows.values() if r.item.is_calib)
        self._sci_count.setText(str(nsci))
        self._cal_count.setText(str(ncal))
        self._tile_bias.set_count(nb)
        self._tile_flat.set_count(nf)
        self._tile_arc.set_count(na)

    def _update_status(self) -> None:
        nb, nf, na, _ = self._counts()
        ready = nb > 0 and nf > 0 and na > 0
        self._tile_bias.set_ready(nb > 0)
        self._tile_flat.set_ready(nf > 0)
        self._tile_arc.set_ready(na > 0)
        if ready:
            self._ready_badge.setText("READY")
            self._ready_badge.setStyleSheet(
                f"color:{theme.GREEN}; background:{theme.GREEN_BG}; font-size:10px;"
                " font-weight:600; letter-spacing:1px; border-radius:10px; padding:2px 8px;"
            )
        else:
            self._ready_badge.setText("INCOMPLETE")
            self._ready_badge.setStyleSheet(
                f"color:{theme.ORANGE}; background:{theme.ORANGE_BG}; font-size:10px;"
                " font-weight:600; letter-spacing:1px; border-radius:10px; padding:2px 8px;"
            )
        self._process_btn.setEnabled(self._plan.is_runnable())

    # -- reduce-state passthrough (called by main window) --------------------

    def set_reduce_state(self, path: str, state: str) -> None:
        row = self._rows.get(path)
        if row is not None:
            row.set_reduce_state(state)
