"""Main application window for the LLAMAS Quick Look GUI.

A fixed three-column layout matching the prototype: a top header bar, a body with
the left run panel (frames + calibrations), the center display, and the right
spectrum panel, over a bottom status bar.  Owns the :class:`Session` and the
reduce/calib/cube controllers and wires the panels together through the session.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.controllers.calib_controller import CalibController
from llamas_ql.QLook.controllers.cube_controller import CubeController
from llamas_ql.QLook.controllers.reduce_controller import ReduceController
from llamas_ql.QLook.core import settings as qsettings
from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.widgets.display_panel import DisplayPanel
from llamas_ql.QLook.widgets.frame_row import REDUCE_DONE, REDUCE_READY, REDUCE_RUNNING
from llamas_ql.QLook.widgets.header_bar import HeaderBar
from llamas_ql.QLook.widgets.run_panel import RunPanel
from llamas_ql.QLook.widgets.spectrum_panel import SpectrumPanel
from llamas_ql.QLook.widgets.status_bar_widget import StatusBarWidget


def _placeholder_column(object_name: str, text: str, width: int | None) -> QWidget:
    """A labelled stand-in column used until the real panel lands."""
    w = QWidget()
    w.setObjectName(object_name)
    if width is not None:
        w.setFixedWidth(width)
    layout = QVBoxLayout(w)
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setProperty("role", "muted")
    layout.addWidget(label)
    return w


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LLAMAS Quick Look")
        self.resize(1280, 800)

        self.session = Session()
        self.reduce_controller = ReduceController(self.session, self)
        self.calib_controller = CalibController(self.session, self)
        self.cube_controller = CubeController(self.session, self)

        self._build_ui()
        self._wire()
        self._restore_geometry()

    # -- layout --------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("rootWidget")
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.header = HeaderBar(self)
        vbox.addWidget(self.header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.left_panel = self._build_left()
        self.center_panel = self._build_center()
        self.right_panel = self._build_right()
        body_layout.addWidget(self.left_panel)
        body_layout.addWidget(self.center_panel, 1)
        body_layout.addWidget(self.right_panel)
        vbox.addWidget(body, 1)

        self.status = StatusBarWidget(self)
        vbox.addWidget(self.status)

        self.setCentralWidget(root)

    def _build_left(self) -> QWidget:
        self.run_panel = RunPanel(self.session, self)
        return self.run_panel

    def _build_center(self) -> QWidget:
        self.display_panel = DisplayPanel(self.session, self)
        return self.display_panel

    def _build_right(self) -> QWidget:
        self.spectrum_panel = SpectrumPanel(self.session, self)
        return self.spectrum_panel

    # -- wiring --------------------------------------------------------------

    def _wire(self) -> None:
        self.run_panel.frame_selected.connect(self._on_frame_selected)
        self.run_panel.reduce_requested.connect(self._on_reduce)
        self.run_panel.process_requested.connect(self._on_process)
        self.display_panel.reduce_clicked.connect(self._on_reduce)
        self.display_panel.channel_changed.connect(self._on_channel_changed)
        self.reduce_controller.busy_changed.connect(self._on_reduce_busy)
        self.calib_controller.busy_changed.connect(self._on_calib_busy)

    # -- frame selection / reduction -----------------------------------------

    def _on_frame_selected(self, item) -> None:
        self.header.set_viewing(item.filename)
        self.spectrum_panel.set_frame_meta(item.obj, item.ftype, item.exptime)
        if item.is_calib:
            self.display_panel.show_not_reduced(item)
            self.status.set_state("idle", f"calibration · {item.filename}")
        else:
            self.display_panel.show_ready(item)
            self.status.set_state("idle", f"ready · {item.filename}")
        self._selected_item = item

    def _on_reduce(self, path: str) -> None:
        if self.reduce_controller.running:
            return
        self._reducing_path = path
        self.run_panel.set_reduce_state(path, REDUCE_RUNNING)
        item = getattr(self, "_selected_item", None)
        if item is not None and item.path == path:
            self.display_panel.show_reducing(item)
        self.status.set_state("reducing", f"reducing · {path.split('/')[-1]}")
        self.reduce_controller.run(path, inspect_channel=self.display_panel.current_channel())

    def _on_reduce_busy(self, busy: bool) -> None:
        if busy:
            return
        path = getattr(self, "_reducing_path", None)
        if path is not None:
            done = self.session.current_rss is not None
            self.run_panel.set_reduce_state(path, REDUCE_DONE if done else REDUCE_READY)
            self.status.set_state(
                "complete" if done else "idle",
                "reduction complete" if done else "reduction failed",
            )
            self._reducing_path = None

    def _on_channel_changed(self, channel: str) -> None:
        # Re-pick the matching channel RSS from the last reduction, if available.
        self.status.set_cursor(f"channel · {channel}")

    def _on_process(self, plan) -> None:
        if self.calib_controller.running or not plan.is_runnable():
            return
        self.status.set_state("reducing", "processing calibrations…")
        self.calib_controller.run(plan)

    def _on_calib_busy(self, busy: bool) -> None:
        if not busy:
            ready = self.session.calib_ready
            self.status.set_state(
                "complete" if ready else "idle",
                "calibrations ready" if ready else "calibration done",
            )

    # -- geometry persistence ------------------------------------------------

    def _restore_geometry(self) -> None:
        geo = qsettings.get_bytes("main/geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def closeEvent(self, event) -> None:
        qsettings.set_bytes("main/geometry", self.saveGeometry())
        super().closeEvent(event)
