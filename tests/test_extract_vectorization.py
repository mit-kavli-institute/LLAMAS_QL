"""Regression test: the vectorised optimal extraction matches the original loop.

The old implementation extracted each fibre with a per-column Python scan::

    for i in range(naxis1):
        thisx = (x_spec == i)
        if np.nansum(thisx) > 0:
            extracted[i] = nansum(f_spec[thisx]*weights[thisx]) / nansum(weights[thisx])
        else:
            extracted[i] = 0.0

``ExtractLlamas._optimal_extract_column`` replaces that with a ``np.bincount``
grouped weighted mean. This test pins the two to bit-for-bit-equivalent results
(within float tolerance), including the NaN handling, so future edits can't
silently change the science output.
"""
import numpy as np
import pytest

from llamas_ql.Extract.extractLlamas import ExtractLlamas


def _reference_loop(x_spec, f_spec, weights, naxis1):
    extracted = np.zeros(naxis1)
    for i in range(naxis1):
        thisx = (x_spec == i)
        if np.nansum(thisx) > 0:
            with np.errstate(divide="ignore", invalid="ignore"):
                extracted[i] = np.nansum(f_spec[thisx] * weights[thisx]) / np.nansum(weights[thisx])
        else:
            extracted[i] = 0.0
    return extracted


@pytest.mark.parametrize("seed", range(25))
def test_optimal_extract_matches_reference(seed):
    rng = np.random.default_rng(seed)
    naxis1 = 128
    npix = int(rng.integers(400, 3000))
    x_spec = rng.integers(0, naxis1, size=npix).astype(float)  # integer-valued cols
    f_spec = rng.normal(1000, 300, size=npix)
    weights = rng.uniform(0.0, 1.0, size=npix)
    f_spec[rng.random(npix) < 0.05] = np.nan   # NaN flux pixels
    weights[rng.random(npix) < 0.05] = np.nan  # NaN weight pixels

    ref = _reference_loop(x_spec, f_spec, weights, naxis1)
    got = ExtractLlamas._optimal_extract_column(x_spec, f_spec, weights, naxis1)

    assert got.shape == (naxis1,)
    # NaN appears identically in both where a non-empty column has all-NaN weights.
    assert np.array_equal(np.isnan(ref), np.isnan(got))
    finite = np.isfinite(ref) & np.isfinite(got)
    assert np.allclose(ref[finite], got[finite], rtol=1e-9, atol=1e-9)


def test_empty_columns_are_zero():
    naxis1 = 10
    # only columns 2 and 5 receive any pixels
    x_spec = np.array([2, 2, 5], dtype=float)
    f_spec = np.array([10.0, 30.0, 7.0])
    weights = np.array([1.0, 1.0, 2.0])
    got = ExtractLlamas._optimal_extract_column(x_spec, f_spec, weights, naxis1)
    assert got[2] == pytest.approx(20.0)   # (10+30)/(1+1)
    assert got[5] == pytest.approx(7.0)    # (7*2)/2
    zero_cols = [c for c in range(naxis1) if c not in (2, 5)]
    assert np.all(got[zero_cols] == 0.0)
