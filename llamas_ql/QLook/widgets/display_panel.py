"""Center column: the square IFU display with toolbar, colorbar and wavelength slider.

Renders the loaded RSS as a white-light fibre image on a dark canvas (compass +
10" scale bar overlays), with mode/grid/channel/colormap controls in a 46px
toolbar and a wavelength scrubber beneath.  Empty / ready-to-reduce / reducing
states are shown as overlay pages.  Spaxel clicks publish a fibre selection on
the session for the spectrum panel.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core import theme
from llamas_ql.QLook.core.session import Selection, SelectionKind, Session
from llamas_ql.QLook.models.fiber_geometry import FiberGeometry
from llamas_ql.QLook.widgets._overlays import (
    LLAMAS_FOV_ARCSEC,
    add_compass,
    add_scale_bar,
)


def _seg_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    return b


class DisplayPanel(QWidget):
    channel_changed = Signal(str)   # 'blue' | 'green' | 'red'
    reduce_clicked = Signal(str)    # path (from the centre 'Reduce frame' CTA)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("centerColumn")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._geom: FiberGeometry | None = None
        self._mode = "whitelight"
        self._colormap = "viridis"
        self._channel = "green"
        self._grid_items: list = []
        self._pending_path: str | None = None
        self._stretch = "linear"
        self._clip = (1.0, 99.0)   # (lo, hi) percentiles for the scale limits

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_body(), 1)

        self.session.rss_loaded.connect(self._on_rss)

    # -- toolbar -------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("viewerToolbar")
        bar.setFixedHeight(46)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(14)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        self._title = QLabel("No frame loaded")
        self._title.setStyleSheet("background:transparent; font-size:13px; font-weight:600;")
        self._subtitle = QLabel("—")
        self._subtitle.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:11px; color:{theme.TEXT_MUTED};"
        )
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        lay.addLayout(titles)
        lay.addStretch(1)

        # Controls live in one container we can show/hide as a group.
        self._controls = QWidget()
        cl = QHBoxLayout(self._controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)

        # Mode segmented (White-light / λ slice).
        modew = QWidget()
        modew.setAttribute(Qt.WA_StyledBackground, True)
        modew.setStyleSheet(f"background:{theme.TOOLBAR_GROUP}; border-radius:7px;")
        ml = QHBoxLayout(modew)
        ml.setContentsMargins(2, 2, 2, 2)
        ml.setSpacing(2)
        self._mode_white = _seg_button("White-light")
        self._mode_slice = _seg_button("λ slice")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for b, m in ((self._mode_white, "whitelight"), (self._mode_slice, "slice")):
            self._mode_group.addButton(b)
            ml.addWidget(b)
            b.clicked.connect(lambda _c=False, mode=m: self._set_mode(mode))
        self._mode_white.setChecked(True)
        cl.addWidget(modew)

        # IFU grid toggle.
        self._grid_btn = QPushButton("IFU grid")
        self._grid_btn.setCheckable(True)
        self._grid_btn.setCursor(Qt.PointingHandCursor)
        self._grid_btn.toggled.connect(self._set_grid)
        cl.addWidget(self._grid_btn)

        # Channel switch (Blue / Green / Red).
        chanw = QWidget()
        chanw.setAttribute(Qt.WA_StyledBackground, True)
        chanw.setStyleSheet(f"background:{theme.TOOLBAR_GROUP}; border-radius:7px;")
        chl = QHBoxLayout(chanw)
        chl.setContentsMargins(2, 2, 2, 2)
        chl.setSpacing(2)
        self._chan_group = QButtonGroup(self)
        self._chan_group.setExclusive(True)
        self._chan_buttons = {}
        for label, ch in (("B", "blue"), ("G", "green"), ("R", "red")):
            b = _seg_button(label)
            self._chan_group.addButton(b)
            self._chan_buttons[ch] = b
            chl.addWidget(b)
            b.clicked.connect(lambda _c=False, c=ch: self._set_channel(c))
        self._chan_buttons["green"].setChecked(True)
        cl.addWidget(chanw)

        # Colormap.
        self._cmap = QComboBox()
        self._cmap.addItems(["viridis", "grayscale"])
        self._cmap.currentTextChanged.connect(self._set_colormap)
        cl.addWidget(self._cmap)

        lay.addWidget(self._controls)
        self._controls.setVisible(False)
        self._style_segments()
        return bar

    # -- body ----------------------------------------------------------------

    def _build_body(self) -> QWidget:
        body = QWidget()
        body.setObjectName("centerColumn")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)

        stage = QHBoxLayout()
        stage.setSpacing(12)

        # Stacked canvas: overlay pages + the pyqtgraph image.
        self._stack = QStackedWidget()
        self._stack.setAttribute(Qt.WA_StyledBackground, True)
        self._stack.setStyleSheet(
            f"background:{theme.CANVAS}; border:1px solid {theme.CANVAS_BORDER}; border-radius:8px;"
        )
        self._page_empty = self._make_empty_page()
        self._page_ready = self._make_ready_page()
        self._page_reducing = self._make_reducing_page()
        self._plot = self._make_plot()
        for w in (self._page_empty, self._page_ready, self._page_reducing, self._plot):
            self._stack.addWidget(w)
        self._stack.setCurrentWidget(self._page_empty)
        stage.addWidget(self._stack, 1)

        stage.addWidget(self._make_colorbar())
        lay.addLayout(stage, 1)

        lay.addWidget(self._make_wavelength_bar())
        lay.addWidget(self._make_scale_bar())
        return body

    def _make_plot(self) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setBackground(theme.CANVAS)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.getPlotItem().hideAxis("left")
        plot.getPlotItem().hideAxis("bottom")
        vb = plot.getPlotItem().getViewBox()
        vb.setAspectLocked(True)
        half = LLAMAS_FOV_ARCSEC / 2.0
        vb.setRange(xRange=(-half, half), yRange=(-half, half), padding=0.02)
        self._scatter = pg.ScatterPlotItem(size=9, pen=None)
        plot.addItem(self._scatter)
        add_scale_bar(vb, size=10.0)
        add_compass(vb)
        plot.scene().sigMouseClicked.connect(self._on_canvas_click)
        self._vb = vb
        return plot

    def _make_empty_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        box = QLabel()
        box.setFixedSize(46, 46)
        box.setStyleSheet("border:1.5px dashed #3a4452; border-radius:10px; background:transparent;")
        l1 = QLabel("Select a frame from tonight's run")
        l1.setStyleSheet("background:transparent; color:#7c8898; font-size:13px;")
        l1.setAlignment(Qt.AlignCenter)
        l2 = QLabel("pick a science frame, then press Reduce")
        l2.setStyleSheet(
            f"background:transparent; color:#566273; font-family:'{theme.mono_family()}'; font-size:11px;"
        )
        l2.setAlignment(Qt.AlignCenter)
        lay.addWidget(box, 0, Qt.AlignHCenter)
        lay.addWidget(l1)
        lay.addWidget(l2)
        return w

    def _make_ready_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(13)
        self._ready_name = QLabel("")
        self._ready_name.setAlignment(Qt.AlignCenter)
        self._ready_name.setStyleSheet(
            f"background:transparent; color:#9aa6b6; font-family:'{theme.mono_family()}'; font-size:12px;"
        )
        msg = QLabel("Science frame · ready for quick reduction")
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet("background:transparent; color:#c6d0dd; font-size:14px;")
        cta = QPushButton("▶  Reduce frame")
        cta.setCursor(Qt.PointingHandCursor)
        cta.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT}; color:#fff; border:none; border-radius:8px;"
            f" font-size:13px; font-weight:600; padding:10px 20px; }}"
        )
        cta.clicked.connect(lambda: self._pending_path and self.reduce_clicked.emit(self._pending_path))
        lay.addWidget(self._ready_name)
        lay.addWidget(msg)
        lay.addWidget(cta, 0, Qt.AlignHCenter)
        return w

    def _make_reducing_page(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(14)
        bar = QFrame()
        bar.setFixedSize(180, 3)
        bar.setStyleSheet("background:#5e9bf0; border-radius:2px;")
        txt = QLabel("reducing · bias − flat / arc · cube recon")
        txt.setAlignment(Qt.AlignCenter)
        txt.setStyleSheet(
            f"background:transparent; color:#9fc1f2; font-family:'{theme.mono_family()}'; font-size:11px; letter-spacing:1px;"
        )
        lay.addWidget(bar, 0, Qt.AlignHCenter)
        lay.addWidget(txt)
        return w

    def _make_colorbar(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(40)
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        self._cb_max = QLabel("max")
        self._cb_min = QLabel("min")
        for l in (self._cb_max, self._cb_min):
            l.setAlignment(Qt.AlignRight)
            l.setStyleSheet(
                f"background:transparent; color:{theme.TEXT_SECONDARY}; font-family:'{theme.mono_family()}'; font-size:10px;"
            )
        self._cb_bar = QFrame()
        self._cb_bar.setFixedWidth(14)
        lay.addWidget(self._cb_max)
        lay.addWidget(self._cb_bar, 1)
        lay.addWidget(self._cb_min)
        self._style_colorbar()
        return w

    def _make_wavelength_bar(self) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE}; border:1px solid {theme.CANVAS_BORDER}; border-radius:8px;"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)
        cap = QLabel("WAVELENGTH")
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:11px; letter-spacing:1px;"
        )
        self._wave_slider = QSlider(Qt.Horizontal)
        self._wave_slider.setMinimum(350)
        self._wave_slider.setMaximum(975)
        self._wave_slider.setValue(656)
        self._wave_slider.valueChanged.connect(self._on_wavelength)
        self._chan_chip = QLabel("GREEN")
        self._wave_val = QLabel("656 nm")
        self._wave_val.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:15px; font-weight:600;"
        )
        lay.addWidget(cap)
        lay.addWidget(self._wave_slider, 1)
        lay.addWidget(self._chan_chip)
        lay.addWidget(self._wave_val)
        self._update_wave_chip(656)
        return w

    def _make_scale_bar(self) -> QWidget:
        """Image display controls: stretch, scale limits, and reset view."""
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE}; border:1px solid {theme.CANVAS_BORDER}; border-radius:8px;"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        def cap(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(
                f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:11px; letter-spacing:1px;"
            )
            return l

        lay.addWidget(cap("STRETCH"))
        self._stretch_combo = QComboBox()
        self._stretch_combo.addItems(["Linear", "Sqrt", "Log", "Asinh"])
        self._stretch_combo.currentTextChanged.connect(self._set_stretch)
        lay.addWidget(self._stretch_combo)

        lay.addSpacing(8)
        lay.addWidget(cap("SCALE"))
        self._clip_combo = QComboBox()
        self._clip_combo.addItems(["99%", "99.5%", "95%", "90%", "Min–Max"])
        self._clip_combo.currentTextChanged.connect(self._set_clip)
        lay.addWidget(self._clip_combo)

        lay.addStretch(1)
        reset = QPushButton("Reset view")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset_view)
        lay.addWidget(reset)
        return w

    # -- public API ----------------------------------------------------------

    def show_ready(self, item) -> None:
        """Show the ready-to-reduce overlay for a selected science frame."""
        self._pending_path = item.path
        self._ready_name.setText(item.filename)
        self._title.setText(item.filename)
        self._subtitle.setText(f"{item.obj or '—'} · {item.exp_str}")
        self._controls.setVisible(False)
        self._stack.setCurrentWidget(self._page_ready)

    def show_not_reduced(self, item) -> None:
        """A non-science frame is selected: nothing to display."""
        self._pending_path = None
        self._title.setText(item.filename)
        self._subtitle.setText(f"marked {item.ftype}")
        self._controls.setVisible(False)
        self._stack.setCurrentWidget(self._page_empty)

    def show_empty(self) -> None:
        self._pending_path = None
        self._title.setText("No frame loaded")
        self._subtitle.setText("—")
        self._controls.setVisible(False)
        self._stack.setCurrentWidget(self._page_empty)

    def show_reducing(self, item) -> None:
        self._title.setText(item.filename)
        self._subtitle.setText("reducing…")
        self._controls.setVisible(False)
        self._stack.setCurrentWidget(self._page_reducing)

    def current_channel(self) -> str:
        return self._channel

    # -- session reaction ----------------------------------------------------

    def _on_rss(self, rss) -> None:
        if rss is None or getattr(rss, "fibermap", None) is None:
            self._geom = None
            self._scatter.clear()
            return
        self._geom = FiberGeometry(rss)
        ch = str(getattr(rss, "channel", "green")).lower()
        if ch in self._chan_buttons:
            self._channel = ch
            self._chan_buttons[ch].setChecked(True)
            self._update_wave_chip(self._wave_slider.value())
        self._title.setText(getattr(rss, "primary_header", {}).get("OBJECT", "RSS") if getattr(rss, "primary_header", None) else "RSS")
        self._subtitle.setText(f"RGB cube · {ch}")
        self._controls.setVisible(True)
        self._render()
        self._stack.setCurrentWidget(self._plot)

    def _render(self) -> None:
        if self._geom is None:
            return
        if self._mode == "slice":
            center = self._wave_slider.value() * 10.0  # nm -> Å
            flux = self._geom.whitelight_flux(center - 50.0, center + 50.0)
        else:
            flux = self._geom.whitelight_flux()
        valid = self._geom.valid
        x = self._geom.x[valid]
        y = self._geom.y[valid]
        f = flux[valid]
        rows = np.nonzero(valid)[0]
        brushes = self._colors(f)
        spots = [
            {"pos": (x[i], y[i]), "data": int(rows[i]), "brush": brushes[i]}
            for i in range(len(rows))
        ]
        self._scatter.setData(spots)

    def _colors(self, values: np.ndarray):
        v = np.asarray(values, dtype=float)
        finite = v[np.isfinite(v)]
        if finite.size == 0:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = np.nanpercentile(finite, [self._clip[0], self._clip[1]])
            if hi <= lo:
                hi = lo + 1.0
        norm = np.clip((v - lo) / (hi - lo), 0, 1)
        norm[~np.isfinite(norm)] = 0.0
        norm = self._apply_stretch(norm)
        name = "viridis" if self._colormap == "viridis" else "CET-L1"
        try:
            cmap = pg.colormap.get(name)
        except Exception:
            cmap = pg.colormap.get("viridis")
        lut = cmap.getLookupTable(0.0, 1.0, 256)
        idx = (norm * 255).astype(int)
        return [pg.mkBrush(*lut[i]) for i in idx]

    def _on_canvas_click(self, event) -> None:
        if self._geom is None:
            return
        pt = self._vb.mapSceneToView(event.scenePos())
        row = self._geom.nearest_fiber(pt.x(), pt.y())
        if row is None:
            return
        self.session.set_selection(
            Selection(kind=SelectionKind.FIBER, fiber_index=row, label=f"fiber {row}")
        )

    # -- control handlers ----------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._render()

    def _set_grid(self, on: bool) -> None:
        for it in self._grid_items:
            self._vb.removeItem(it)
        self._grid_items = []
        if on:
            half = LLAMAS_FOV_ARCSEC / 2.0
            pen = pg.mkPen(color=(220, 230, 245, 56), width=1)
            # 2 rows x 4 cols grid: 3 vertical + 1 horizontal interior line.
            for c in range(1, 4):
                line = pg.InfiniteLine(pos=-half + c * (2 * half) / 4, angle=90, pen=pen)
                self._vb.addItem(line)
                self._grid_items.append(line)
            mid = pg.InfiniteLine(pos=0.0, angle=0, pen=pen)
            self._vb.addItem(mid)
            self._grid_items.append(mid)

    def _set_channel(self, ch: str) -> None:
        self._channel = ch
        self._update_wave_chip(self._wave_slider.value())
        self._style_segments()
        self.channel_changed.emit(ch)

    def _set_colormap(self, name: str) -> None:
        self._colormap = name
        self._style_colorbar()
        self._render()

    def _set_stretch(self, name: str) -> None:
        self._stretch = name.lower()
        self._render()

    def _set_clip(self, name: str) -> None:
        self._clip = {
            "99%": (1.0, 99.0),
            "99.5%": (0.5, 99.5),
            "95%": (5.0, 95.0),
            "90%": (10.0, 90.0),
            "Min–Max": (0.0, 100.0),
        }.get(name, (1.0, 99.0))
        self._render()

    def _reset_view(self) -> None:
        half = LLAMAS_FOV_ARCSEC / 2.0
        self._vb.setRange(xRange=(-half, half), yRange=(-half, half), padding=0.02)

    def _apply_stretch(self, norm: np.ndarray) -> np.ndarray:
        """Map normalized [0,1] values through the selected display stretch."""
        n = np.clip(norm, 0.0, 1.0)
        if self._stretch == "sqrt":
            return np.sqrt(n)
        if self._stretch == "log":
            return np.log10(1.0 + 9.0 * n)
        if self._stretch == "asinh":
            return np.arcsinh(10.0 * n) / np.arcsinh(10.0)
        return n

    def _on_wavelength(self, value: int) -> None:
        self._update_wave_chip(value)
        if self._mode == "slice":
            self._render()

    def _update_wave_chip(self, value: int) -> None:
        for ch, (lo, hi) in theme.CHANNEL_BANDS.items():
            if lo <= value < hi:
                band = ch
                break
        else:
            band = "red"
        color = theme.CHANNEL_COLORS[band]
        self._chan_chip.setText(band.upper())
        self._chan_chip.setStyleSheet(
            f"background:{color}; color:#fff; font-family:'{theme.mono_family()}'; font-size:10px;"
            " font-weight:700; letter-spacing:1px; border-radius:4px; padding:2px 6px;"
        )
        self._wave_val.setText(f"{value} nm")

    # -- styling helpers -----------------------------------------------------

    def _style_segments(self) -> None:
        def style(btn: QPushButton, active: bool, accent: str) -> None:
            bg = "#fff" if active else "transparent"
            fg = accent if active else theme.TEXT_SECONDARY
            btn.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border:none; border-radius:5px;"
                f" padding:5px 11px; font-size:12px; font-weight:500; }}"
            )
        style(self._mode_white, self._mode_white.isChecked(), theme.ACCENT)
        style(self._mode_slice, self._mode_slice.isChecked(), theme.ACCENT)
        for ch, b in self._chan_buttons.items():
            style(b, b.isChecked(), theme.CHANNEL_COLORS[ch])

    def _style_colorbar(self) -> None:
        stops = theme.VIRIDIS_STOPS if self._colormap == "viridis" else theme.GRAYSCALE_STOPS
        grad = theme.linear_gradient_qss(stops, horizontal=False)
        # bottom->top: reverse so high values at top.
        self._cb_bar.setStyleSheet(
            f"background:{theme.linear_gradient_qss(list(reversed(stops)))};"
            f" border:1px solid {theme.CANVAS_BORDER}; border-radius:3px;"
        )
