"""IPC protocol shared between the GUI and the reduction subprocess.

The GUI launches ``python -m llamas_ql.QLook.workers.runner`` and feeds it a
:class:`JobSpec` (JSON on stdin).  The subprocess streams newline-delimited JSON
(NDJSON) events back on stdout.  This module is deliberately **Qt-free** so the
subprocess can import it without pulling in PySide6.

Event lines look like::

    {"type": "stage", "stage": "extract", "status": "start"}
    {"type": "log", "level": "INFO", "name": "llamas_ql.Extract", "msg": "..."}
    {"type": "progress", "pct": 42, "stage": "extract"}
    {"type": "partial", "payload": {"kind": "whitelight", "path": "..."}}
    {"type": "result", "payload": {"rss": "/path/to/RSS.fits"}}
    {"type": "error", "msg": "traceback ..."}

A ``partial`` event is a *non-terminal* intermediate artefact: it lets one job
publish products as they become ready (e.g. a white-light image before the RSS)
so the GUI can display them progressively. The job stays open; exactly one
terminal ``result`` (or ``error``) closes it.

Any stdout line that is *not* valid event JSON is treated by the GUI as a raw
log line (the backend emits bare ``print()`` and noisy Ray/pypeit output).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# --- protocol constants -----------------------------------------------------

#: The ordered pipeline stages a reduction job moves through.  The GUI maps each
#: stage onto a fixed percentage so coarse progress is always reliable even when
#: the backend offers no fine-grained callback.
REDUCE_STAGES: tuple[str, ...] = (
    "validate",
    "flatfield",
    "extract",
    "wavelength",
    "rss",
    "cube",
    "whitelight",
)

#: Stages for the afternoon calibration job.
CALIB_STAGES: tuple[str, ...] = ("validate", "bias", "traces", "flatfield")

#: Stages for the quick binary white-light job (no extraction).
WHITELIGHT_STAGES: tuple[str, ...] = ("validate", "bias", "whitelight")

#: Stages for the extraction-only (boxcar RSS) job.
EXTRACT_STAGES: tuple[str, ...] = ("validate", "extract", "wavelength", "rss")

STATUS_START = "start"
STATUS_DONE = "done"
STATUS_SKIP = "skip"
STATUS_FAIL = "fail"


# --- job specification ------------------------------------------------------


@dataclass
class JobSpec:
    """A unit of background work handed to the runner subprocess.

    Attributes
    ----------
    kind:
        ``"reduce"`` (science quick reduction → RSS), ``"cube"`` (build a cube
        from an RSS), ``"whitelight"`` (binary white-light, no extraction),
        ``"extract_only"`` (boxcar RSS, no interpolation), ``"calib"`` (afternoon
        calibration processing), ``"bias"`` (build nightly bias masters),
        ``"plugin"``, or ``"dummy"`` (Phase-0 plumbing self-test).
    inputs:
        Input file paths relevant to the job (e.g. the science frame, or the
        per-channel flats for a calibration run).
    output_dir:
        Directory the runner writes products into.
    options:
        Free-form job options forwarded to the backend calls (e.g.
        ``remove_cosmic_rays``, ``grid_method``, calibration role assignments).
    plugin_id:
        Set when ``kind == "plugin"`` to identify the target plugin.
    """

    kind: str
    inputs: list[str] = field(default_factory=list)
    output_dir: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    plugin_id: str | None = None
    #: Correlates events to this job when a persistent worker serves many jobs.
    job_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "JobSpec":
        data = json.loads(text)
        return cls(
            kind=data["kind"],
            inputs=list(data.get("inputs", [])),
            output_dir=data.get("output_dir"),
            options=dict(data.get("options", {})),
            plugin_id=data.get("plugin_id"),
            job_id=data.get("job_id"),
        )


# --- event helpers (used by the runner to emit NDJSON) ----------------------


def _emit(stream, obj: dict[str, Any], job_id: str | None = None) -> None:
    """Write one NDJSON event line and flush so the GUI sees it promptly."""
    if job_id is not None:
        obj["job_id"] = job_id
    stream.write(json.dumps(obj) + "\n")
    stream.flush()


def emit_stage(stream, stage: str, status: str, detail: str | None = None,
               job_id: str | None = None) -> None:
    obj: dict[str, Any] = {"type": "stage", "stage": stage, "status": status}
    if detail:
        obj["detail"] = detail
    _emit(stream, obj, job_id)


def emit_progress(stream, pct: int, stage: str | None = None,
                  job_id: str | None = None) -> None:
    obj: dict[str, Any] = {"type": "progress", "pct": int(pct)}
    if stage:
        obj["stage"] = stage
    _emit(stream, obj, job_id)


def emit_log(stream, level: str, msg: str, name: str = "",
             job_id: str | None = None) -> None:
    _emit(stream, {"type": "log", "level": level, "name": name, "msg": msg}, job_id)


def emit_partial(stream, payload: dict[str, Any], job_id: str | None = None) -> None:
    """Emit a non-terminal intermediate artefact; the job stays open."""
    _emit(stream, {"type": "partial", "payload": payload}, job_id)


def emit_result(stream, payload: dict[str, Any], job_id: str | None = None) -> None:
    _emit(stream, {"type": "result", "payload": payload}, job_id)


def emit_error(stream, msg: str, job_id: str | None = None) -> None:
    _emit(stream, {"type": "error", "msg": msg}, job_id)


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse one stdout line into an event dict, or ``None`` if it is not one.

    A line is only treated as an event when it is a JSON object carrying a
    string ``"type"`` field; everything else (bare prints, Ray/pypeit chatter)
    returns ``None`` so the caller can show it as a raw log line.
    """
    line = line.strip()
    if not line or line[0] != "{":
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("type"), str):
        return obj
    return None
