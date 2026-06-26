"""Top header bar: logo, telescope/observer/viewing fields, and a live UT clock.

Mirrors the prototype's 50px top bar.  The TELESCOPE/OBSERVER fields are static
placeholders; VIEWING updates to the currently selected/loaded frame.
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from llamas_ql.QLook.core import theme


def _field(label: str, value: str) -> tuple[QWidget, QLabel]:
    """A stacked LABEL / value group; returns the widget and the value label."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(1)
    cap = QLabel(label)
    cap.setStyleSheet(
        f"color:{theme.TEXT_MUTED}; font-size:11px; letter-spacing:1px;"
    )
    val = QLabel(value)
    val.setStyleSheet(f"color:{theme.TEXT}; font-size:12px; font-weight:500;")
    lay.addWidget(cap)
    lay.addWidget(val)
    return w, val


class HeaderBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(18)

        # Logo: a small gradient dot + wordmark.
        dot = QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"border-radius:7px; background:{theme.linear_gradient_qss(['#2f6fd6','#2f9e54','#d6573f'], horizontal=True)};"
        )
        logo = QLabel("LLAMAS")
        logo.setStyleSheet("font-size:15px; font-weight:700; letter-spacing:1px;")
        sub = QLabel("QUICK LOOK")
        sub.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:12px; font-weight:500; letter-spacing:1px;"
        )
        logo_row = QHBoxLayout()
        logo_row.setSpacing(8)
        logo_row.addWidget(dot)
        logo_row.addWidget(logo)
        logo_row.addWidget(sub)
        logo_w = QWidget()
        logo_w.setLayout(logo_row)
        layout.addWidget(logo_w)

        tele, _ = _field("TELESCOPE", "Magellan II · Clay 6.5m")
        obs, _ = _field("OBSERVER", "R. Simcoe")
        view, self._viewing = _field("VIEWING", "—")
        layout.addWidget(tele)
        layout.addWidget(obs)
        layout.addWidget(view)

        layout.addStretch(1)

        # UT clock (mono), updated every second.
        clock_w = QWidget()
        clock_l = QVBoxLayout(clock_w)
        clock_l.setContentsMargins(0, 0, 0, 0)
        clock_l.setSpacing(1)
        self._time = QLabel("--:--:--")
        self._time.setAlignment(Qt.AlignRight)
        self._time.setStyleSheet(
            f"font-family:'{theme.mono_family()}'; font-size:15px; font-weight:600;"
        )
        self._date = QLabel("")
        self._date.setAlignment(Qt.AlignRight)
        self._date.setStyleSheet(
            f"font-family:'{theme.mono_family()}'; font-size:11px; color:{theme.TEXT_MUTED};"
        )
        clock_l.addWidget(self._time)
        clock_l.addWidget(self._date)
        layout.addWidget(clock_w)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def set_viewing(self, text: str) -> None:
        self._viewing.setText(text or "—")

    def _tick(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self._time.setText(now.strftime("%H:%M:%S") + "  UT")
        self._date.setText(now.strftime("%d %b %Y"))
