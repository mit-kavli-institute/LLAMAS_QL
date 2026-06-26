"""Drives a science Quick Reduce: build the job, stream progress, load the RSS.

Owns the :class:`ReductionProcess` lifecycle for a reduction.  On success it
picks the RSS file matching the channel the observer wants to inspect, loads it
as :class:`RSSData`, and publishes it on the :class:`Session`.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.models.rss_model import RSSData
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionProcess


class ReduceController(QObject):
    busy_changed = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._proc: ReductionProcess | None = None
        self._inspect_channel = "red"
        # Signals re-exposed for the LogDock to connect to.
        self.log_line = None
        self.progress = None
        self.stage = None

    @property
    def proc(self) -> ReductionProcess | None:
        return self._proc

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    def cancel(self) -> None:
        if self.running:
            self._proc.cancel()

    def run(self, science_file: str, inspect_channel: str = "red") -> ReductionProcess:
        """Start a reduction for ``science_file``; return the process to wire UI to."""
        if self.running:
            raise RuntimeError("a reduction is already running")
        self._inspect_channel = inspect_channel
        self.session.paths.ensure_dirs()

        trace_dir = self.session.paths.trace_dir
        options = {"remove_cosmic_rays": True}
        if os.path.isdir(trace_dir) and os.listdir(trace_dir):
            options["trace_dir"] = trace_dir  # else runner falls back to mastercalib

        self._proc = ReductionProcess(self)
        self._proc.finished.connect(self._on_finished)
        self._proc.failed.connect(self._on_failed)

        self.busy_changed.emit(True)
        self._proc.start(
            JobSpec(
                kind="reduce",
                inputs=[science_file],
                output_dir=self.session.paths.output_dir,
                options=options,
            )
        )
        return self._proc

    # -- completion ----------------------------------------------------------

    def _on_finished(self, payload: dict) -> None:
        rss_files = payload.get("rss_files", []) or []
        chosen = self._pick_channel(rss_files, self._inspect_channel)
        if chosen is not None:
            try:
                self.session.set_rss(RSSData(chosen))
            except Exception as exc:  # surface load failure, stay alive
                self._emit_log("ERROR", f"Failed to load RSS {chosen}: {exc}")
        else:
            self._emit_log("WARNING", "Reduction finished but produced no RSS file.")
        self.busy_changed.emit(False)
        self._proc = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._proc = None

    @staticmethod
    def _pick_channel(rss_files: list[str], channel: str) -> str | None:
        if not rss_files:
            return None
        for f in rss_files:
            if f"_{channel}" in os.path.basename(f).lower():
                return f
        return rss_files[0]  # single-channel / unlabelled fallback

    def _emit_log(self, level: str, text: str) -> None:
        if self._proc is not None:
            self._proc.log_line.emit(level, text)
