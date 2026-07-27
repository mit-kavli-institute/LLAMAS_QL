"""GUI-side tests for the progressive quick look (headless Qt)."""
import numpy as np
import pytest
from astropy.io import fits

from llamas_ql.QLook.core.session import Session
from llamas_ql.QLook.controllers.quicklook_controller import QuickLookController
from llamas_ql.QLook.models.whitelight_model import WhiteLightData


def test_controller_publishes_each_partial(qapp, monkeypatch):
    """Each streamed partial lands on the session immediately."""
    import llamas_ql.QLook.controllers.quicklook_controller as qc
    import llamas_ql.QLook.models.cube_model as cube_mod

    monkeypatch.setattr(qc, "RSSData", lambda path: f"RSS::{path}")
    monkeypatch.setattr(cube_mod, "CubeData", lambda path: f"CUBE::{path}")

    session = Session()
    ctrl = QuickLookController(session)
    ctrl._inspect_channel = "green"

    ctrl._on_partial({"kind": "whitelight", "path": "/tmp/wl.fits"})
    assert session.current_whitelight == "/tmp/wl.fits"
    assert session.current_rss is None

    ctrl._on_partial({"kind": "rss", "rss_files": ["/tmp/a_green_RSS.fits",
                                                   "/tmp/a_red_RSS.fits"]})
    assert session.current_rss == "RSS::/tmp/a_green_RSS.fits"  # green picked

    ctrl._on_partial({"kind": "cube", "path": "/tmp/c.fits"})
    assert session.current_cube == "CUBE::/tmp/c.fits"


def test_whitelight_model_reads_2d_image(tmp_path):
    img = np.arange(20, dtype=float).reshape(4, 5)
    path = tmp_path / "wl.fits"
    hdul = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(data=img, name="GREEN"),
    ])
    hdul[1].header["COLOR"] = "green"
    hdul.writeto(path)

    wl = WhiteLightData(str(path))
    assert wl.shape == (4, 5)
    assert wl.channel == "green"
    assert np.allclose(wl.image, img)


class _StubGeom:
    """Minimal FiberGeometry stand-in in spaxel-index coordinates."""
    def __init__(self, x, y):
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.valid = np.ones(self.x.size, dtype=bool)

    def whitelight_flux(self, *a):
        return np.ones(self.x.size)

    def nearest_fiber(self, sx, sy):
        d = (self.x - sx) ** 2 + (self.y - sy) ** 2
        return int(np.argmin(d))


def test_hex_render_scaling_and_click_inversion(qapp):
    """A click at a fibre's display position resolves back to that fibre row.

    Exercises the fibre-map → display (rotation + pitch) render mapping and its
    inverse in the click handler (the round-trip must recover the original fibre),
    including the N-up/E-left 90° rotation."""
    from PySide6.QtCore import QPointF
    from llamas_ql.QLook.widgets.display_panel import DisplayPanel, SPAXEL_PITCH_ARCSEC

    session = Session()
    panel = DisplayPanel(session)
    panel._geom = _StubGeom([10, 20, 30], [10, 10, 10])
    panel._interactive = True   # picking is gated on the RSS being loaded
    panel._render()

    # Centroid is (20, 10); fibre index 2 sits at LUT (30, 10).  With the 90° CCW
    # rotation (x,y)->(-y,x): display = ((cy-gy)*pitch, (gx-cx)*pitch) = (0, 10*pitch).
    assert panel._geom_center == (20.0, 10.0)
    target = QPointF(0.0, 10 * SPAXEL_PITCH_ARCSEC)
    panel._vb.mapSceneToView = lambda scene_pos: target  # bypass the scene

    captured = {}
    session.set_selection = lambda sel: captured.__setitem__("sel", sel)

    class _Ev:
        def scenePos(self):
            return QPointF(0, 0)

    panel._on_canvas_click(_Ev())
    assert captured["sel"].fiber_index == 2
    panel.deleteLater()


def test_hex_rotation_is_north_up_east_left():
    """The render transform rotates LUT (N=+x, E=+y) to display (N up, E left)."""
    from llamas_ql.QLook.widgets import hex_view

    center, pitch = (0.0, 0.0), 1.0
    # North (+x in LUT) must map to +y (up) on screen.
    nx, ny = hex_view.to_display(1.0, 0.0, center, pitch)
    assert (round(float(nx), 9), round(float(ny), 9)) == (0.0, 1.0)
    # East (+y in LUT) must map to -x (left) on screen.
    ex, ey = hex_view.to_display(0.0, 1.0, center, pitch)
    assert (round(float(ex), 9), round(float(ey), 9)) == (-1.0, 0.0)
    # to_lut is the exact inverse.
    gx, gy = hex_view.to_lut(nx, ny, center, pitch)
    assert (round(float(gx), 9), round(float(gy), 9)) == (1.0, 0.0)


def test_hex_scatter_size_tiles_the_grid():
    """The scatter marker size makes pointy-top hexagons tile at the spaxel pitch."""
    import math

    from llamas_ql.QLook.widgets.display_panel import HEX_SCATTER_SIZE, SPAXEL_PITCH_ARCSEC

    assert HEX_SCATTER_SIZE == pytest.approx(SPAXEL_PITCH_ARCSEC * 2.0 / math.sqrt(3.0))


def test_whitelight_model_selects_requested_channel(tmp_path):
    """WhiteLightData(channel=...) returns that channel's image + table, not the first."""
    blue = np.zeros((4, 5), dtype=float)
    green = np.ones((4, 5), dtype=float) * 7.0
    path = tmp_path / "wlc.fits"
    hdul = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(data=blue, name="BLUE"),
        fits.BinTableHDU.from_columns([
            fits.Column(name="XDATA", format="E", array=np.array([1.0, 2.0], "f4")),
            fits.Column(name="YDATA", format="E", array=np.array([3.0, 4.0], "f4")),
            fits.Column(name="FLUX", format="E", array=np.array([0.0, 0.0], "f4")),
        ], name="BLUE_TAB"),
        fits.ImageHDU(data=green, name="GREEN"),
        fits.BinTableHDU.from_columns([
            fits.Column(name="XDATA", format="E", array=np.array([5.0, 6.0], "f4")),
            fits.Column(name="YDATA", format="E", array=np.array([7.0, 8.0], "f4")),
            fits.Column(name="FLUX", format="E", array=np.array([9.0, 9.0], "f4")),
        ], name="GREEN_TAB"),
    ])
    hdul.writeto(path)

    wl = WhiteLightData(str(path), channel="green")
    assert wl.channel == "green"
    assert np.allclose(wl.image, green)         # not the first (BLUE) HDU
    assert wl.has_fibers and np.allclose(wl.x, [5.0, 6.0])
    assert np.allclose(wl.flux, [9.0, 9.0])
