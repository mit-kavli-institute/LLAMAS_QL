"""Drives the extraction-only reduction (Mode C): boxcar RSS, no interpolation.

Submits an ``extract_only`` job to the persistent worker and loads the matching
channel RSS onto the session. Mirrors :class:`ReduceController`.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.models.rss_model import RSSData
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionJob, get_worker


class ExtractOnlyController(QObject):
    busy_changed = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._job: ReductionJob | None = None
        self._inspect_channel = "green"

    @property
    def running(self) -> bool:
        return self._job is not None and self._job.running

    def cancel(self) -> None:
        if self.running:
            self._job.cancel()

    def run(self, science_file: str, inspect_channel: str = "green") -> ReductionJob:
        if self.running:
            raise RuntimeError("extraction already running")
        self._inspect_channel = inspect_channel
        self.session.paths.ensure_dirs()
        options = {}
        trace_dir = self.session.paths.trace_dir
        if os.path.isdir(trace_dir) and os.listdir(trace_dir):
            options["trace_dir"] = trace_dir
        self._job = get_worker().submit(
            JobSpec(
                kind="extract_only", inputs=[science_file],
                output_dir=self.session.paths.output_dir, options=options,
            )
        )
        self._job.finished.connect(self._on_finished)
        self._job.failed.connect(self._on_failed)
        self.busy_changed.emit(True)
        return self._job

    def _on_finished(self, payload: dict) -> None:
        rss_files = payload.get("rss_files", []) or []
        chosen = self._pick_channel(rss_files, self._inspect_channel)
        if chosen is not None:
            try:
                self.session.set_rss(RSSData(chosen))
            except Exception as exc:
                if self._job is not None:
                    self._job.log_line.emit("ERROR", f"Failed to load RSS {chosen}: {exc}")
        self.busy_changed.emit(False)
        self._job = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._job = None

    @staticmethod
    def _pick_channel(rss_files: list[str], channel: str) -> str | None:
        if not rss_files:
            return None
        for f in rss_files:
            if f"_{channel}" in os.path.basename(f).lower():
                return f
        return rss_files[0]
