"""Shared geometry for the hexagonal IFU white-light view.

The LLAMAS IFU is a real pointy-top hexagonal lenslet array, so the white-light
display draws one hexagon per fibre rather than an interpolated raster.  This
module holds the pure geometry both display paths (the fast pre-RSS white-light
and the interactive post-RSS fibre map) share:

  * :func:`hexagon_symbol`  — the pointy-top unit hexagon used as the scatter
    marker.
  * :data:`HEX_SIZE_FACTOR` — marker size (in arcsec) per arcsec of spaxel pitch
    so neighbouring hexagons tile edge-to-edge without gaps or overlap.
  * :func:`to_display` / :func:`to_lut` — the fibre-map-units ⇄ on-screen-arcsec
    transform and its exact inverse.  This is where the 90° rotation that puts
    North up / East left (matching the compass overlay) lives, so the render and
    the click-picking use one consistent mapping.

Keeping the geometry here (Qt only for the symbol path) lets the display code
stay thin and lets the transform be unit-tested directly.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QPainterPath

#: Pointy-top hexagons at spaxel pitch ``p`` tile when their circumradius is
#: ``p / sqrt(3)`` (flat-to-flat width == p).  pyqtgraph's ScatterPlotItem sizes
#: a symbol by its full extent (the unit symbol spans radius 0.5, i.e. diameter
#: 1), so the marker size in arcsec is ``2 * p / sqrt(3) = p * HEX_SIZE_FACTOR``.
HEX_SIZE_FACTOR = 2.0 / math.sqrt(3.0)


def hexagon_symbol() -> QPainterPath:
    """A unit pointy-top hexagon centred at the origin (pyqtgraph scatter symbol)."""
    path = QPainterPath()
    for i in range(6):
        ang = math.pi / 2 + i * math.pi / 3  # pointy-top
        x, y = 0.5 * math.cos(ang), 0.5 * math.sin(ang)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def hex_scatter_size(pitch_arcsec: float) -> float:
    """Scatter marker size (arcsec) that makes pointy-top hexagons tile at ``pitch``."""
    return float(pitch_arcsec) * HEX_SIZE_FACTOR


def to_display(gx, gy, center, pitch):
    """Fibre-map units → on-screen arcsec, North-up / East-left.

    Applies a 90° CCW rotation ``(x, y) -> (-y, x)`` about the array centroid,
    then scales by the spaxel ``pitch``.  The rotation is what aligns the LUT
    (whose own annotations put N at +x, E at +y) with the display's compass
    (N up = +y, E left = -x); its determinant is +1, so it is a pure rotation
    with no mirror flip.

    Args:
        gx, gy: fibre positions in fibre-map (LUT) units (scalars or arrays).
        center: ``(cx, cy)`` centroid in the same LUT units.
        pitch: arcsec per LUT unit.

    Returns:
        ``(dx, dy)`` display coordinates in arcsec (same shape as the inputs).
    """
    cx, cy = center
    gx = np.asarray(gx, dtype=float)
    gy = np.asarray(gy, dtype=float)
    dx = (cy - gy) * pitch          # -(y - cy) * pitch
    dy = (gx - cx) * pitch          #  (x - cx) * pitch
    return dx, dy


def to_lut(dx, dy, center, pitch):
    """Inverse of :func:`to_display`: on-screen arcsec → fibre-map units.

    Args:
        dx, dy: display coordinates in arcsec.
        center: ``(cx, cy)`` centroid in LUT units.
        pitch: arcsec per LUT unit.

    Returns:
        ``(gx, gy)`` in fibre-map (LUT) units.
    """
    cx, cy = center
    gx = cx + dy / pitch
    gy = cy - dx / pitch
    return gx, gy


def build_spots(gx, gy, brushes, rows, center, pitch):
    """Build pyqtgraph ScatterPlotItem spot dicts for a set of fibres.

    Positions are rotated + centred + pitch-scaled via :func:`to_display`; each
    spot carries the RSS row index in ``data`` so a click resolves to a fibre.

    Args:
        gx, gy: fibre positions in LUT units (1-D arrays, already filtered to the
            fibres being drawn).
        brushes: per-fibre QBrush list (len == len(gx)).
        rows: per-fibre integer id stashed in the spot ``data`` (len == len(gx)).
        center: ``(cx, cy)`` centroid in LUT units.
        pitch: arcsec per LUT unit.

    Returns:
        list[dict]: spot dicts for ``ScatterPlotItem.setData``.
    """
    dx, dy = to_display(gx, gy, center, pitch)
    return [
        {"pos": (float(dx[i]), float(dy[i])), "data": int(rows[i]), "brush": brushes[i]}
        for i in range(len(rows))
    ]
