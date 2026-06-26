"""GUI-side wrapper around the reduction subprocess.

Launches ``python -m llamas_ql.QLook.workers.runner`` via :class:`QProcess`,
feeds it a :class:`JobSpec` on stdin, and translates the NDJSON it streams back
into Qt signals.  Keeps all Ray/pypeit work out of the GUI process.
"""

from __future__ import annotations

import itertools
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from llamas_ql.QLook.workers import protocol
from llamas_ql.QLook.workers.protocol import (
    CALIB_STAGES,
    EXTRACT_STAGES,
    REDUCE_STAGES,
    WHITELIGHT_STAGES,
    JobSpec,
)

#: Grace period (ms) after terminate() before we hard-kill the child.
_KILL_GRACE_MS = 5000

#: Expected stage ladders per job kind (for UI scaffolding).
_STAGE_LADDERS = {
    "calib": CALIB_STAGES,
    "whitelight": WHITELIGHT_STAGES,
    "extract_only": EXTRACT_STAGES,
}


def stage_ladder(kind: str) -> tuple[str, ...]:
    return _STAGE_LADDERS.get(kind, REDUCE_STAGES)


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


class ReductionJob(QObject):
    """A single job's handle on the persistent worker.

    Exposes the same signals as :class:`ReductionProcess` so controllers wire it
    identically; the worker routes ``job_id``-tagged events here.
    """

    log_line = Signal(str, str)
    progress = Signal(int, str)
    stage = Signal(str, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, job_id: str, spec: JobSpec, worker: "ReductionWorker"):
        super().__init__(worker)
        self.job_id = job_id
        self.spec = spec
        self._worker = worker
        self._done = False

    @property
    def running(self) -> bool:
        return not self._done

    def cancel(self) -> None:
        """Best-effort cancel: drop if still queued, else abort the worker."""
        if not self._done:
            self._worker.cancel(self.job_id)

    def _emit_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self.log_line.emit(event.get("level", "INFO"), event.get("msg", ""))
        elif etype == "progress":
            self.progress.emit(int(event.get("pct", 0)), event.get("stage", ""))
        elif etype == "stage":
            self.stage.emit(event.get("stage", ""), event.get("status", ""))

    def _complete(self, payload: dict | None, error: str | None) -> None:
        if self._done:
            return
        self._done = True
        if error is not None:
            self.failed.emit(error)
        else:
            self.finished.emit(payload or {})


class ReductionWorker(QObject):
    """Long-lived ``runner --serve`` process serving a serial queue of jobs.

    The heavy backend (Ray, pypeit, the ~4.5 GB reference arc) loads once and is
    reused across jobs. Jobs run one-at-a-time (Ray-safe); :meth:`submit` returns
    a :class:`ReductionJob` whose signals fire as its events arrive.
    """

    started = Signal()
    crashed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._ids = itertools.count(1)
        self._jobs: dict[str, ReductionJob] = {}
        self._queue: list[str] = []
        self._current: str | None = None
        self._cancelled: str | None = None  # job whose kill is a deliberate cancel

    # -- lifecycle -----------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def ensure_started(self) -> bool:
        if self.alive:
            return True
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_proc_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc
        proc.start(sys.executable, ["-u", "-m", "llamas_ql.QLook.workers.runner", "--serve"])
        if not proc.waitForStarted(5000):
            self._proc = None
            return False
        self.started.emit()
        return True

    def submit(self, spec: JobSpec) -> ReductionJob:
        """Queue ``spec`` (a fresh job_id is assigned) and return its handle."""
        if not self.ensure_started():
            job = ReductionJob("dead", spec, self)
            QTimer.singleShot(0, lambda: job._complete(None, "worker failed to start"))
            return job
        job_id = f"job-{next(self._ids)}"
        spec.job_id = job_id
        job = ReductionJob(job_id, spec, self)
        self._jobs[job_id] = job
        self._queue.append(job_id)
        self._pump()
        return job

    def cancel(self, job_id: str) -> None:
        """Cancel a job: remove if queued; if it is running, restart the worker."""
        if job_id in self._queue:
            self._queue.remove(job_id)
            self._finish(job_id, None, "cancelled")
            return
        if job_id == self._current and self.alive:
            # No clean mid-run cancel in a shared process: abort the current job by
            # killing the worker; _on_proc_finished fails only it and resumes the
            # rest of the queue in a fresh worker.
            self._cancelled = job_id
            self._proc.kill()

    def shutdown(self) -> None:
        if self.alive:
            self._proc.closeWriteChannel()
            self._proc.terminate()
            if not self._proc.waitForFinished(2000):
                self._proc.kill()

    # -- queue pump ----------------------------------------------------------

    def _pump(self) -> None:
        """Dispatch the next queued job when idle (serial execution).

        Restarts the worker first if it died (e.g. after a cancel-kill) so queued
        jobs survive the abort of an earlier one.
        """
        if self._current is not None or not self._queue:
            return
        if not self.alive and not self.ensure_started():
            self._fail_all("reduction worker failed to restart")
            return
        self._current = self._queue.pop(0)
        spec = self._jobs[self._current].spec
        self._proc.write((spec.to_json() + "\n").encode("utf-8"))

    # -- stdout routing ------------------------------------------------------

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._route(line)

    def _route(self, line: str) -> None:
        event = protocol.parse_event(line)
        if event is None:
            return  # bare prints / Ray chatter: dropped (not job-correlated)
        job_id = event.get("job_id") or self._current
        job = self._jobs.get(job_id)
        if job is None:
            return
        etype = event.get("type")
        if etype == "result":
            self._finish(job_id, dict(event.get("payload", {})), None)
        elif etype == "error":
            self._finish(job_id, None, event.get("msg", "unknown error"))
        else:
            job._emit_event(event)

    def _finish(self, job_id: str, payload: dict | None, error: str | None) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            job._complete(payload, error)
        if self._current == job_id:
            self._current = None
            self._pump()

    # -- failures ------------------------------------------------------------

    def _on_proc_error(self, err) -> None:
        if err == QProcess.FailedToStart:
            self._fail_all("reduction worker failed to start")

    def _on_proc_finished(self, exit_code: int, exit_status) -> None:
        if self._buf.strip():
            self._route(self._buf)
            self._buf = ""
        self._proc = None
        cancelled = self._cancelled
        self._cancelled = None
        if cancelled is not None:
            # Deliberate cancel-kill: fail only the cancelled job, keep the queue,
            # and resume it in a fresh worker.
            self._current = None
            self._finish(cancelled, None, "cancelled")
            self._pump()
        elif self._jobs:
            # Unexpected exit: fail everything (likely a systemic backend error).
            self._fail_all(f"reduction worker exited (code {exit_code})")

    def _fail_all(self, message: str) -> None:
        self.crashed.emit(message)
        for job_id in list(self._jobs):
            self._finish(job_id, None, message)
        self._queue.clear()
        self._current = None


_WORKER: ReductionWorker | None = None


def get_worker() -> ReductionWorker:
    """The process-wide persistent reduction worker (created on first use)."""
    global _WORKER
    if _WORKER is None:
        _WORKER = ReductionWorker()
    return _WORKER
