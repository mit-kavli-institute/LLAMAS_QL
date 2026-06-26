"""RSSData: a lazy facade over an RSS FITS file produced by ``generate_rss``.

One RSS file holds one spectrograph channel's row-stacked spectra.  This model
exposes the per-fiber arrays the views need (FLUX/COUNTS/SKY/WAVE/MASK/FWHM) and
the FIBERMAP table, with a small helper to pull a single fiber's 1-D spectrum.

Qt-free on purpose: it is plain numpy/astropy so the same model can be used from
a worker or a test without importing PySide6.
"""

from __future__ import annotations

import os

import numpy as np
from astropy.io import fits


class RSSData:
    """In-memory view of one channel's RSS FITS file.

    Arrays are shaped ``(n_fiber, n_wave)``.  Missing optional extensions (e.g.
    SKY when sky subtraction was off) are returned as ``None``.
    """

    #: Data extensions we surface, mapped to attribute names.
    _EXTS = {
        "FLUX": "flux",
        "COUNTS": "counts",
        "SKY": "sky",
        "WAVE": "wave",
        "MASK": "mask",
        "FWHM": "fwhm",
    }

    def __init__(self, path: str):
        self.path = path
        self.flux = None
        self.counts = None
        self.sky = None
        self.wave = None
        self.mask = None
        self.fwhm = None
        self.fibermap = None
        self.channel = "UNKNOWN"
        self.primary_header = None
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"RSS file not found: {self.path}")
        with fits.open(self.path, memmap=False) as hdul:
            self.primary_header = hdul[0].header.copy()
            self.channel = str(self.primary_header.get("CHANNEL", "UNKNOWN"))
            names = {hdu.name.upper() for hdu in hdul}
            for ext, attr in self._EXTS.items():
                if ext in names:
                    setattr(self, attr, np.asarray(hdul[ext].data))
            if "FIBERMAP" in names:
                self.fibermap = hdul["FIBERMAP"].data.copy()

    # -- convenience ---------------------------------------------------------

    @property
    def n_fiber(self) -> int:
        return 0 if self.flux is None else int(self.flux.shape[0])

    @property
    def n_wave(self) -> int:
        return 0 if self.flux is None else int(self.flux.shape[1])

    @property
    def has_wavelength(self) -> bool:
        """True when the WAVE axis looks like real wavelengths, not pixel index."""
        if self.wave is None:
            return False
        finite = self.wave[np.isfinite(self.wave)]
        return finite.size > 0 and float(np.nanmax(finite)) > 100.0

    def spectrum(self, fiber_index: int, kind: str = "flux"):
        """Return ``(wave, values)`` 1-D arrays for one fiber.

        ``kind`` is one of ``flux``/``counts``/``sky``.  Falls back to FLUX when
        the requested array is absent.  The wavelength axis is the per-fiber WAVE
        row if present, else a pixel index.
        """
        arr = getattr(self, kind, None)
        if arr is None:
            arr = self.flux
        if arr is None or not (0 <= fiber_index < arr.shape[0]):
            return np.array([]), np.array([])
        values = np.asarray(arr[fiber_index, :], dtype=float)
        if self.wave is not None and self.wave.shape == arr.shape:
            wave = np.asarray(self.wave[fiber_index, :], dtype=float)
        else:
            wave = np.arange(values.size, dtype=float)
        return wave, values

    @property
    def wave_label(self) -> str:
        return "Wavelength (Å)" if self.has_wavelength else "Pixel"
