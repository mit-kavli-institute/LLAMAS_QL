RED_IDXS = [1, 4, 7, 10, 13, 16, 19, 22]
GREEN_IDXS = [2, 5, 8, 11, 14, 17, 20, 23]
BLUE_IDXS = [3, 6, 9, 12, 15, 18, 21, 24]


N_fib = {'1A':298, '1B':300, '2A':298, '2B':297, '3A':298, '3B':300, '4A':300, '4B':298}

N_det = 24

idx_lookup = {
    ("red", "1", "A"): 1,
    ("green", "1", "A"): 2,
    ("blue", "1", "A"): 3,
    ("red", "1", "B"): 4,
    ("green", "1", "B"): 5,
    ("blue", "1", "B"): 6,
    ("red", "2", "A"): 7,
    ("green", "2", "A"): 8,
    ("blue", "2", "A"): 9,
    ("red", "2", "B"): 10,
    ("green", "2", "B"): 11,
    ("blue", "2", "B"): 12,
    ("red", "3", "A"): 13,
    ("green", "3", "A"): 14,
    ("blue", "3", "A"): 15,
    ("red", "3", "B"): 16,
    ("green", "3", "B"): 17,
    ("blue", "3", "B"): 18,
    ("red", "4", "A"): 19,
    ("green", "4", "A"): 20,
    ("blue", "4", "A"): 21,
    ("red", "4", "B"): 22,
    ("green", "4", "B"): 23,
    ("blue", "4", "B"): 24
   
}

gain_noise_lookup ={
    ("red", "1", "A"): {'noise': 2.4, 'gain': 1.25},
    ("green", "1", "A"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "1", "A"): {'noise': 2.4, 'gain': 1.25},
    ("red", "1", "B"): {'noise': 2.4, 'gain': 1.25},
    ("green", "1", "B"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "1", "B"): {'noise': 2.4, 'gain': 1.25},
    ("red", "2", "A"): {'noise': 2.4, 'gain': 1.25},
    ("green", "2", "A"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "2", "A"): {'noise': 2.4, 'gain': 1.25},
    ("red", "2", "B"): {'noise': 2.4, 'gain': 1.25},
    ("green", "2", "B"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "2", "B"): {'noise': 2.4, 'gain': 1.25},
    ("red", "3", "A"): {'noise': 2.4, 'gain': 1.25},
    ("green", "3", "A"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "3", "A"): {'noise': 2.4, 'gain': 1.25},
    ("red", "3", "B"): {'noise': 2.4, 'gain': 1.25},
    ("green", "3", "B"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "3", "B"): {'noise': 2.4, 'gain': 1.25},
    ("red", "4", "A"): {'noise': 2.4, 'gain': 1.25},
    ("green", "4", "A"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "4", "A"): {'noise': 2.4, 'gain': 1.25},
    ("red", "4", "B"): {'noise': 2.4, 'gain': 1.25},
    ("green", "4", "B"): {'noise': 2.4, 'gain': 1.25},
    ("blue", "4", "B"): {'noise': 2.4, 'gain': 1.25}
}

#old version-- kkeping for now to check more conservative values
# LACOSMIC_DEFAULTS = {
#     'contrast': 5.0,
#     'cr_threshold': 4.5,
#     'neighbor_threshold': 0.3,
#     'effective_gain': 1.25,
#     'readnoise': 6.9,
# }

LACOSMIC_DEFAULTS = {
    'contrast': 5.0,
    'cr_threshold': 6.5,
    'neighbor_threshold': 0.7,
    'effective_gain': 1.25,
    'readnoise': 6.9,
    'maxiter': 2
}

# Per-color overrides (merged on top of LACOSMIC_DEFAULTS)
LACOSMIC_COLOR_OVERRIDES = {
    'red': {
        'cr_threshold': 10.0,
        'contrast': 9.0,
        'skip': True,  # disable CR cleaning for red until tuning is finalised
    },
}

# ---------------------------------------------------------------------------
# Bias regions
# ---------------------------------------------------------------------------
# Detector regions (low-signal areas) used to estimate a per-frame bias level
# when the master-bias path is unusable (read-mode mismatch, missing master, or
# a master whose level is wildly off the frame). Each region is a
# ``(y1, y2, x1, x2)`` slice in detector-pixel coords; ``None`` means the full
# extent on that axis. ``region_bias_level`` medians the union of all regions
# for a key. Keyed by benchside ('1A'..'4B') with a 'default' fallback.
#
# TODO(observer): replace the placeholder 'default' rows with the true bias/
# overscan regions for LLAMAS detectors; add per-benchside entries as needed.
BIAS_REGIONS = {
    'default': [(30, 50, None, None)],  # rows 30-50, all columns (legacy behaviour)
}

# Max |master-bias median - region-bias estimate| (DN) before the master is
# judged "wildly off" and the region-based estimate is substituted instead.
BIAS_OFFSET_WARN_DN = 50.0


def region_bias_level(data, key='default'):
    """Estimate a scalar bias level (DN) from ``BIAS_REGIONS`` for a detector.

    Args:
        data: 2-D detector frame (anything convertible to float).
        key: Benchside key (e.g. '2A'); falls back to 'default' if absent.

    Returns:
        float median over the union of the key's regions, or ``nan`` if no
        region pixels are available.
    """
    import numpy as np

    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2:
        return float('nan')
    regions = BIAS_REGIONS.get(key) or BIAS_REGIONS.get('default', [])
    pixels = []
    for (y1, y2, x1, x2) in regions:
        ys = slice(y1, y2)
        xs = slice(x1, x2)
        sub = arr[ys, xs]
        if sub.size:
            pixels.append(sub.ravel())
    if not pixels:
        return float('nan')
    return float(np.nanmedian(np.concatenate(pixels)))

