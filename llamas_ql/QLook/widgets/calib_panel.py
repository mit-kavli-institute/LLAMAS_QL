"""Calibration panel: choose the afternoon-cal directory, confirm roles, process.

Auto-detects each frame's role from its header, shows them in an editable table
(low-confidence rows highlighted).  Each row carries a 'Use' radio so the observer
picks *which* file is used for a role when several share it (e.g. three dome
flats); exactly one file is active per role.  Enables 'Process Calibrations' once
a dome flat is present and emits :pyattr:`process_requested` with the plan.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.controllers.calib_detect import (
    ROLES,
    CalibPlan,
    Confidence,
    detect_calibrations,
)
from llamas_ql.QLook.core.session import Session

# Roles that designate a usable calibration product (drive the 'Use' radios and
# the status summary); 'science'/'unknown' frames carry no radio.
_USABLE_ROLES = ("bias", "dome_flat", "twilight", "arc")


class CalibPanel(QWidget):
    process_requested = Signal(object)  # CalibPlan

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._plan = CalibPlan()
        # Per-row 'Use' radios, indexed by table row (None for non-usable roles).
        self._radios: list[QRadioButton | None] = []

        self._dir_label = QLabel("(no directory)")
        self._dir_label.setStyleSheet("color:#888;")
        choose_btn = QPushButton("Choose…")
        choose_btn.clicked.connect(self._choose_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Cals:"))
        dir_row.addWidget(self._dir_label, 1)
        dir_row.addWidget(choose_btn)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["File", "Object", "Role", "Use"])
        self._table.horizontalHeader().setStretchLastSection(False)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._process_btn = QPushButton("Process Calibrations")
        self._process_btn.clicked.connect(self._on_process)
        self._process_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(dir_row)
        layout.addWidget(self._table)
        layout.addWidget(self._status)
        layout.addWidget(self._process_btn)

        if self.session.paths.calib_dir:
            self._set_dir(self.session.paths.calib_dir)

    # -- busy state ----------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        self._process_btn.setEnabled(not busy and self._plan.is_runnable())

    # -- directory & detection ----------------------------------------------

    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select calibration directory", self.session.paths.calib_dir or ""
        )
        if path:
            self._set_dir(path)

    def _set_dir(self, path: str) -> None:
        self.session.set_calib_dir(path)
        self._dir_label.setText(path)
        self._dir_label.setStyleSheet("")
        self._detect()

    def _detect(self) -> None:
        self._plan = detect_calibrations(self.session.paths.calib_dir)
        self._populate()

    def _populate(self) -> None:
        frames = self._plan.frames
        self._radios = [None] * len(frames)
        self._table.setRowCount(len(frames))
        for r, frame in enumerate(frames):
            name_item = QTableWidgetItem(frame.filename)
            obj_item = QTableWidgetItem(frame.obj)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            obj_item.setFlags(obj_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(r, 0, name_item)
            self._table.setItem(r, 1, obj_item)

            combo = QComboBox()
            combo.addItems(list(ROLES))
            combo.setCurrentText(frame.role)
            combo.currentTextChanged.connect(
                lambda role, idx=r: self._on_role_changed(idx, role)
            )
            # Flag low-confidence guesses for observer attention.
            if frame.confidence == Confidence.LOW:
                combo.setStyleSheet("QComboBox { background:#3a3320; }")
            elif frame.confidence == Confidence.NONE:
                combo.setStyleSheet("QComboBox { background:#3a2020; }")
            self._table.setCellWidget(r, 2, combo)

            # 'Use' radio: exclusivity is managed manually (per role), so each
            # radio opts out of Qt's parent-based auto-exclusive grouping.
            radio = QRadioButton()
            radio.setAutoExclusive(False)
            radio.toggled.connect(lambda on, idx=r: self._on_radio_toggled(idx, on))
            self._radios[r] = radio
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setAlignment(Qt.AlignCenter)
            cell_layout.addWidget(radio)
            self._table.setCellWidget(r, 3, cell)

        self._refresh_radio_state()
        self._update_status()

    # -- role / use selection -----------------------------------------------

    def _on_role_changed(self, row: int, role: str) -> None:
        if 0 <= row < len(self._plan.frames):
            self._plan.frames[row].role = role
        # Drop any chosen pick whose file no longer carries that role.
        for r in list(self._plan.chosen):
            path = self._plan.chosen[r]
            if not any(f.role == r and f.path == path for f in self._plan.frames):
                del self._plan.chosen[r]
        self._refresh_radio_state()
        self._update_status()

    def _on_radio_toggled(self, row: int, on: bool) -> None:
        if not on or not (0 <= row < len(self._plan.frames)):
            return
        frame = self._plan.frames[row]
        self._plan.set_chosen(frame.role, frame.path)
        # Manually enforce one active radio per role: clear same-role siblings.
        for other, radio in enumerate(self._radios):
            if radio is None or other == row:
                continue
            if self._plan.frames[other].role == frame.role and radio.isChecked():
                radio.blockSignals(True)
                radio.setChecked(False)
                radio.blockSignals(False)
        self._update_status()

    def _refresh_radio_state(self) -> None:
        """Show a radio only for usable roles and check the chosen/first per role."""
        seen_checked: set[str] = set()
        for r, frame in enumerate(self._plan.frames):
            radio = self._radios[r]
            if radio is None:
                continue
            usable = frame.role in _USABLE_ROLES
            radio.setVisible(usable)
            if not usable:
                radio.blockSignals(True)
                radio.setChecked(False)
                radio.blockSignals(False)
                continue
            # Chosen file for this role, defaulting to the first frame of it.
            chosen_path = self._plan.chosen.get(frame.role) or self._plan._first(frame.role)
            want = (
                frame.role not in seen_checked
                and frame.path == chosen_path
            )
            radio.blockSignals(True)
            radio.setChecked(want)
            radio.blockSignals(False)
            if want:
                seen_checked.add(frame.role)
                self._plan.set_chosen(frame.role, frame.path)

    # -- status --------------------------------------------------------------

    def _update_status(self) -> None:
        summary = self._role_summary()
        if self._plan.is_runnable():
            self._status.setText(f"Ready ✓   {summary}\nDome flat = {self._flat_name()}")
            self._status.setStyleSheet("color:#98c379;")
            self._process_btn.setEnabled(True)
        else:
            self._status.setText(
                f"{summary}\nNo dome flat assigned — pick one to enable processing."
            )
            self._status.setStyleSheet("color:#e5c07b;")
            self._process_btn.setEnabled(False)

    def _role_summary(self) -> str:
        counts = self._plan.role_counts()
        parts = [
            f"{counts[role]} {role.replace('_', ' ').upper()}"
            for role in _USABLE_ROLES
            if counts.get(role)
        ]
        return " · ".join(parts) if parts else "no calibration frames"

    def _flat_name(self) -> str:
        import os

        flat = self._plan.dome_flat
        return os.path.basename(flat) if flat else "(none)"

    def _on_process(self) -> None:
        if self._plan.is_runnable():
            self.process_requested.emit(self._plan)
