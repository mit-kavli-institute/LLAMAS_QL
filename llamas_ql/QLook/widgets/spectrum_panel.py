"""Right column: extracted-spectrum plot, channel legend, aperture, frame header.

Light-theme spectrum plot (blue/green/red channel bands) that draws the selected
fibre's real spectrum when available; below it a channel legend, an APERTURE row,
and a FRAME HEADER key/value table (OBJECT/TYPE/EXPTIME from the FITS header,
remaining fields as static placeholders, matching the prototype).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core import theme
from llamas_ql.QLook.core.session import SelectionKind, Session

_HEADER_PLACEHOLDERS = [
    ("RA (J2000)", "02:42:40.8"),
    ("DEC (J2000)", "−00:00:48"),
    ("UT START", "03:14:21"),
    ("AIRMASS", "1.08"),
    ("SEEING (DIMM)", "0.62″"),
    ("S/N B / G / R", "42 / 118 / 96"),
]


class SpectrumPanel(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("rightColumn")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(392)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._spec_channel = "green"
        self._full_spec: tuple | None = None  # (x_nm, y) last drawn spectrum

        root.addWidget(self._build_header())
        root.addWidget(self._build_plot())
        root.addWidget(self._build_channel_selector())
        root.addWidget(self._build_aperture())
        root.addWidget(self._build_frame_header())
        root.addStretch(1)

        self._set_spec_channel("green")

        self.session.selection_changed.connect(self._on_selection)
        self.session.rss_loaded.connect(self._on_rss)

    # -- sections ------------------------------------------------------------

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE}; border-bottom:1px solid {theme.DIVIDER};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 11, 14, 8)
        title = QLabel("Extracted Spectrum")
        title.setStyleSheet("background:transparent; font-size:12px; font-weight:600;")
        self._spaxel = QLabel("—")
        self._spaxel.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:11px; color:{theme.TEXT_MUTED};"
        )
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(self._spaxel)
        return w

    def _build_plot(self) -> QWidget:
        self._plot = pg.PlotWidget()
        self._plot.setBackground(theme.SURFACE_ALT)
        self._plot.setFixedHeight(206)
        self._plot.setMenuEnabled(False)
        self._plot.hideButtons()
        self._plot.showGrid(x=True, y=True, alpha=0.12)
        pi = self._plot.getPlotItem()
        pi.getAxis("bottom").setPen(pg.mkPen(theme.GRIDLINE))
        pi.getAxis("left").setPen(pg.mkPen(theme.GRIDLINE))
        pi.getAxis("bottom").setTextPen(pg.mkPen(theme.TEXT_MUTED))
        pi.getAxis("left").setTextPen(pg.mkPen(theme.TEXT_MUTED))
        self._plot.setLabel("bottom", "wavelength (nm)")
        self._bands = {}
        self._add_channel_bands()
        self._curve = self._plot.plot([], [], pen=pg.mkPen(theme.TEXT, width=1.4))
        self._plot.enableAutoRange(axis="x", enable=False)
        self._draw_placeholder()
        return self._plot

    def _add_channel_bands(self) -> None:
        # One tinted band per channel; only the active channel's band is shown.
        for ch, (lo, hi) in theme.CHANNEL_BANDS.items():
            color = pg.mkColor(theme.CHANNEL_COLORS[ch])
            color.setAlpha(18)
            region = pg.LinearRegionItem(
                values=(lo, hi), movable=False, brush=pg.mkBrush(color),
                pen=pg.mkPen(None),
            )
            region.setZValue(-10)
            self._plot.addItem(region)
            self._bands[ch] = region

    def _build_channel_selector(self) -> QWidget:
        """Blue / Green / Red buttons that pick which channel the plot shows."""
        w = QWidget()
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background:{theme.SURFACE}; border-bottom:1px solid {theme.DIVIDER};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 6, 12, 10)
        lay.setSpacing(8)
        self._chan_group = QButtonGroup(self)
        self._chan_group.setExclusive(True)
        self._chan_buttons: dict[str, QPushButton] = {}
        labels = {"blue": "Blue · 350–480", "green": "Green · 480–690", "red": "Red · 690–975"}
        for ch in ("blue", "green", "red"):
            b = QPushButton(labels[ch])
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self._chan_group.addButton(b)
            self._chan_buttons[ch] = b
            b.clicked.connect(lambda _c=False, c=ch: self._set_spec_channel(c))
            lay.addWidget(b)
        lay.addStretch(1)
        return w

    def _set_spec_channel(self, ch: str) -> None:
        self._spec_channel = ch
        lo, hi = theme.CHANNEL_BANDS[ch]
        self._plot.setXRange(lo, hi, padding=0.02)
        self._curve.setPen(pg.mkPen(theme.CHANNEL_COLORS[ch], width=1.4))
        for c, region in self._bands.items():
            region.setVisible(c == ch)
        for c, btn in self._chan_buttons.items():
            active = c == ch
            color = theme.CHANNEL_COLORS[c]
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(
                    f"QPushButton {{ background:{color}; color:#fff; border:1px solid {color};"
                    f" border-radius:5px; padding:4px 8px; font-size:11px; font-weight:600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{theme.SURFACE}; color:{color}; border:1px solid #dcdbd6;"
                    f" border-radius:5px; padding:4px 8px; font-size:11px; font-weight:500; }}"
                )

    def _build_aperture(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 8, 14, 2)
        cap = QLabel("APERTURE")
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_MUTED}; font-size:11px; letter-spacing:1px;"
        )
        chip = QLabel("LIVE")
        chip.setStyleSheet(
            f"background:{theme.GREEN_BG}; color:{theme.GREEN}; font-size:9px; font-weight:700;"
            " letter-spacing:1px; border-radius:4px; padding:2px 6px;"
        )
        self._aperture = QLabel("Δα +0.0″   Δδ +0.0″")
        self._aperture.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:12px;"
        )
        lay.addWidget(cap)
        lay.addWidget(chip)
        lay.addStretch(1)
        lay.addWidget(self._aperture)
        return w

    def _build_frame_header(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 18)
        lay.setSpacing(0)
        cap = QLabel("FRAME HEADER")
        cap.setStyleSheet(
            f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:11px;"
            " letter-spacing:1px; margin-bottom:8px;"
        )
        lay.addWidget(cap)
        self._header_rows: dict[str, QLabel] = {}
        for key in ["OBJECT", "TYPE", "EXPTIME"] + [k for k, _ in _HEADER_PLACEHOLDERS]:
            lay.addWidget(self._header_row(key, ""))
        # Seed placeholders.
        for key, val in _HEADER_PLACEHOLDERS:
            self._header_rows[key].setText(val)
        return w

    def _header_row(self, key: str, value: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"border-bottom:1px solid {theme.ROW_DIVIDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 6)
        k = QLabel(key)
        k.setStyleSheet(f"background:transparent; color:{theme.TEXT_SECONDARY}; font-size:12px;")
        v = QLabel(value)
        v.setAlignment(Qt.AlignRight)
        v.setStyleSheet(
            f"background:transparent; font-family:'{theme.mono_family()}'; font-size:12px; font-weight:500;"
        )
        lay.addWidget(k)
        lay.addStretch(1)
        lay.addWidget(v)
        self._header_rows[key] = v
        return w

    # -- public API ----------------------------------------------------------

    def set_frame_meta(self, obj: str, ftype: str, exptime) -> None:
        """Populate the real-from-header rows for the selected/loaded frame."""
        self._header_rows["OBJECT"].setText(obj or "—")
        self._header_rows["TYPE"].setText(ftype or "—")
        self._header_rows["EXPTIME"].setText("—" if exptime is None else f"{exptime:g}s")

    # -- reactions -----------------------------------------------------------

    def _on_rss(self, rss) -> None:
        hdr = getattr(rss, "primary_header", None) if rss is not None else None
        if hdr is not None:
            self.set_frame_meta(hdr.get("OBJECT", ""), hdr.get("OBSTYPE", "Science"),
                                hdr.get("EXPTIME", None))
        # Follow the loaded channel so the plot shows where the data actually is.
        ch = str(getattr(rss, "channel", "")).lower() if rss is not None else ""
        if ch in self._chan_buttons:
            self._set_spec_channel(ch)

    def _on_selection(self, selection) -> None:
        if selection is None:
            return
        rss = self.session.current_rss
        if selection.kind == SelectionKind.FIBER and rss is not None and selection.fiber_index is not None:
            wave, values = rss.spectrum(selection.fiber_index, kind="flux")
            if wave.size:
                # Convert Å -> nm if the axis looks like wavelength.
                x = wave / 10.0 if float(np.nanmax(wave)) > 1500 else wave
                self._curve.setData(x, values)
                self._spaxel.setText(f"fiber {selection.fiber_index}")
                return
        if selection.values is not None and np.asarray(selection.values).size:
            wave = np.asarray(selection.wave, dtype=float)
            x = wave / 10.0 if wave.size and float(np.nanmax(wave)) > 1500 else wave
            self._curve.setData(x, np.asarray(selection.values, dtype=float))
            self._spaxel.setText(selection.label or "region")

    def _draw_placeholder(self) -> None:
        # A faint representative trace so the panel reads as a spectrum when idle.
        x = np.linspace(350, 975, 300)
        y = (
            0.6
            + 0.3 * np.exp(-((x - 500) ** 2) / 400)
            + 0.5 * np.exp(-((x - 656) ** 2) / 120)
            + 0.05 * np.sin(x / 12.0)
        )
        self._curve.setData(x, y)
