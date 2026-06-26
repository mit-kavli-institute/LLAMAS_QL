"""FiberGeometry: spatial layout of RSS fibers for the white-light view.

Vectorizes :func:`llamas_ql.Image.WhiteLightModule.FiberMap_LUT` across an RSS
FIBERMAP so each fiber row gets an ``(x, y)`` focal-plane position in arcsec, and
provides nearest-fiber picking from a click and a per-fiber white-light flux for
colour-coding the scatter.

Qt-free (numpy only) so it can be unit-tested without a display.
"""

from __future__ import annotations

import numpy as np

from llamas_ql.Image.WhiteLightModule import FiberMap_LUT


class FiberGeometry:
    """Maps RSS fiber rows to focal-plane positions and supports click picking."""

    def __init__(self, rss):
        self._rss = rss
        self.x = np.array([])           # arcsec, per fiber row (NaN for unmapped)
        self.y = np.array([])
        self.valid = np.array([], bool)  # fibers with a real position
        self._build()

    def _build(self) -> None:
        fibermap = getattr(self._rss, "fibermap", None)
        if fibermap is None:
            return
        n = len(fibermap)
        xs = np.full(n, np.nan)
        ys = np.full(n, np.nan)
        bench = fibermap["BENCHSIDE"]
        fid = fibermap["FIBER_ID"]
        for i in range(n):
            bs = str(bench[i]).strip()
            try:
                x, y = FiberMap_LUT(bs, int(fid[i]))
            except Exception:
                continue
            # FiberMap_LUT returns (-1, -1) for unknown/dead fibers.
            if x == -1 and y == -1:
                continue
            xs[i] = x
            ys[i] = y
        self.x = xs
        self.y = ys
        self.valid = np.isfinite(xs) & np.isfinite(ys)

    def whitelight_flux(self, wave_min=None, wave_max=None) -> np.ndarray:
        """Per-fiber summed FLUX within an optional wavelength window."""
        flux = getattr(self._rss, "flux", None)
        if flux is None:
            return np.zeros_like(self.x)
        wave = getattr(self._rss, "wave", None)
        if wave is not None and wave.shape == flux.shape and (
            wave_min is not None or wave_max is not None
        ):
            mask = np.ones(flux.shape, bool)
            if wave_min is not None:
                mask &= wave >= wave_min
            if wave_max is not None:
                mask &= wave <= wave_max
            return np.nansum(np.where(mask, flux, np.nan), axis=1)
        return np.nansum(flux, axis=1)

    def nearest_fiber(self, x: float, y: float) -> int | None:
        """Return the RSS row index of the valid fiber closest to ``(x, y)``."""
        if not self.valid.any():
            return None
        d2 = (self.x - x) ** 2 + (self.y - y) ** 2
        d2[~self.valid] = np.inf
        idx = int(np.argmin(d2))
        return idx if np.isfinite(d2[idx]) else None
