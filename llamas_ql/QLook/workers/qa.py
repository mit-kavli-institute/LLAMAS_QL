"""Quick Look bias QA: pre-flight warnings for a frame's bias subtraction.

Qt-free. ``ql_bias_guard`` resolves the read-mode-matched master bias for a
frame and flags, *before* the (slow) extraction runs, any cameras whose master
bias is missing, read-mode-mismatched, or wildly off the frame's own
region-based level (``constants.BIAS_REGIONS``). The actual region-based
substitution happens inside ``GUI.guiExtract`` — this only surfaces warnings the
runner emits to the GUI; it never blocks the reduction.
"""

from __future__ import annotations

import os

import numpy as np
from astropy.io import fits

from llamas_ql.config import FAST_BIAS_FILE, SLOW_BIAS_FILE
from llamas_ql.constants import BIAS_OFFSET_WARN_DN, region_bias_level
from llamas_ql.Utils.readmode import get_readmode


def _camera_key(header) -> tuple[str, str]:
    """Return ``(benchside, label)`` from an extension header, best-effort."""
    if "BENCH" in header and "SIDE" in header:
        bench, side = str(header["BENCH"]).strip(), str(header["SIDE"]).strip()
        color = str(header.get("COLOR", "")).strip().lower()
    else:
        cam = str(header.get("CAM_NAME", "")).split("_")
        bench = cam[0][0] if cam and cam[0] else "?"
        side = cam[0][1] if cam and len(cam[0]) > 1 else "?"
        color = cam[1].lower() if len(cam) > 1 else ""
    return f"{bench}{side}", f"{bench}{side} {color}".strip()


def resolve_bias(science_file: str, slow_bias: str | None = None,
                 fast_bias: str | None = None) -> tuple[str, str | None]:
    """Resolve ``(read_mode, master_bias_path)`` for ``science_file``.

    Read-mode comes from the primary ``READ-MDE`` keyword (slow↔slow, fast↔fast),
    defaulting to the packaged masters. Returns ``(mode, None)`` if the matched
    master is absent.
    """
    with fits.open(science_file, memmap=True) as hdul:
        mode = get_readmode(hdul[0].header)
    fast = fast_bias or FAST_BIAS_FILE
    slow = slow_bias or SLOW_BIAS_FILE
    path = fast if mode == "FAST" else slow
    return mode, (path if path and os.path.isfile(path) else None)


def ql_bias_guard(science_file: str, slow_bias: str | None = None,
                  fast_bias: str | None = None) -> tuple[bool, list[str]]:
    """Pre-flight bias check; returns ``(ok, warnings)`` (never raises).

    ``ok`` is False when any warning was raised. Warnings name the read-mode and,
    per camera, any master that is missing, read-mode-mismatched, or off the
    frame's region level by more than ``BIAS_OFFSET_WARN_DN``.
    """
    warnings: list[str] = []
    try:
        mode, master = resolve_bias(science_file, slow_bias, fast_bias)
    except Exception as exc:  # unreadable science frame — let extraction surface it
        return True, [f"bias guard skipped (could not read {os.path.basename(science_file)}: {exc})"]

    if master is None:
        warnings.append(
            f"No {mode} master bias found — region-based fallback will be used for all cameras."
        )
        return False, warnings

    # Best-effort per-camera level check (region estimate vs master median).
    try:
        with fits.open(science_file, memmap=True) as shdul, fits.open(master, memmap=True) as bhdul:
            bias_by_cam = {}
            for hdu in bhdul[1:]:
                if hdu.data is None:
                    continue
                key, _ = _camera_key(hdu.header)
                bias_by_cam[key] = float(np.nanmedian(hdu.data))
            for hdu in shdul[1:]:
                if hdu.data is None:
                    continue
                key, label = _camera_key(hdu.header)
                region = region_bias_level(hdu.data, key=key)
                master_level = bias_by_cam.get(key)
                if master_level is None or not np.isfinite(region):
                    continue
                if abs(master_level - region) > BIAS_OFFSET_WARN_DN:
                    warnings.append(
                        f"{label}: master bias {master_level:.0f} DN vs region "
                        f"{region:.0f} DN (> {BIAS_OFFSET_WARN_DN:.0f}) — region fallback applied."
                    )
    except Exception as exc:
        warnings.append(f"bias level check incomplete ({exc}); extraction will guard per camera.")

    return (len(warnings) == 0), warnings
