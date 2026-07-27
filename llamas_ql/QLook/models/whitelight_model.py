"""WhiteLightData: a facade over the fast quick-look white-light FITS.

``QuickWhiteLightCube`` writes a white-light FITS with one 2-D image HDU per
channel (``BLUE``/``GREEN``/``RED``) plus a matching per-fibre binary table
(``BLUE_TAB``/``GREEN_TAB``/``RED_TAB`` with ``XDATA``/``YDATA``/``FLUX``).

This model surfaces both so the display can paint the fast white-light before the
RSS (and therefore the interactive hex fibre map) is ready:

  * ``x``/``y``/``flux`` — the exact per-fibre positions (fibre-map units) and
    summed fluxes from the ``<CH>_TAB`` table, for a discrete hexagon render.
  * ``image``/``shape`` — the interpolated raster HDU, kept as a fallback.

Pass ``channel`` to select the right HDU; without it the first 2-D image HDU is
used (legacy behaviour), which for these products is always ``BLUE`` — so callers
that care about the channel should always pass it.

Qt-free (numpy/astropy only).
"""

from __future__ import annotations

import os

import numpy as np
from astropy.io import fits

_KNOWN_CHANNELS = ("blue", "green", "red")


class WhiteLightData:
    def __init__(self, path: str, channel: str | None = None):
        self.path = path
        self.channel = "UNKNOWN"
        self.image: np.ndarray | None = None   # (ny, nx) interpolated raster
        self.x = np.array([], dtype=float)      # per-fibre position, LUT units
        self.y = np.array([], dtype=float)
        self.flux = np.array([], dtype=float)   # per-fibre summed white-light flux
        self._load(channel.lower() if channel else None)

    def _load(self, channel: str | None) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"White-light file not found: {self.path}")
        with fits.open(self.path, memmap=False) as hdul:
            image_hdu = self._pick_image_hdu(hdul, channel)
            if image_hdu is None:
                raise ValueError(
                    f"No 2-D image HDU found in white-light file: {self.path}")
            self.image = np.asarray(image_hdu.data, dtype=float)
            self.channel = self._resolve_channel(image_hdu)
            self._read_table(hdul, image_hdu)

    def _pick_image_hdu(self, hdul, channel):
        """Choose the 2-D image HDU for ``channel`` (EXTNAME/COLOR/CHANNEL), else first."""
        images = [h for h in hdul
                  if getattr(h, "data", None) is not None and np.ndim(h.data) == 2]
        if not images:
            return None
        if channel:
            for h in images:
                name = str(h.name).strip().lower()
                color = str(h.header.get("COLOR", h.header.get("CHANNEL", ""))).strip().lower()
                if channel in (name, color):
                    return h
        return images[0]

    def _resolve_channel(self, hdu) -> str:
        """Channel label from COLOR/CHANNEL header, else a colour-valued EXTNAME."""
        color = str(hdu.header.get("COLOR", hdu.header.get("CHANNEL", ""))).strip().lower()
        if color in _KNOWN_CHANNELS:
            return color
        name = str(hdu.name).strip().lower()
        if name in _KNOWN_CHANNELS:
            return name
        return "UNKNOWN"

    def _read_table(self, hdul, image_hdu) -> None:
        """Read the ``<CH>_TAB`` per-fibre positions/flux matching the image HDU."""
        tab_name = f"{str(image_hdu.name).strip()}_TAB"
        try:
            tab = hdul[tab_name]
        except (KeyError, IndexError):
            # No matching table (e.g. a legacy or hand-built file); leave arrays empty.
            return
        cols = getattr(tab, "columns", None)
        names = set(cols.names) if cols is not None else set()
        if {"XDATA", "YDATA", "FLUX"} <= names:
            self.x = np.asarray(tab.data["XDATA"], dtype=float)
            self.y = np.asarray(tab.data["YDATA"], dtype=float)
            self.flux = np.asarray(tab.data["FLUX"], dtype=float)

    @property
    def has_fibers(self) -> bool:
        return self.x.size > 0 and self.x.size == self.y.size == self.flux.size

    @property
    def shape(self) -> tuple[int, int]:
        return (0, 0) if self.image is None else (int(self.image.shape[0]),
                                                  int(self.image.shape[1]))
