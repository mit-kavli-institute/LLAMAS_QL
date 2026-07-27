"""CubeData: a facade over a Simple Cube FITS written by SimpleCubeConstructor.

The cube FITS has a PRIMARY ``FLUX`` cube ``(n_wave, ny, nx)``, optional ``VAR``
and ``WEIGHT`` extensions, and a ``WAVELENGTH`` binary table.  This model exposes
wavelength slices and spaxel/region spectra for the cube viewer.

Qt-free (numpy/astropy only).
"""

from __future__ import annotations

import os

import numpy as np
from astropy.io import fits


# Output spatial pixel size used by SimpleCubeConstructor when the cube header
# carries no WCS scale (matches its --pixel-size default).
_DEFAULT_PIXEL_SCALE = 0.3  # arcsec / pixel


class CubeData:
    def __init__(self, path: str):
        self.path = path
        self.cube = None       # (n_wave, ny, nx)
        self.var = None
        self.wave_grid = None  # (n_wave,)
        self.channel = "UNKNOWN"
        self.has_wavelength = False
        self.pixel_scale = _DEFAULT_PIXEL_SCALE  # arcsec / spatial pixel
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Cube file not found: {self.path}")
        with fits.open(self.path, memmap=False) as hdul:
            self.cube = np.asarray(hdul[0].data, dtype=float)
            hdr = hdul[0].header
            self.channel = str(hdr.get("CHANNEL", "UNKNOWN"))
            self.pixel_scale = self._read_pixel_scale(hdr)
            names = {hdu.name.upper() for hdu in hdul}
            if "VAR" in names:
                self.var = np.asarray(hdul["VAR"].data, dtype=float)
            if "WAVELENGTH" in names:
                self.wave_grid = np.asarray(hdul["WAVELENGTH"].data["wavelength"], dtype=float)
        if self.wave_grid is None or len(self.wave_grid) != self.n_wave:
            self.wave_grid = np.arange(self.n_wave, dtype=float)
            self.has_wavelength = False
        else:
            finite = self.wave_grid[np.isfinite(self.wave_grid)]
            self.has_wavelength = finite.size > 0 and float(np.nanmax(finite)) > 100.0

    @staticmethod
    def _read_pixel_scale(hdr) -> float:
        """Arcsec/pixel from the spatial WCS scale, defaulting to 0.3"/pix.

        Reads ``CDELT1``/``CD1_1`` (degrees) when present and converts to arcsec;
        falls back to the constructor's default pixel size otherwise.
        """
        for key in ("CDELT1", "CD1_1"):
            if key in hdr:
                try:
                    val = abs(float(hdr[key]))
                except (TypeError, ValueError):
                    continue
                if val <= 0:
                    continue
                # Degrees/pix -> arcsec/pix; pass through values already in arcsec.
                return val * 3600.0 if val < 0.01 else val
        return _DEFAULT_PIXEL_SCALE

    # -- shape ---------------------------------------------------------------

    @property
    def n_wave(self) -> int:
        return 0 if self.cube is None else int(self.cube.shape[0])

    @property
    def shape_spatial(self) -> tuple[int, int]:
        if self.cube is None:
            return (0, 0)
        return int(self.cube.shape[1]), int(self.cube.shape[2])  # (ny, nx)

    @property
    def extent_arcsec(self) -> tuple[float, float]:
        """Spatial extent ``(width, height)`` in arcsec (nx, ny × pixel_scale)."""
        ny, nx = self.shape_spatial
        return nx * self.pixel_scale, ny * self.pixel_scale

    @property
    def wave_label(self) -> str:
        return "Wavelength (Å)" if self.has_wavelength else "Slice index"

    # -- extraction ----------------------------------------------------------

    def slice(self, wave_index: int) -> np.ndarray:
        """The (ny, nx) spatial image at a wavelength index."""
        wave_index = int(np.clip(wave_index, 0, max(0, self.n_wave - 1)))
        return self.cube[wave_index]

    def white_light(self) -> np.ndarray:
        """Wavelength-collapsed (nansum) image for the default display."""
        return np.nansum(self.cube, axis=0)

    def spectrum_at(self, ix: int, iy: int):
        """Return ``(wave, values)`` for spaxel ``(ix, iy)`` (x=col, y=row)."""
        ny, nx = self.shape_spatial
        if not (0 <= ix < nx and 0 <= iy < ny):
            return np.array([]), np.array([])
        return self.wave_grid, np.asarray(self.cube[:, iy, ix], dtype=float)

    def region_spectrum(self, mask: np.ndarray):
        """Sum spectra over a boolean ``(ny, nx)`` spatial mask."""
        if mask is None or not mask.any():
            return np.array([]), np.array([])
        summed = np.nansum(self.cube[:, mask], axis=1)
        return self.wave_grid, np.asarray(summed, dtype=float)
