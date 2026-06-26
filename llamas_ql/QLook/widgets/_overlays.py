"""Shared spatial-display overlays for the cube and white-light views.

Both views show the LLAMAS IFU field in focal-plane arcseconds; this module adds
the common furniture so they read like the mockup's square IFU panel:

  * :func:`add_scale_bar` — a labelled physical scale bar (e.g. ``10"``).
  * :func:`add_compass`   — an N (up) / E (left) sky-orientation indicator.

``LLAMAS_FOV_ARCSEC`` re-exports the single FOV definition from the cube
constructor so the views don't redefine the field of view.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtWidgets import QWidget

from llamas_ql.Cube.simple_cube_constructor import LLAMAS_FOV

# Single source of truth for the field of view (~35", "just over 34"x34").
LLAMAS_FOV_ARCSEC = float(LLAMAS_FOV)


class SquareContainer(QWidget):
    """Centers a single child widget as the largest square that fits.

    Used to give the spatial views a square display area (matching the mockup's
    IFU panel) regardless of the surrounding dock/tab shape.
    """

    def __init__(self, inner: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._inner = inner
        inner.setParent(self)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        self._inner.setGeometry(x, y, side, side)


def add_scale_bar(view_box, size: float = 10.0, suffix: str = '"'):
    """Anchor a :class:`pyqtgraph.ScaleBar` (default 10") to ``view_box``.

    Returns the ScaleBar so the caller can keep a reference (Qt parents it to the
    ViewBox, which keeps it pinned bottom-right regardless of pan/zoom).
    """
    bar = pg.ScaleBar(size=size, suffix=suffix, brush=pg.mkBrush("#ffffff"),
                      pen=pg.mkPen("#ffffff"))
    bar.setParentItem(view_box)
    bar.anchor((1, 1), (1, 1), offset=(-20, -20))
    return bar


def add_compass(view_box, half_extent: float = LLAMAS_FOV_ARCSEC / 2.0):
    """Draw an N/E compass near the lower-left of the field, in arcsec coords.

    N points up (+y) and E points left (-x), matching the standard on-sky
    orientation.  Items are added in data coordinates near the FOV corner; with
    the views defaulting to the FOV range this sits in the lower-left as in the
    mockup.  Returns the created items so callers can hold references.
    """
    he = float(half_extent)
    ox, oy = -0.82 * he, -0.82 * he   # corner origin of the compass
    arm = 0.22 * he                   # arm length
    pen = pg.mkPen("#ffffff", width=2)

    items = []
    # N arm (up) and E arm (left), drawn as short line segments.
    north = pg.PlotCurveItem([ox, ox], [oy, oy + arm], pen=pen)
    east = pg.PlotCurveItem([ox, ox - arm], [oy, oy], pen=pen)
    view_box.addItem(north)
    view_box.addItem(east)
    items += [north, east]

    n_label = pg.TextItem("N", color="#ffffff", anchor=(0.5, 1.0))
    n_label.setPos(ox, oy + arm)
    e_label = pg.TextItem("E", color="#ffffff", anchor=(1.0, 0.5))
    e_label.setPos(ox - arm, oy)
    view_box.addItem(n_label)
    view_box.addItem(e_label)
    items += [n_label, e_label]

    return items
