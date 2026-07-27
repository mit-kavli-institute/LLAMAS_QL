"""Simple Cube viewer (pyqtgraph ImageView).

Shows the cube on a square, arcsec-scaled spatial field matching the LLAMAS IFU
(~34"x34"), with a wavelength slider (the ImageView timeline), an N/E compass and
a 10" scale bar.  Click a spaxel to plot its spectrum, or drag a rectangular ROI
to sum a region.  A 'Build Cube' button constructs the cube from the current RSS.
Spectra are published as :class:`Selection` objects so the shared SpectrumPlot
renders them.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core.session import Selection, SelectionKind, Session
from llamas_ql.QLook.widgets._overlays import (
    LLAMAS_FOV_ARCSEC,
    SquareContainer,
    add_compass,
    add_scale_bar,
)

# Row-major so cube[row, col] maps to (y=row, x=col), matching numpy/astropy.
pg.setConfigOption("imageAxisOrder", "row-major")


class CubeViewer(QWidget):
    build_requested = Signal(str)  # emits the RSS path to build a cube from

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._cube = None
        # Pixel->arcsec mapping applied to the image (set in _on_cube).
        self._img_pos = (0.0, 0.0)
        self._img_scale = 1.0

        self._build_btn = QPushButton("Build Cube from current RSS")
        self._build_btn.clicked.connect(self._on_build)
        self._build_btn.setEnabled(False)
        self._roi_btn = QPushButton("Region → Spectrum")
        self._roi_btn.setCheckable(True)
        self._roi_btn.toggled.connect(self._toggle_roi)
        self._roi_btn.setEnabled(False)
        self._info = QLabel("")
        self._info.setStyleSheet("color:#888;")

        controls = QHBoxLayout()
        controls.addWidget(self._build_btn)
        controls.addWidget(self._roi_btn)
        controls.addWidget(self._info, 1)

        # PlotItem-backed ImageView so the spatial field has arcsec axes.
        self._view = pg.ImageView(view=pg.PlotItem())
        self._view.ui.roiBtn.hide()       # we provide our own ROI control
        self._view.ui.menuBtn.hide()
        self._plot = self._view.getView()
        self._vb = self._plot.getViewBox()
        self._vb.setAspectLocked(True)
        self._plot.setLabel("bottom", "x (arcsec)")
        self._plot.setLabel("left", "y (arcsec)")
        add_scale_bar(self._vb, size=10.0)
        add_compass(self._vb)

        self._roi = pg.RectROI([0, 0], [5, 5], pen=pg.mkPen("#61afef", width=2))
        self._roi.sigRegionChangeFinished.connect(self._on_roi_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(SquareContainer(self._view), 1)

        self.session.rss_loaded.connect(self._on_rss)
        self.session.cube_loaded.connect(self._on_cube)
        self._view.getView().scene().sigMouseClicked.connect(self._on_click)

    # -- session reactions ---------------------------------------------------

    def _on_rss(self, rss) -> None:
        self._build_btn.setEnabled(rss is not None)

    def _on_cube(self, cube) -> None:
        self._cube = cube
        if cube is None:
            return
        # Map pixel indices to focal-plane arcsec, centered on the field.
        ny, nx = cube.shape_spatial
        w, h = cube.extent_arcsec
        ps = cube.pixel_scale
        self._img_pos = (-w / 2.0, -h / 2.0)
        self._img_scale = ps
        # ImageView timeline = wavelength axis; frames are (ny, nx).
        self._view.setImage(
            cube.cube,
            xvals=np.asarray(cube.wave_grid, dtype=float),
            autoRange=False,
            pos=[self._img_pos[0], self._img_pos[1]],
            scale=[ps, ps],
        )
        # Show the full LLAMAS field (a touch larger than the data when needed).
        half = max(w, h, LLAMAS_FOV_ARCSEC) / 2.0
        self._vb.setRange(xRange=(-half, half), yRange=(-half, half), padding=0.02)
        self._roi_btn.setEnabled(True)
        self._info.setText(
            f"{cube.channel} cube {nx}×{ny} × {cube.n_wave} "
            f"({cube.wave_label}) · {ps:g}\"/pix"
        )

    # -- build ---------------------------------------------------------------

    def _on_build(self) -> None:
        rss = self.session.current_rss
        if rss is not None:
            self.build_requested.emit(rss.path)

    def set_busy(self, busy: bool) -> None:
        self._build_btn.setEnabled(not busy and self.session.current_rss is not None)

    # -- spaxel click --------------------------------------------------------

    def _on_click(self, event) -> None:
        if self._cube is None or self._roi_btn.isChecked():
            return
        # ImageItem-local coords are pixel indices regardless of the arcsec
        # transform, so mapFromScene yields (col, row) directly.
        img_item = self._view.getImageItem()
        pos = img_item.mapFromScene(event.scenePos())
        col, row = int(pos.x()), int(pos.y())
        ny, nx = self._cube.shape_spatial
        if not (0 <= col < nx and 0 <= row < ny):
            return
        wave, values = self._cube.spectrum_at(col, row)
        if not values.size:
            return
        self.session.set_selection(
            Selection(
                kind=SelectionKind.SPAXEL, ix=col, iy=row,
                label=f"spaxel ({col},{row}) [{self._cube.channel}]",
                wave=wave, values=values,
            )
        )

    # -- ROI region ----------------------------------------------------------

    def _toggle_roi(self, on: bool) -> None:
        view = self._view.getView()
        if on:
            view.addItem(self._roi)
            self._on_roi_changed()
        else:
            view.removeItem(self._roi)

    def _on_roi_changed(self, *_args) -> None:
        if self._cube is None or not self._roi_btn.isChecked():
            return
        ny, nx = self._cube.shape_spatial
        # ROI lives in arcsec (view) coords; convert its corners to pixel indices.
        x0, y0 = self._roi.pos()
        w, h = self._roi.size()
        px, py = self._img_pos
        ps = self._img_scale or 1.0
        c0, c1 = sorted((int((x0 - px) / ps), int((x0 + w - px) / ps)))
        r0, r1 = sorted((int((y0 - py) / ps), int((y0 + h - py) / ps)))
        c0, c1 = max(0, c0), min(nx, c1)
        r0, r1 = max(0, r0), min(ny, r1)
        if c1 <= c0 or r1 <= r0:
            return
        mask = np.zeros((ny, nx), bool)
        mask[r0:r1, c0:c1] = True
        wave, values = self._cube.region_spectrum(mask)
        if not values.size:
            return
        self.session.set_selection(
            Selection(
                kind=SelectionKind.REGION, region_mask=mask,
                label=f"region [{c0}:{c1},{r0}:{r1}] ({self._cube.channel})",
                wave=wave, values=values,
            )
        )
