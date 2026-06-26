"""GUI-side wrapper around the reduction subprocess.

Launches ``python -m llamas_ql.QLook.workers.runner`` via :class:`QProcess`,
feeds it a :class:`JobSpec` on stdin, and translates the NDJSON it streams back
into Qt signals.  Keeps all Ray/pypeit work out of the GUI process.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from llamas_ql.QLook.workers import protocol
from llamas_ql.QLook.workers.protocol import JobSpec, REDUCE_STAGES, CALIB_STAGES

#: Grace period (ms) after terminate() before we hard-kill the child.
_KILL_GRACE_MS = 5000


class ReductionProcess(QObject):
    """Runs one :class:`JobSpec` in a child process, emitting progress signals.

    Signals
    -------
    log_line(level, text):  a log/raw line for the LogDock.
    progress(pct, stage):   coarse or fine progress, 0-100.
    stage(name, status):    a pipeline stage changed state (start/done/skip/fail).
    finished(payload):      job completed successfully; ``payload`` holds result paths.
    failed(message):        job errored, was cancelled, or the process crashed.
    """

    log_line = Signal(str, str)
    progress = Signal(int, str)
    stage = Signal(str, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._result: dict | None = None
        self._error: str | None = None
        self._cancelling = False
        self._kill_timer: QTimer | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def stage_ladder(self, kind: str) -> tuple[str, ...]:
        """The expected stage sequence for a job kind (for UI scaffolding)."""
        return CALIB_STAGES if kind == "calib" else REDUCE_STAGES

    def start(self, job: JobSpec) -> None:
        if self.running:
            raise RuntimeError("a reduction is already running")
        self._buf = ""
        self._result = None
        self._error = None
        self._cancelling = False

        proc = QProcess(self)
        # Merge stderr into stdout so bare print()/Ray/pypeit chatter is captured
        # alongside our NDJSON events on a single ordered channel.
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc

        # Use the same interpreter so the child inherits the env and the
        # llamas_ql/__init__.py Ray/signal monkey-patch.
        proc.start(sys.executable, ["-u", "-m", "llamas_ql.QLook.workers.runner"])
        if not proc.waitForStarted(5000):
            self._proc = None
            self.failed.emit("failed to start reduction subprocess")
            return
        proc.write(job.to_json().encode("utf-8"))
        proc.closeWriteChannel()

    def cancel(self) -> None:
        """Request cancellation: SIGTERM now, SIGKILL if the child lingers."""
        if not self.running:
            return
        self._cancelling = True
        self.log_line.emit("WARNING", "Cancelling reduction…")
        self._proc.terminate()
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)
        self._kill_timer.start(_KILL_GRACE_MS)

    def _force_kill(self) -> None:
        if self.running:
            self.log_line.emit("ERROR", "Subprocess did not exit; killing.")
            self._proc.kill()

    # -- stdout parsing ------------------------------------------------------

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        chunk = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += chunk
        # Process only complete lines; keep any trailing partial in the buffer.
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        event = protocol.parse_event(line)
        if event is None:
            text = line.rstrip()
            if text:
                self.log_line.emit("INFO", text)
            return
        etype = event.get("type")
        if etype == "log":
            self.log_line.emit(event.get("level", "INFO"), event.get("msg", ""))
        elif etype == "progress":
            self.progress.emit(int(event.get("pct", 0)), event.get("stage", ""))
        elif etype == "stage":
            self.stage.emit(event.get("stage", ""), event.get("status", ""))
        elif etype == "result":
            self._result = dict(event.get("payload", {}))
        elif etype == "error":
            self._error = event.get("msg", "unknown error")

    # -- completion ----------------------------------------------------------

    def _on_finished(self, exit_code: int, exit_status) -> None:
        if self._kill_timer is not None:
            self._kill_timer.stop()
            self._kill_timer = None
        # Drain any buffered partial line.
        if self._buf.strip():
            self._handle_line(self._buf)
            self._buf = ""
        proc = self._proc
        self._proc = None

        if self._cancelling:
            self.failed.emit("Reduction cancelled.")
            return
        if self._error is not None:
            self.failed.emit(self._error)
            return
        if exit_code != 0 or exit_status == QProcess.CrashExit:
            self.failed.emit(f"Reduction subprocess exited with code {exit_code}.")
            return
        self.finished.emit(self._result or {})

    def _on_proc_error(self, err) -> None:
        # Only surface spawn/IO failures here; normal exits go through finished.
        if err == QProcess.FailedToStart:
            self.failed.emit("Reduction subprocess failed to start.")
