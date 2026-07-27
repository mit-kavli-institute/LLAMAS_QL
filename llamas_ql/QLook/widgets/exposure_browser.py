"""Exposure browser: pick a night directory, refresh it, and launch reductions.

Holds the science-directory chooser, the exposure table (new files highlighted),
a channel selector for which RSS channel to inspect after a reduction, and the
Refresh / Quick Reduce actions.  Reduction is delegated to a controller.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.models.exposure_model import ExposureScanner, ExposureTableModel


class ExposureBrowser(QWidget):
    #: Emitted with the selected science file path when Quick Reduce is pressed.
    quick_reduce_requested = Signal(str)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._scanner = ExposureScanner()

        self._dir_label = QLabel("(no directory)")
        self._dir_label.setStyleSheet("color:#888;")
        choose_btn = QPushButton("Choose…")
        choose_btn.clicked.connect(self._choose_dir)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Data:"))
        dir_row.addWidget(self._dir_label, 1)
        dir_row.addWidget(choose_btn)

        self._model = ExposureTableModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.doubleClicked.connect(lambda _i: self._quick_reduce())
        # Make the picked row obvious in the dark theme.
        self._table.setStyleSheet(
            "QTableView::item:selected { background:#2d5a88; color:#ffffff; }"
        )

        # Shows which science frame Quick Reduce will run.
        self._selected_label = QLabel("(no frame selected)")
        self._selected_label.setStyleSheet("color:#888;")

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        self._reduce_btn = QPushButton("Quick Reduce")
        self._reduce_btn.clicked.connect(self._quick_reduce)
        self._reduce_btn.setEnabled(False)

        self._channel = QComboBox()
        self._channel.addItems(["red", "green", "blue"])

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._refresh_btn)
        btn_row.addWidget(self._reduce_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(QLabel("Inspect:"))
        btn_row.addWidget(self._channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(dir_row)
        layout.addWidget(self._table)
        layout.addWidget(self._selected_label)
        layout.addLayout(btn_row)

        self._table.selectionModel().selectionChanged.connect(self._on_sel_changed)
        if self.session.paths.data_dir:
            self._set_dir(self.session.paths.data_dir)

    # -- public API ----------------------------------------------------------

    @property
    def selected_channel(self) -> str:
        return self._channel.currentText()

    def set_busy(self, busy: bool) -> None:
        """Disable actions while a reduction is running."""
        self._reduce_btn.setEnabled(not busy and self._has_selection())
        self._refresh_btn.setEnabled(not busy)

    def refresh(self) -> None:
        entries = self._scanner.scan(self.session.paths.data_dir)
        self._model.set_entries(entries)

    # -- internals -----------------------------------------------------------

    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select science data directory", self.session.paths.data_dir or ""
        )
        if path:
            self._set_dir(path)

    def _set_dir(self, path: str) -> None:
        self.session.set_data_dir(path)
        self._dir_label.setText(path)
        self._dir_label.setStyleSheet("")
        self.refresh()

    def _on_sel_changed(self, *_args) -> None:
        self._reduce_btn.setEnabled(self._has_selection())
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        import os

        path = self._selected_path()
        if path:
            name = os.path.basename(path)
            self._selected_label.setText(f"Selected: {name}")
            self._selected_label.setStyleSheet("color:#98c379;")
            self._reduce_btn.setText(f"Quick Reduce → {name}")
        else:
            self._selected_label.setText("(no frame selected)")
            self._selected_label.setStyleSheet("color:#888;")
            self._reduce_btn.setText("Quick Reduce")

    def _has_selection(self) -> bool:
        return self._table.selectionModel() is not None and self._table.selectionModel().hasSelection()

    def _selected_path(self) -> str | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        entry = self._model.entry_at(rows[0].row())
        return entry.path if entry else None

    def _quick_reduce(self) -> None:
        path = self._selected_path()
        if path:
            self.quick_reduce_requested.emit(path)
