"""Classify calibration FITS frames from their headers (read-only, Qt-free).

A LLAMAS exposure is a single multi-extension FITS holding all 24 cameras, so a
*dome flat* frame already covers red/green/blue — trace generation just needs one
flat MEF.  This classifier proposes a role per file plus an overall
:class:`CalibPlan`; the GUI lets the observer confirm or override every choice.

Frame-type header keywords are not guaranteed across LLAMAS data, so detection is
heuristic (keyword first, then exposure time, then filename) and each assignment
carries a confidence the UI can flag.  The classifier never decides on its own —
it proposes.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from enum import Enum

from astropy.io import fits

# Calibration roles the pipeline cares about.
ROLE_BIAS = "bias"
ROLE_FLAT = "dome_flat"
ROLE_TWILIGHT = "twilight"
ROLE_ARC = "arc"
ROLE_SCIENCE = "science"
ROLE_UNKNOWN = "unknown"

ROLES = (ROLE_BIAS, ROLE_FLAT, ROLE_TWILIGHT, ROLE_ARC, ROLE_SCIENCE, ROLE_UNKNOWN)

_TYPE_KEYS = ("EXPTYPE", "IMAGETYP", "OBSTYPE", "OBJECT")
_EXPTIME_KEYS = ("EXPTIME", "EXPOSURE")

# substring -> role, applied to header type/object text and to the filename.
_KEYWORD_ROLES = (
    ("twilight", ROLE_TWILIGHT), ("twi", ROLE_TWILIGHT), ("skyflat", ROLE_TWILIGHT),
    ("domeflat", ROLE_FLAT), ("dome", ROLE_FLAT), ("flat", ROLE_FLAT),
    ("thar", ROLE_ARC), ("arc", ROLE_ARC), ("comp", ROLE_ARC), ("wave", ROLE_ARC),
    ("bias", ROLE_BIAS), ("zero", ROLE_BIAS),
)


class Confidence(str, Enum):
    HIGH = "high"   # explicit type keyword matched
    LOW = "low"     # inferred from exptime / filename
    NONE = "none"


@dataclass
class CalibFrame:
    path: str
    filename: str
    role: str
    confidence: Confidence
    obj: str = ""
    exptime: float | None = None


@dataclass
class CalibPlan:
    """The proposed calibration set; mutated by the panel as the observer edits.

    ``chosen`` records the observer's pick of *which* file to use for each role
    when several frames share that role (e.g. three dome flats).  When a role has
    no explicit pick the first frame of that role is used as the default.
    """

    frames: list[CalibFrame] = field(default_factory=list)
    chosen: dict[str, str] = field(default_factory=dict)

    def frames_for_role(self, role: str) -> list[CalibFrame]:
        """All frames currently assigned to ``role`` (panel order preserved)."""
        return [f for f in self.frames if f.role == role]

    def set_chosen(self, role: str, path: str) -> None:
        """Record ``path`` as the file to use for ``role``."""
        self.chosen[role] = path

    def role_counts(self) -> dict[str, int]:
        """Number of frames assigned to each role (for the status summary)."""
        counts: dict[str, int] = {}
        for f in self.frames:
            counts[f.role] = counts.get(f.role, 0) + 1
        return counts

    def _first(self, role: str) -> str | None:
        """The observer-chosen file for ``role``, else the first frame of it."""
        picked = self.chosen.get(role)
        if picked is not None and any(
            f.role == role and f.path == picked for f in self.frames
        ):
            return picked
        for f in self.frames:
            if f.role == role:
                return f.path
        return None

    @property
    def dome_flat(self) -> str | None:
        return self._first(ROLE_FLAT)

    @property
    def twilight(self) -> str | None:
        return self._first(ROLE_TWILIGHT)

    @property
    def bias(self) -> str | None:
        return self._first(ROLE_BIAS)

    @property
    def arc(self) -> str | None:
        return self._first(ROLE_ARC)

    def is_runnable(self) -> bool:
        """Trace generation requires at least one dome-flat MEF."""
        return self.dome_flat is not None


def _classify_header(hdr) -> tuple[str, Confidence, str, float | None]:
    obj = ""
    type_text = ""
    for k in _TYPE_KEYS:
        if k in hdr:
            val = str(hdr[k])
            if k == "OBJECT":
                obj = val
            type_text += " " + val
    exptime = None
    for k in _EXPTIME_KEYS:
        if k in hdr:
            try:
                exptime = float(hdr[k])
            except (TypeError, ValueError):
                pass
            break

    text = type_text.lower()
    for key, role in _KEYWORD_ROLES:
        if key in text:
            return role, Confidence.HIGH, obj, exptime
    if exptime is not None and exptime == 0:
        return ROLE_BIAS, Confidence.LOW, obj, exptime
    return ROLE_UNKNOWN, Confidence.NONE, obj, exptime


def classify_file(path: str) -> CalibFrame:
    role, conf, obj, exptime = ROLE_UNKNOWN, Confidence.NONE, "", None
    try:
        hdr = fits.getheader(path, ext=0)
        role, conf, obj, exptime = _classify_header(hdr)
    except Exception:
        pass
    # Filename fallback when the header was unhelpful.
    if role == ROLE_UNKNOWN:
        name = os.path.basename(path).lower()
        for key, frole in _KEYWORD_ROLES:
            if key in name:
                role, conf = frole, Confidence.LOW
                break
    return CalibFrame(
        path=path, filename=os.path.basename(path), role=role,
        confidence=conf, obj=obj, exptime=exptime,
    )


def detect_calibrations(directory: str) -> CalibPlan:
    """Scan ``directory`` and propose a :class:`CalibPlan`."""
    paths = sorted(
        glob.glob(os.path.join(directory, "*.fits"))
        + glob.glob(os.path.join(directory, "*.fit"))
    )
    return CalibPlan(frames=[classify_file(p) for p in paths])
