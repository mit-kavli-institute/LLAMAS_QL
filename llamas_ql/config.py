import os
import sys
import glob
from functools import lru_cache
from importlib.machinery import PathFinder

# Define the root directory for outputs
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Docs/DATA")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LUT_DIR = os.path.join(BASE_DIR, "LUT")

# Local (packaged) master-calibration locations. In a dev checkout these are
# often empty (the large master products are git-ignored and deployed
# separately). The resolvers below fall back to an installed ``llamas-pyjamas``
# library, which ships the master traces, reference arc and master biases.
_LOCAL_CALIB_DIR = os.path.join(BASE_DIR, "mastercalib")
_LOCAL_BIAS_DIR = os.path.join(BASE_DIR, "Bias")


@lru_cache(maxsize=1)
def pyjamas_pkg_dir():
    """Directory of the installed ``llamas_pyjamas`` package, or ``None``.

    Resolution uses :class:`importlib.machinery.PathFinder` rather than
    :func:`importlib.util.find_spec` on purpose: ``llamas_ql/__init__`` aliases
    ``sys.modules['llamas_pyjamas'] = llamas_ql`` for pickle compatibility, which
    would make the ordinary finder report *this* package's directory. PathFinder
    searches ``sys.path`` on disk and ignores that alias, so it finds the real
    llamas-pyjamas install (typically an editable install on ``sys.path``).
    """
    try:
        spec = PathFinder.find_spec("llamas_pyjamas", sys.path)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    origin = getattr(spec, "origin", None)
    if origin and origin not in ("namespace", "built-in"):
        return os.path.dirname(origin)
    # Namespace package: use the first search location if present.
    locs = list(getattr(spec, "submodule_search_locations", None) or [])
    return locs[0] if locs else None


def _pyjamas_subdir(name):
    root = pyjamas_pkg_dir()
    return os.path.join(root, name) if root else None


def _dir_has_pkl(d):
    return bool(d) and os.path.isdir(d) and bool(glob.glob(os.path.join(d, "*.pkl")))


def _dir_has_master_bias(d):
    return bool(d) and (os.path.isfile(os.path.join(d, "slow_master_bias.fits"))
                        or os.path.isfile(os.path.join(d, "fast_master_bias.fits")))


def resolve_master_calib_dir():
    """Master fibre-trace directory to read from.

    Precedence: local packaged ``mastercalib`` (if it holds ``*.pkl``) →ﾠthe
    installed llamas-pyjamas ``mastercalib`` → ``None`` when neither exists.
    This is a *read-only* source; nightly traces are written elsewhere.
    """
    if _dir_has_pkl(_LOCAL_CALIB_DIR):
        return _LOCAL_CALIB_DIR
    pj = _pyjamas_subdir("mastercalib")
    if _dir_has_pkl(pj):
        return pj
    return None


def resolve_bias_dir():
    """Master-bias directory: local if it has a master bias FITS, else pyjamas."""
    if _dir_has_master_bias(_LOCAL_BIAS_DIR):
        return _LOCAL_BIAS_DIR
    pj = _pyjamas_subdir("Bias")
    if _dir_has_master_bias(pj):
        return pj
    return _LOCAL_BIAS_DIR


def resolve_reference_arc():
    """Path to ``LLAMAS_reference_arc.pkl`` (local LUT → pyjamas LUT), or ``None``."""
    local = os.path.join(LUT_DIR, "LLAMAS_reference_arc.pkl")
    if os.path.isfile(local):
        return local
    pj = _pyjamas_subdir("LUT")
    if pj:
        cand = os.path.join(pj, "LLAMAS_reference_arc.pkl")
        if os.path.isfile(cand):
            return cand
    return None


def master_calib_dirs_readonly():
    """All directories that must be treated as read-only master libraries.

    Used by the trace-writing guards so nightly generation can never overwrite a
    master trace pickle (in either the local packaged dir or the pyjamas install).
    """
    dirs = [os.path.abspath(_LOCAL_CALIB_DIR)]
    pj = _pyjamas_subdir("mastercalib")
    if pj:
        dirs.append(os.path.abspath(pj))
    return tuple(dict.fromkeys(dirs))


def guard_not_master_dir(target_dir):
    """Raise if ``target_dir`` is a read-only master-calibration library.

    Nightly trace generation must write to a nightly output directory (e.g.
    ``OUTPUT_DIR/traces``); this guard makes an accidental write into the master
    library — the local packaged ``mastercalib`` or the shared llamas-pyjamas
    install — a loud, immediate error instead of a silent overwrite.
    """
    abstarget = os.path.abspath(target_dir)
    if abstarget in master_calib_dirs_readonly():
        raise ValueError(
            f"Refusing to write trace products into the read-only master "
            f"calibration library: {abstarget}. Write nightly traces to a "
            f"nightly output directory (e.g. {os.path.join(OUTPUT_DIR, 'traces')})."
        )


# Resolved master-calibration locations used across the pipeline. Fall back to
# the local packaged dir when nothing resolves so path joins never break; the
# up-front preflight in the reduction runner turns a genuinely-missing library
# into a clear, early error rather than a late/opaque failure.
CALIB_DIR = resolve_master_calib_dir() or _LOCAL_CALIB_DIR
BIAS_DIR = resolve_bias_dir()
SLOW_BIAS_FILE = os.path.join(BIAS_DIR, 'slow_master_bias.fits')
FAST_BIAS_FILE = os.path.join(BIAS_DIR, 'fast_master_bias.fits')

# Ensure the output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)
