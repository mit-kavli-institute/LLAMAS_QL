"""Unit tests for the per-frame edge-bias (DC offset) correction.

Covers the two mask/measure primitives ported from llamas-pyjamas and the
``build_edge_bias_config`` bridge.  No Qt / no real detector data needed.
"""
import numpy as np
import pytest

from llamas_ql.Bias.biasChecking import (
    build_topbottom_stripe_mask,
    measure_edge_dc_offset,
)
from llamas_ql import reduce


class _StubTracer:
    """A trace whose fibres occupy a central horizontal band across all columns."""

    def __init__(self, shape, band=(95, 105)):
        fiberimg = np.full(shape, -1, dtype=int)
        fiberimg[band[0]:band[1], :] = 0   # fibre 0 spans the dispersion axis
        self.fiberimg = fiberimg


def test_stripe_mask_excludes_the_fibre_band():
    shape = (200, 200)
    tr = _StubTracer(shape)
    mask = build_topbottom_stripe_mask(tr, shape, min_distance=20.0)
    # Stripe pixels exist, and none of them fall on the fibre band.
    assert mask.sum() > 0
    assert not mask[95:105, :].any()
    # The far top/bottom rows are unilluminated stripe.
    assert mask[0, 100] and mask[-1, 100]


def test_stripe_mask_empty_without_tracer():
    assert build_topbottom_stripe_mask(None, (50, 50)).sum() == 0
    assert build_topbottom_stripe_mask(object(), (50, 50)).sum() == 0


def test_measure_edge_dc_offset_recovers_known_level():
    rng = np.random.default_rng(1)
    shape = (200, 200)
    data = rng.normal(12.0, 2.0, shape)      # residual DC of 12 in the stripes
    data[95:105, :] += 4000.0                # bright fibre band (must be excluded)
    tr = _StubTracer(shape)
    mask = build_topbottom_stripe_mask(tr, shape, min_distance=20.0)
    offset, n, src = measure_edge_dc_offset(data, mask, min_pixels=100)
    assert src == "ok"
    assert n == int(mask.sum())
    assert offset == pytest.approx(12.0, abs=0.2)


def test_measure_edge_dc_offset_skips_when_too_few_pixels():
    data = np.random.default_rng(2).normal(5.0, 1.0, (50, 50))
    mask = np.ones_like(data, dtype=bool)
    offset, n, src = measure_edge_dc_offset(data, mask, min_pixels=10**9)
    assert src == "skipped" and offset == 0.0


def test_measure_edge_dc_offset_skips_constant_placeholder():
    data = np.full((50, 50), 1.0)            # a placeholder extension
    mask = np.ones_like(data, dtype=bool)
    offset, n, src = measure_edge_dc_offset(data, mask, min_pixels=100)
    assert src == "skipped" and offset == 0.0


def test_build_edge_bias_config_defaults_enabled():
    cfg = reduce.build_edge_bias_config({}, "/tmp/out")
    assert cfg["enabled"] is True
    assert cfg["min_distance"] == 20.0
    assert cfg["min_pixels"] == 500
    assert cfg["use_flat_mask"] is False
    assert cfg["qa_csv"].endswith("edge_bias_levels.csv")


def test_build_edge_bias_config_honours_overrides():
    cfg = reduce.build_edge_bias_config(
        {"edge_bias_dc_correction": False, "edge_bias_min_distance": 30,
         "edge_bias_min_pixels": 250}, "/tmp/out")
    assert cfg["enabled"] is False
    assert cfg["min_distance"] == 30.0
    assert cfg["min_pixels"] == 250
