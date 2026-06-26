"""Main application window for the LLAMAS Quick Look GUI.

A fixed three-column layout matching the prototype: a top header bar, a body with
the left run panel (frames + calibrations), the center display, and the right
spectrum panel, over a bottom status bar.  Owns the :class:`Session` and the
reduce/calib/cube controllers and wires the panels together through the session.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.controllers.calib_controller import CalibController
from llamas_ql.QLook.controllers.cube_controller import CubeController
from llamas_ql.QLook.controllers.extract_only_controller import ExtractOnlyController
from llamas_ql.QLook.controllers.reduce_controller import ReduceController
from llamas_ql.QLook.controllers.whitelight_controller import WhiteLightController
from llamas_ql.QLook.core import settings as qsettings
from llamas_ql.QLook.core import theme
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
        self.whitelight_controller = WhiteLightController(self.session, self)
        self.extract_controller = ExtractOnlyController(self.session, self)

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

        self.banner = self._build_banner()
        vbox.addWidget(self.banner)

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

    def _build_banner(self) -> QWidget:
        """A dismissable warning bar (e.g. default-master traces in use)."""
        w = QWidget()
        w.setObjectName("banner")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"QWidget#banner {{ background:{theme.ORANGE_BG}; "
            f"border-bottom:1px solid {theme.ORANGE_BORDER}; }}"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 6, 10, 6)
        self._banner_label = QLabel("")
        self._banner_label.setWordWrap(True)
        self._banner_label.setStyleSheet(
            f"background:transparent; color:{theme.ORANGE}; font-size:11px; font-weight:600;"
        )
        dismiss = QPushButton("✕")
        dismiss.setFixedSize(20, 20)
        dismiss.setCursor(Qt.PointingHandCursor)
        dismiss.clicked.connect(lambda: self.banner.setVisible(False))
        lay.addWidget(self._banner_label, 1)
        lay.addWidget(dismiss)
        w.setVisible(False)
        return w

    def show_banner(self, text: str) -> None:
        self._banner_label.setText(text)
        self.banner.setVisible(True)

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
        self.calib_controller.busy_changed.connect(self._on_calib_busy)
        self.calib_controller.trace_fallback.connect(self._on_trace_fallback)
        self.session.whitelight_loaded.connect(self._on_whitelight_loaded)
        self._reducing_path = None

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

    def _reduce_busy(self) -> bool:
        return any(c.running for c in (
            self.reduce_controller, self.whitelight_controller,
            self.extract_controller, self.cube_controller))

    def _on_reduce(self, path: str) -> None:
        if self._reduce_busy():
            return
        mode = self.run_panel.reduction_mode()
        self._reducing_path = path
        self.run_panel.set_reduce_state(path, REDUCE_RUNNING)
        item = getattr(self, "_selected_item", None)
        if item is not None and item.path == path:
            self.display_panel.show_reducing(item)
        self.status.set_state("reducing", f"reducing ({mode}) · {os.path.basename(path)}")

        channel = self.display_panel.current_channel()
        if mode == "whitelight":
            job = self.whitelight_controller.run(path)
            job.finished.connect(lambda _p: self._reduce_done(path, True))
        elif mode == "extract":
            job = self.extract_controller.run(path, inspect_channel=channel)
            job.finished.connect(lambda _p: self._reduce_done(path, True))
        else:  # cube: optimal reduce -> RSS -> build cube
            job = self.reduce_controller.run(path, inspect_channel=channel)
            job.finished.connect(lambda payload: self._chain_cube(path, payload, channel))
        job.failed.connect(lambda msg: self._reduce_done(path, False, msg))
        job.log_line.connect(self._on_job_log)

    def _chain_cube(self, path: str, payload: dict, channel: str) -> None:
        rss = ExtractOnlyController._pick_channel(payload.get("rss_files", []) or [], channel)
        if not rss:
            self._reduce_done(path, False, "no RSS produced")
            return
        self.status.set_state("reducing", "building cube…")
        cjob = self.cube_controller.run(rss)
        cjob.finished.connect(lambda _p: self._reduce_done(path, True))
        cjob.failed.connect(lambda msg: self._reduce_done(path, False, msg))
        cjob.log_line.connect(self._on_job_log)

    def _reduce_done(self, path: str, ok: bool, msg: str = "") -> None:
        self.run_panel.set_reduce_state(path, REDUCE_DONE if ok else REDUCE_READY)
        self.status.set_state(
            "complete" if ok else "idle",
            "reduction complete" if ok else f"reduction failed: {msg}".rstrip(": "),
        )
        self._reducing_path = None

    def _on_job_log(self, level: str, text: str) -> None:
        if level.upper() in ("WARNING", "ERROR"):
            self.show_banner(text)

    def _on_whitelight_loaded(self, path) -> None:
        if path:
            self.status.set_state("complete", f"white-light ready · {os.path.basename(str(path))}")

    def _on_channel_changed(self, channel: str) -> None:
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

    def _on_trace_fallback(self, cameras: list) -> None:
        n = len(cameras)
        self.show_banner(
            f"⚠ {n} camera(s) reverted to DEFAULT master traces — "
            f"reductions use packaged traces for: " + "; ".join(cameras)
        )

    # -- geometry persistence ------------------------------------------------

    def _restore_geometry(self) -> None:
        geo = qsettings.get_bytes("main/geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def closeEvent(self, event) -> None:
        qsettings.set_bytes("main/geometry", self.saveGeometry())
        from llamas_ql.QLook.workers.reduction_process import get_worker

        get_worker().shutdown()
        super().closeEvent(event)
