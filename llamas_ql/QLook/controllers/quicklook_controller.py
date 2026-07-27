"""Drives the progressive Quick Look (the default Reduce action).

Submits a single ``quicklook`` job and publishes each streamed ``partial`` on the
:class:`Session` the moment it arrives, so the GUI paints products progressively:

    white-light image → per-fibre spectra (RSS) → cube

The white-light appears within seconds; when the RSS lands the hex fibre map
becomes clickable; the cube follows if it finishes in time. A slow or failed cube
never drops the white-light or spectra already shown.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.models.rss_model import RSSData
from llamas_ql.QLook.workers.protocol import JobSpec
from llamas_ql.QLook.workers.reduction_process import ReductionJob, get_worker


class QuickLookController(QObject):
    busy_changed = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._proc: ReductionJob | None = None
        self._inspect_channel = "green"

    @property
    def proc(self) -> ReductionJob | None:
        return self._proc

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    def cancel(self) -> None:
        if self.running:
            self._proc.cancel()

    def run(self, science_file: str, inspect_channel: str = "green") -> ReductionJob:
        """Start a progressive quick look for ``science_file``."""
        if self.running:
            raise RuntimeError("a reduction is already running")
        self._inspect_channel = inspect_channel
        self.session.paths.ensure_dirs()

        trace_dir = self.session.paths.trace_dir
        # Quick look defaults: cosmic-ray removal OFF for speed (toggleable).
        options = {"remove_cosmic_rays": False, "inspect_channel": inspect_channel}
        if os.path.isdir(trace_dir) and os.listdir(trace_dir):
            options["trace_dir"] = trace_dir  # else runner falls back to master calibs

        self._proc = get_worker().submit(
            JobSpec(
                kind="quicklook",
                inputs=[science_file],
                output_dir=self.session.paths.output_dir,
                options=options,
            )
        )
        self._proc.partial.connect(self._on_partial)
        self._proc.finished.connect(self._on_finished)
        self._proc.failed.connect(self._on_failed)
        self.busy_changed.emit(True)
        return self._proc

    # -- streamed products ---------------------------------------------------

    def _on_partial(self, payload: dict) -> None:
        """Publish each intermediate product as it streams in."""
        kind = payload.get("kind")
        try:
            if kind == "whitelight":
                self.session.set_whitelight(payload.get("path"))
            elif kind == "rss":
                chosen = self._pick_channel(
                    payload.get("rss_files", []) or [], self._inspect_channel)
                if chosen is not None:
                    self.session.set_rss(RSSData(chosen))
            elif kind == "cube":
                path = payload.get("path")
                if path:
                    from llamas_ql.QLook.models.cube_model import CubeData
                    self.session.set_cube(CubeData(path))
        except Exception as exc:  # surface load failure, keep the session alive
            self._emit_log("ERROR", f"Failed to load quick-look {kind}: {exc}")

    def _on_finished(self, payload: dict) -> None:
        self.busy_changed.emit(False)
        self._proc = None

    def _on_failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self._proc = None

    @staticmethod
    def _pick_channel(rss_files: list[str], channel: str) -> str | None:
        if not rss_files:
            return None
        if channel:
            for f in rss_files:
                if f"_{channel.lower()}" in os.path.basename(f).lower():
                    return f
        return rss_files[0]  # single-channel / unlabelled fallback

    def _emit_log(self, level: str, text: str) -> None:
        if self._proc is not None:
            self._proc.log_line.emit(level, text)
