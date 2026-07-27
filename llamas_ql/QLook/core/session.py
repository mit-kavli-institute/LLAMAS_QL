"""Session: the single source of truth for Quick Look GUI state.

Widgets and plugins never talk to each other directly; they read and mutate the
:class:`Session` and react to its signals.  This keeps the white-light view, the
cube viewer, the spectrum plot and any plugins in sync with no direct coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from PySide6.QtCore import QObject, Signal

from llamas_ql.QLook.core.paths import NightPaths


class SelectionKind(str, Enum):
    FIBER = "fiber"
    SPAXEL = "spaxel"
    REGION = "region"


@dataclass
class Selection:
    """The currently selected spatial element to plot a spectrum for."""

    kind: SelectionKind
    #: For FIBER: the RSS row index.  For SPAXEL: ignored.
    fiber_index: int | None = None
    #: For SPAXEL/REGION: cube spatial coordinates / mask.
    ix: int | None = None
    iy: int | None = None
    region_mask: Any = None
    label: str = ""
    #: For SPAXEL/REGION, a precomputed (wave, values) spectrum from the cube.
    #: FIBER selections leave these None and are read live from the RSS.
    wave: Any = None
    values: Any = None


@dataclass
class PinnedSpectrum:
    """A spectrum the user pinned onto the plot for comparison."""

    label: str
    wave: Any  # 1-D ndarray
    flux: Any  # 1-D ndarray
    color: str | None = None


class Session(QObject):
    """Holds all live GUI state and broadcasts changes via Qt signals."""

    night_dir_changed = Signal(str)
    calib_dir_changed = Signal(str)
    calib_ready_changed = Signal(bool)
    rss_loaded = Signal(object)        # RSSData | None
    cube_loaded = Signal(object)       # CubeData | None
    whitelight_loaded = Signal(object)  # white-light FITS path (str) | None
    selection_changed = Signal(object)  # Selection | None
    pinned_changed = Signal(list)       # list[PinnedSpectrum]

    def __init__(self, paths: NightPaths | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.paths = paths or NightPaths()
        self._calib_ready = False
        self.current_rss: Any = None    # RSSData
        self.current_cube: Any = None   # CubeData
        self.current_whitelight: Any = None  # white-light FITS path
        self.selection: Selection | None = None
        self.pinned_spectra: list[PinnedSpectrum] = field(default_factory=list)  # type: ignore
        self.pinned_spectra = []

    # -- directories ---------------------------------------------------------

    def set_data_dir(self, path: str) -> None:
        self.paths.data_dir = path
        self.night_dir_changed.emit(path)

    def set_calib_dir(self, path: str) -> None:
        self.paths.calib_dir = path
        self.calib_dir_changed.emit(path)

    # -- calibration readiness ----------------------------------------------

    @property
    def calib_ready(self) -> bool:
        return self._calib_ready

    def set_calib_ready(self, ready: bool) -> None:
        if ready != self._calib_ready:
            self._calib_ready = ready
            self.calib_ready_changed.emit(ready)

    # -- data products -------------------------------------------------------

    def set_rss(self, rss: Any) -> None:
        self.current_rss = rss
        self.rss_loaded.emit(rss)

    def set_cube(self, cube: Any) -> None:
        self.current_cube = cube
        self.cube_loaded.emit(cube)

    def set_whitelight(self, path: Any) -> None:
        self.current_whitelight = path
        self.whitelight_loaded.emit(path)

    # -- selection / pins ----------------------------------------------------

    def set_selection(self, selection: Selection | None) -> None:
        self.selection = selection
        self.selection_changed.emit(selection)

    def pin_spectrum(self, spec: PinnedSpectrum) -> None:
        self.pinned_spectra.append(spec)
        self.pinned_changed.emit(self.pinned_spectra)

    def clear_pins(self) -> None:
        self.pinned_spectra = []
        self.pinned_changed.emit(self.pinned_spectra)
