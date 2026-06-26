"""Drives 'Process Calibrations': generate traces from the confirmed dome flat.

On success it marks the session calib-ready (the exposure reduce path then uses
the freshly generated ``trace_dir`` instead of the packaged mastercalib).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.controllers.calib_detect import CalibPlan
from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionJob, get_worker


class CalibController(QObject):
    busy_changed = Signal(bool)
    #: Emitted on success with the list of cameras that reverted to default masters.
    trace_fallback = Signal(list)

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

    def run(self, plan: CalibPlan) -> ReductionJob:
        if self.running:
            raise RuntimeError("calibration already running")
        if not plan.is_runnable():
            raise ValueError("calibration plan needs a dome flat")
        self.session.paths.ensure_dirs()

        self._proc = get_worker().submit(
            JobSpec(
                kind="calib",
                inputs=[plan.dome_flat],
                output_dir=self.session.paths.trace_dir,
                options={"dome_flat": plan.dome_flat, "twilight": plan.twilight},
            )
        )
        self._proc.finished.connect(self._on_finished)
        self._proc.failed.connect(self._on_failed)

        self.busy_changed.emit(True)
        # The chosen bias/arc are recorded on the plan for forward use but are
        # not yet inputs to trace generation — surface that rather than dropping
        # it silently.
        if plan.bias or plan.arc:
            import os

            extras = ", ".join(
                f"{role}={os.path.basename(p)}"
                for role, p in (("bias", plan.bias), ("arc", plan.arc))
                if p
            )
            self._proc.log_line.emit(
                "INFO", f"Selected {extras} (recorded; not used by trace gen yet)."
            )
        return self._proc

    def _on_finished(self, payload: dict) -> None:
        self.session.set_calib_ready(True)
        fallback = payload.get("trace_fallback_cameras") or []
        if fallback:
            self.trace_fallback.emit(list(fallback))
        self.busy_changed.emit(False)
        self._proc = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._proc = None
