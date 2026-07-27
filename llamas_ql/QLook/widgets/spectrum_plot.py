"""Interactive 1-D spectrum plot (pyqtgraph).

Shows the spectrum of the current selection, with a FLUX/COUNTS/SKY toggle and
the ability to pin spectra for side-by-side comparison.  Driven entirely by the
:class:`Session`: it reacts to ``selection_changed`` / ``rss_loaded`` /
``pinned_changed`` and never reaches into other widgets.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from llamas_ql.QLook.core.session import PinnedSpectrum, SelectionKind, Session

_PIN_COLORS = ["#e06c75", "#98c379", "#61afef", "#c678dd", "#e5c07b", "#56b6c2"]


class SpectrumPlot(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._kind = "flux"
        self._current = None  # (label, wave, values) for the live selection

        # Controls: kind toggle + pin/clear.
        self._flux_rb = QRadioButton("Flux")
        self._counts_rb = QRadioButton("Counts")
        self._sky_rb = QRadioButton("Sky")
        self._flux_rb.setChecked(True)
        self._group = QButtonGroup(self)
        for rb, kind in ((self._flux_rb, "flux"), (self._counts_rb, "counts"), (self._sky_rb, "sky")):
            self._group.addButton(rb)
            rb.toggled.connect(lambda checked, k=kind: self._on_kind(k) if checked else None)

        self._pin_btn = QPushButton("Pin")
        self._pin_btn.clicked.connect(self._on_pin)
        self._clear_btn = QPushButton("Clear pins")
        self._clear_btn.clicked.connect(self.session.clear_pins)

        controls = QHBoxLayout()
        controls.addWidget(self._flux_rb)
        controls.addWidget(self._counts_rb)
        controls.addWidget(self._sky_rb)
        controls.addStretch(1)
        controls.addWidget(self._pin_btn)
        controls.addWidget(self._clear_btn)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1e1e1e")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.addLegend()
        self._plot.setLabel("bottom", "Pixel")
        self._plot.setLabel("left", "Flux")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(self._plot)

        self.session.selection_changed.connect(self._on_selection)
        self.session.pinned_changed.connect(lambda _l: self._redraw())
        self.session.rss_loaded.connect(lambda _r: self._update_axis_label())

    # -- session reactions ---------------------------------------------------

    def _on_kind(self, kind: str) -> None:
        self._kind = kind
        self._refresh_current()
        self._redraw()

    def _on_selection(self, selection) -> None:
        self._refresh_current()
        self._redraw()

    def _refresh_current(self) -> None:
        self._current = None
        sel = self.session.selection
        if sel is None:
            return
        # Cube spaxel/region selections carry their own precomputed spectrum
        # (the FLUX/COUNTS/SKY toggle only applies to RSS fibre spectra).
        if sel.kind != SelectionKind.FIBER and sel.values is not None:
            wave = np.asarray(sel.wave)
            values = np.asarray(sel.values)
            if values.size:
                self._current = (sel.label or "region", wave, values)
            return
        rss = self.session.current_rss
        if rss is None or sel.fiber_index is None:
            return
        wave, values = rss.spectrum(sel.fiber_index, kind=self._kind)
        if wave.size:
            label = sel.label or f"fiber {sel.fiber_index}"
            self._current = (f"{label} [{self._kind}]", wave, values)

    def _update_axis_label(self) -> None:
        rss = self.session.current_rss
        self._plot.setLabel("bottom", rss.wave_label if rss is not None else "Pixel")
        self._refresh_current()
        self._redraw()

    # -- drawing -------------------------------------------------------------

    def _redraw(self) -> None:
        self._plot.clear()
        # need a fresh legend after clear()
        self._plot.addLegend()
        for i, pinned in enumerate(self.session.pinned_spectra):
            color = pinned.color or _PIN_COLORS[i % len(_PIN_COLORS)]
            self._plot.plot(pinned.wave, pinned.flux, pen=pg.mkPen(color, width=1),
                            name=pinned.label)
        if self._current is not None:
            label, wave, values = self._current
            self._plot.plot(wave, values, pen=pg.mkPen("#ffffff", width=1.5), name=label)

    def _on_pin(self) -> None:
        if self._current is None:
            return
        label, wave, values = self._current
        n = len(self.session.pinned_spectra)
        self.session.pin_spectrum(
            PinnedSpectrum(label=label, wave=np.asarray(wave), flux=np.asarray(values),
                           color=_PIN_COLORS[n % len(_PIN_COLORS)])
        )
