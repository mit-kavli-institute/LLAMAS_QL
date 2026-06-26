"""White-light / fibre-map view (pyqtgraph).

Renders the RSS fibres as a scatter in focal-plane (arcsec) coordinates, colour-
coded by their summed white-light flux.  Clicking near a fibre selects it, which
drives the spectrum plot via the shared :class:`Session`.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

from llamas_ql.QLook.core.session import Selection, SelectionKind, Session
from llamas_ql.QLook.models.fiber_geometry import FiberGeometry
from llamas_ql.QLook.widgets._overlays import (
    LLAMAS_FOV_ARCSEC,
    SquareContainer,
    add_compass,
    add_scale_bar,
)


class WhiteLightWidget(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._geom: FiberGeometry | None = None

        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1e1e1e")
        self._plot.setAspectLocked(True)
        self._plot.setLabel("bottom", "x (arcsec)")
        self._plot.setLabel("left", "y (arcsec)")
        # Default to the full LLAMAS field; add the shared compass + scale bar.
        half = LLAMAS_FOV_ARCSEC / 2.0
        self._plot.setRange(xRange=(-half, half), yRange=(-half, half), padding=0.02)
        vb = self._plot.getPlotItem().getViewBox()
        add_scale_bar(vb, size=10.0)
        add_compass(vb)

        self._scatter = pg.ScatterPlotItem(size=9, pen=None)
        self._scatter.sigClicked.connect(self._on_point_clicked)
        self._plot.addItem(self._scatter)

        # Marker showing the currently selected fibre.
        self._marker = pg.ScatterPlotItem(
            size=16, pen=pg.mkPen("#ffffff", width=2), brush=None, symbol="o"
        )
        self._plot.addItem(self._marker)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(SquareContainer(self._plot), 1)

        self.session.rss_loaded.connect(self._on_rss)
        self.session.selection_changed.connect(self._on_selection)
        # Also accept clicks on empty canvas -> nearest fibre.
        self._plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

    # -- session reactions ---------------------------------------------------

    def _on_rss(self, rss) -> None:
        self._marker.clear()
        if rss is None or rss.fibermap is None:
            self._scatter.clear()
            self._geom = None
            return
        self._geom = FiberGeometry(rss)
        flux = self._geom.whitelight_flux()
        valid = self._geom.valid
        x = self._geom.x[valid]
        y = self._geom.y[valid]
        f = flux[valid]
        rows = np.nonzero(valid)[0]
        brushes = self._colormap(f)
        spots = [
            {"pos": (x[i], y[i]), "data": int(rows[i]), "brush": brushes[i]}
            for i in range(len(rows))
        ]
        self._scatter.setData(spots)
        self._plot.autoRange()

    def _on_selection(self, selection) -> None:
        if (
            selection is None
            or selection.kind != SelectionKind.FIBER
            or self._geom is None
            or selection.fiber_index is None
        ):
            self._marker.clear()
            return
        idx = selection.fiber_index
        if 0 <= idx < len(self._geom.x) and self._geom.valid[idx]:
            self._marker.setData([{"pos": (self._geom.x[idx], self._geom.y[idx])}])

    # -- interaction ---------------------------------------------------------

    def _on_point_clicked(self, _scatter, points) -> None:
        if not len(points):
            return
        row = int(points[0].data())
        self._select_fiber(row)

    def _on_scene_clicked(self, event) -> None:
        if self._geom is None:
            return
        vb = self._plot.getPlotItem().vb
        pt = vb.mapSceneToView(event.scenePos())
        row = self._geom.nearest_fiber(pt.x(), pt.y())
        if row is not None:
            self._select_fiber(row)

    def _select_fiber(self, row: int) -> None:
        rss = self.session.current_rss
        label = f"fiber {row}"
        if rss is not None and rss.fibermap is not None and 0 <= row < len(rss.fibermap):
            bs = str(rss.fibermap["BENCHSIDE"][row]).strip()
            fid = int(rss.fibermap["FIBER_ID"][row])
            label = f"{bs}/{fid} ({rss.channel})"
        self.session.set_selection(
            Selection(kind=SelectionKind.FIBER, fiber_index=row, label=label)
        )

    @staticmethod
    def _colormap(values: np.ndarray):
        """Map flux values to brushes via a percentile-stretched viridis LUT."""
        v = np.asarray(values, dtype=float)
        finite = v[np.isfinite(v)]
        if finite.size == 0:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = np.nanpercentile(finite, [5, 99])
            if hi <= lo:
                hi = lo + 1.0
        norm = np.clip((v - lo) / (hi - lo), 0, 1)
        norm[~np.isfinite(norm)] = 0.0
        cmap = pg.colormap.get("viridis")
        lut = cmap.getLookupTable(0.0, 1.0, 256)
        idx = (norm * 255).astype(int)
        return [pg.mkBrush(*lut[i]) for i in idx]
