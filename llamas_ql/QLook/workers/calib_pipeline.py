"""Qt-free calibration orchestration used by the runner's ``calib`` handler.

Wraps the existing reduction science into the Quick Look afternoon-cal flow:
read-mode-matched bias masters (optional), cross-correlating trace generation
with master-fallback validation (req #4), and lamp flat-field maps (req #3).
Each function returns a list of human warnings the runner emits to the GUI; the
heavy stage/progress emission stays in the runner.
"""

from __future__ import annotations

import os

from llamas_ql.config import CALIB_DIR, LUT_DIR


def generate_and_validate_traces(dome_flat: str, trace_dir: str,
                                 slow_bias: str | None = None,
                                 fast_bias: str | None = None) -> list[str]:
    """Generate nightly traces, then validate fibre counts and fall back to masters.

    A LLAMAS dome-flat MEF carries all channels, so the same file feeds red/green/
    blue. Returns warnings naming cameras that reverted to default master traces or
    were dropped because no master was available.
    """
    from llamas_ql import reduce
    from llamas_ql.Utils.utils import validate_and_fix_trace_fibres

    reduce.generate_traces(dome_flat, dome_flat, dome_flat, trace_dir,
                           slow_bias=slow_bias, fast_bias=fast_bias)
    report = validate_and_fix_trace_fibres(trace_dir, CALIB_DIR)

    warnings: list[str] = []
    fell_back = set()
    for channel, bench, side, master_path, _copied in report.get("fallback_used", []):
        fell_back.add((channel, bench, side))
        warnings.append(
            f"{bench}{side} {channel}: trace fibre-count invalid — reverted to "
            f"DEFAULT master ({os.path.basename(master_path)})."
        )
    for channel, bench, side, expected, actual in report.get("invalid_traces", []):
        if (channel, bench, side) not in fell_back:
            warnings.append(
                f"{bench}{side} {channel}: {actual}/{expected} fibres and no master "
                f"available — camera dropped."
            )
    return warnings


def extract_lamp_flats(dome_flat: str, output_dir: str, trace_dir: str,
                       slow_bias: str | None = None,
                       fast_bias: str | None = None) -> tuple[str | None, list[str]]:
    """Produce lamp (dome) flat-field pixel maps; returns ``(map_path, warnings)``.

    Requires the reference arc (the pixel-flat smoothing runs in wavelength space);
    skips gracefully when it is absent so trace generation alone still readies the
    night.
    """
    warnings: list[str] = []
    arc = os.path.join(LUT_DIR, "LLAMAS_reference_arc.pkl")
    if not os.path.exists(arc):
        warnings.append(
            "Reference arc absent — skipping lamp flat-field map (traces still produced)."
        )
        return None, warnings

    from llamas_ql.Flat.flatLlamas import process_pixel_flat_simple
    from llamas_ql.QLook.workers.qa import resolve_bias

    _, master = resolve_bias(dome_flat, slow_bias, fast_bias)
    try:
        result = process_pixel_flat_simple(
            dome_flat, dome_flat, dome_flat,
            use_bias=master, output_dir=output_dir, trace_dir=trace_dir,
        )
        return result, warnings
    except Exception as exc:  # flats are non-critical for quick look — warn, continue
        warnings.append(f"Lamp flat-field generation failed ({exc}); proceeding without pixel flats.")
        return None, warnings


def build_bias_masters(bias_files: list[str], output_dir: str) -> tuple[dict, list[str]]:
    """Optionally build per-read-mode master biases from raw bias MEFs.

    Groups inputs by ``READ-MDE`` and median-stacks each group via ``BiasLlamas``.
    Returns ``({'SLOW': path, 'FAST': path}, warnings)``. Best-effort: observers
    normally deploy pre-built masters, so failures only warn.
    """
    from astropy.io import fits

    from llamas_ql.Bias.llamasBias import BiasLlamas
    from llamas_ql.Utils.readmode import get_readmode

    os.makedirs(output_dir, exist_ok=True)
    groups: dict[str, list[str]] = {}
    warnings: list[str] = []
    for f in bias_files:
        try:
            with fits.open(f, memmap=True) as hdul:
                mode = get_readmode(hdul[0].header)
        except Exception as exc:
            warnings.append(f"Skipping bias {os.path.basename(f)} ({exc}).")
            continue
        groups.setdefault(mode, []).append(f)

    masters: dict[str, str] = {}
    for mode, files in groups.items():
        try:
            BiasLlamas(files).master_bias()  # writes combined_bias.fits beside inputs
            src = os.path.join(os.path.dirname(files[0]), "combined_bias.fits")
            dst = os.path.join(output_dir, f"{mode.lower()}_master_bias.fits")
            if os.path.exists(src):
                os.replace(src, dst)
                masters[mode] = dst
        except Exception as exc:
            warnings.append(f"Master bias build failed for {mode} ({exc}).")
    return masters, warnings
