"""Drives Simple Cube construction from the current RSS file.

Runs the cube job in the subprocess (Ray-free but kept off the GUI thread), then
loads the resulting cube FITS as :class:`CubeData` onto the session.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.models.cube_model import CubeData
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionJob, get_worker


class CubeController(QObject):
    busy_changed = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._proc: ReductionJob | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    def cancel(self) -> None:
        if self.running:
            self._proc.cancel()

    def run(self, rss_path: str, grid_method: str = "nearest_hex") -> ReductionJob:
        if self.running:
            raise RuntimeError("cube build already running")
        self._proc = get_worker().submit(
            JobSpec(kind="cube", inputs=[rss_path], options={"grid_method": grid_method})
        )
        self._proc.finished.connect(self._on_finished)
        self._proc.failed.connect(self._on_failed)
        self.busy_changed.emit(True)
        return self._proc

    def _on_finished(self, payload: dict) -> None:
        cube_path = payload.get("cube")
        if cube_path:
            try:
                self.session.set_cube(CubeData(cube_path))
            except Exception as exc:
                if self._proc is not None:
                    self._proc.log_line.emit("ERROR", f"Failed to load cube: {exc}")
        self.busy_changed.emit(False)
        self._proc = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._proc = None
