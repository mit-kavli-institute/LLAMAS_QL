"""Bottom status bar: a state dot + message, cursor readout, queue and version.

Mirrors the prototype's 26px dark status strip ("awaiting selection" / "reducing…"
/ "reduction complete").
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from llamas_ql.QLook.core import theme

_STATE_COLORS = {
    "idle": "#7e8186",
    "reducing": "#e0a52f",
    "complete": "#3fbf6f",
    "ready": "#3fbf6f",
}


class StatusBarWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(26)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(18)

        self._dot = QLabel("●")
        self._state = QLabel("awaiting selection")
        self._cursor = QLabel("cursor  —")
        self._queue = QLabel("queue 0 / 0")
        self._version = QLabel("LLAMAS QL v0.9")

        mono = theme.mono_family()
        for lbl, color in (
            (self._dot, _STATE_COLORS["idle"]),
            (self._state, theme.STATUSBAR_TEXT),
            (self._cursor, theme.STATUSBAR_TEXT_DIM),
            (self._queue, theme.STATUSBAR_TEXT_DIM),
            (self._version, theme.STATUSBAR_TEXT_DIM),
        ):
            lbl.setStyleSheet(
                f"background:transparent; color:{color}; font-family:'{mono}'; font-size:11px;"
            )

        layout.addWidget(self._dot)
        layout.addWidget(self._state)
        layout.addWidget(self._cursor)
        layout.addStretch(1)
        layout.addWidget(self._queue)
        layout.addWidget(self._version)

    def set_state(self, state: str, text: str) -> None:
        """state ∈ {idle, reducing, complete, ready}; text is the human message."""
        color = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
        self._dot.setStyleSheet(
            f"background:transparent; color:{color}; font-family:'{theme.mono_family()}'; font-size:11px;"
        )
        self._state.setText(text)

    def set_cursor(self, text: str) -> None:
        self._cursor.setText(text or "cursor  —")

    def set_queue(self, done: int, total: int) -> None:
        self._queue.setText(f"queue {done} / {total}")
