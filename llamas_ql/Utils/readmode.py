"""
Utility for LLAMAS CCD read-mode detection.

Reads the READ-MDE keyword from a primary FITS header. If the keyword is
absent (older data), infers the mode from the median of rows 30-50 of
image_data using a 1000 DN threshold. Always defaults to 'SLOW' if
neither method yields a definitive answer.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

# Pixel threshold (DN) separating FAST from SLOW mode bias level
_FAST_SLOW_THRESHOLD = 1000.0


def get_readmode(header, image_data=None) -> str:
    """
    Determine the CCD read mode for a LLAMAS detector extension.

    Args:
        header (astropy.io.fits.Header (or any dict-like)): A FITS primary or extension header. The key ``READ-MDE`` is
            inspected first.
        image_data (numpy.ndarray, optional): Raw 2-D image array. Used only when ``READ-MDE`` is absent from
            *header*. The median of rows 30-50 is compared to
            ``_FAST_SLOW_THRESHOLD``: values above the threshold imply 'FAST'
            mode (higher dark current / shorter integration in overscan region).

    Returns:
        str: ``'FAST'`` or ``'SLOW'`` (always upper-case).

    Notes:
        Default is ``'SLOW'``. No exception is ever raised — this function is
        designed for defensive use in fallback paths.
    """
    # Priority 1: explicit header keyword
    raw = header.get('READ-MDE', None)
    if raw is not None:
        mode = str(raw).strip().upper()
        if mode in ('FAST', 'SLOW'):
            logger.debug(f"get_readmode: READ-MDE={mode} (from header)")
            return mode
        logger.warning(
            f"get_readmode: unrecognised READ-MDE value '{raw}', "
            f"falling through to inference"
        )

    # Priority 2: infer from rows 30-50 of image_data
    if image_data is not None:
        try:
            rows = image_data[30:50, :]
            if rows.size == 0:
                raise ValueError("rows 30-50 yielded no pixels")
            level = float(np.median(rows.astype(float)))
            mode = 'FAST' if level > _FAST_SLOW_THRESHOLD else 'SLOW'
            logger.info(
                f"get_readmode: inferred {mode} from rows 30-50 "
                f"(median={level:.1f} DN, threshold={_FAST_SLOW_THRESHOLD})"
            )
            return mode
        except Exception as exc:
            logger.warning(f"get_readmode: row-median inference failed: {exc}")

    # Priority 3: default
    logger.warning(
        "get_readmode: READ-MDE absent and no image_data; defaulting to 'SLOW'"
    )
    return 'SLOW'
