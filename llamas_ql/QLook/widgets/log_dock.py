"""Live log + progress dock.

Streams log lines from a running reduction, shows coarse stage progress, and
offers a Cancel button.  It is a passive view: a controller connects a
:class:`~llamas_ql.QLook.workers.reduction_process.ReductionProcess`'s signals
to the slots here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_LEVEL_COLORS = {
    "DEBUG": "#888888",
    "INFO": "#d4d4d4",
    "WARNING": "#e5c07b",
    "ERROR": "#e06c75",
    "CRITICAL": "#e06c75",
}
_MAX_BLOCKS = 5000  # cap retained log lines so a long night doesn't grow unbounded


class LogDock(QWidget):
    """Read-only streaming log view with a progress bar and Cancel button."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(_MAX_BLOCKS)
        self._view.setStyleSheet(
            "QPlainTextEdit { background:#1e1e1e; color:#d4d4d4;"
            " font-family: monospace; }"
        )

        self._stage_label = QLabel("Idle", self)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setEnabled(False)

        bar = QHBoxLayout()
        bar.addWidget(self._stage_label, 1)
        bar.addWidget(self._progress, 2)
        bar.addWidget(self._cancel_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self._view)

        self._cancel_requested = None  # callable set by the controller

    # -- controller wiring ---------------------------------------------------

    def bind_cancel(self, callback) -> None:
        """Register the callable invoked when Cancel is pressed."""
        self._cancel_requested = callback
        self._cancel_btn.clicked.connect(self._on_cancel)

    def set_running(self, running: bool) -> None:
        self._cancel_btn.setEnabled(running)
        if running:
            self._progress.setValue(0)
            self._stage_label.setText("Starting…")
        else:
            self._stage_label.setText("Idle")

    # -- slots (connect ReductionProcess signals here) -----------------------

    @Slot(str, str)
    def append_log(self, level: str, text: str) -> None:
        color = _LEVEL_COLORS.get(level.upper(), "#d4d4d4")
        self._view.appendHtml(
            f'<span style="color:{color}">{_escape(text)}</span>'
        )
        self._view.moveCursor(QTextCursor.End)

    @Slot(int, str)
    def set_progress(self, pct: int, stage: str) -> None:
        self._progress.setValue(max(0, min(100, pct)))
        if stage:
            self._stage_label.setText(stage)

    @Slot(str, str)
    def set_stage(self, name: str, status: str) -> None:
        self._stage_label.setText(f"{name}: {status}")

    def _on_cancel(self) -> None:
        if self._cancel_requested is not None:
            self._cancel_requested()


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
