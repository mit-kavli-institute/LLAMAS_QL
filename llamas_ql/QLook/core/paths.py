"""Directory resolution for the Quick Look GUI.

Thin wrapper over :mod:`llamas_ql.config` that also tracks the observer-chosen
calibration and science-data directories for the current night.  Defaults come
from the package config so the GUI works out of the box on the observing
machine; the observer overrides ``calib_dir`` / ``data_dir`` at runtime.
"""

from __future__ import annotations

import os

from llamas_ql import config


class NightPaths:
    """Resolved input/output directories for a Quick Look session."""

    def __init__(
        self,
        calib_dir: str | None = None,
        data_dir: str | None = None,
        output_dir: str | None = None,
    ):
        self.calib_dir = calib_dir or config.CALIB_DIR
        self.data_dir = data_dir or config.DATA_DIR
        self.output_dir = output_dir or config.OUTPUT_DIR

    # Packaged, read-only resources are always taken from the installed package.
    @property
    def lut_dir(self) -> str:
        return config.LUT_DIR

    @property
    def bias_dir(self) -> str:
        return config.BIAS_DIR

    @property
    def slow_bias_file(self) -> str:
        return config.SLOW_BIAS_FILE

    @property
    def fast_bias_file(self) -> str:
        return config.FAST_BIAS_FILE

    @property
    def trace_dir(self) -> str:
        """Where freshly generated nightly traces are written."""
        return os.path.join(self.output_dir, "traces")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.output_dir, "logs")

    def ensure_dirs(self) -> None:
        for d in (self.output_dir, self.trace_dir, self.log_dir):
            os.makedirs(d, exist_ok=True)
