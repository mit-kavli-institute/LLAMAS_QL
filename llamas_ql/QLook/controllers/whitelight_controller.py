"""Drives the quick binary white-light reduction (Mode A).

Submits a ``whitelight`` job to the persistent worker and publishes the resulting
white-light FITS path on the session. Mirrors :class:`CubeController`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionJob, get_worker


class WhiteLightController(QObject):
    busy_changed = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._job: ReductionJob | None = None

    @property
    def running(self) -> bool:
        return self._job is not None and self._job.running

    def cancel(self) -> None:
        if self.running:
            self._job.cancel()

    def run(self, science_file: str) -> ReductionJob:
        if self.running:
            raise RuntimeError("white-light already running")
        self.session.paths.ensure_dirs()
        options = {}
        trace_dir = self.session.paths.trace_dir
        import os

        if os.path.isdir(trace_dir) and os.listdir(trace_dir):
            options["trace_dir"] = trace_dir
        self._job = get_worker().submit(
            JobSpec(
                kind="whitelight", inputs=[science_file],
                output_dir=self.session.paths.output_dir, options=options,
            )
        )
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self.busy_changed.emit(True)
        return self._job

    def _on_finished(self, payload: dict) -> None:
        path = payload.get("whitelight")
        if path:
            self.session.set_whitelight(path)
        self.busy_changed.emit(False)
        self._job = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._job = None
