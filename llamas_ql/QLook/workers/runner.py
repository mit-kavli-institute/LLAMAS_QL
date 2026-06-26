"""Reduction subprocess entry point (Qt-free).

Run as ``python -m llamas_ql.QLook.workers.runner``.  Reads a :class:`JobSpec`
as JSON on stdin, performs the requested backend work by calling into
``llamas_ql.reduce`` / cube / white-light routines, and streams NDJSON events
back on stdout (see :mod:`llamas_ql.QLook.workers.protocol`).

Running in a child process is deliberate: the reduction backend uses Ray (which
installs its own signal handlers and worker processes) and pypeit, loads a
~4.5 GB reference arc, and occasionally aborts in C extensions.  Isolating it
keeps the long-lived GUI responsive, crash-proof, and memory-lean.

This module imports the heavy backend lazily *inside* each handler so that the
Phase-0 ``dummy`` self-test runs without paying that import cost.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from llamas_ql.QLook.workers import protocol
from llamas_ql.QLook.workers.protocol import JobSpec


class NDJSONLogHandler(logging.Handler):
    """Logging handler that re-emits ``llamas_ql`` log records as NDJSON events.

    Attached to the ``llamas_ql`` parent logger so every child logger's records
    propagate here and reach the GUI's live log dock as structured events.
    """

    def __init__(self, stream):
        super().__init__()
        self._stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            protocol.emit_log(
                self._stream,
                level=record.levelname,
                msg=self.format(record),
                name=record.name,
            )
        except Exception:  # never let logging crash the job
            self.handleError(record)


def _install_log_bridge(out) -> None:
    """Route the ``llamas_ql`` logger into the NDJSON stream for the GUI."""
    handler = NDJSONLogHandler(out)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("llamas_ql")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# --- job handlers -----------------------------------------------------------


def _run_dummy(job: JobSpec, out) -> dict:
    """Phase-0 plumbing self-test: exercise the full event vocabulary.

    Emits each reduce stage with start/done plus interpolated progress and a few
    log lines, then returns a synthetic result.  Lets us verify the GUI log
    dock, progress bar and cancellation before any Ray-touching code exists.
    """
    stages = protocol.REDUCE_STAGES
    n = len(stages)
    logger = logging.getLogger("llamas_ql.QLook.dummy")
    for i, stage in enumerate(stages):
        protocol.emit_stage(out, stage, protocol.STATUS_START)
        logger.info("dummy: entering stage %s", stage)
        # Two sub-steps per stage so progress visibly advances.
        for sub in range(2):
            time.sleep(0.25)
            pct = int(100 * (i + (sub + 1) / 2) / n)
            protocol.emit_progress(out, pct, stage)
        protocol.emit_stage(out, stage, protocol.STATUS_DONE)
    return {"dummy": True, "echo_inputs": job.inputs}


def _stage_pct(out, stages, index: int) -> None:
    """Emit coarse progress at the boundary *before* entering stage ``index``."""
    protocol.emit_progress(out, int(100 * index / len(stages)), stages[index])


def _run_reduce(job: JobSpec, out) -> dict:
    """Science quick reduction: extract -> wavelength -> RSS (-> cube/white-light in Phase 3).

    Produces, per spectrograph channel, an RSS FITS file the GUI can inspect.
    Wavelength calibration is skipped gracefully when the (large) reference arc
    is not deployed; downstream products then use a pixel-index axis.
    """
    import os

    from llamas_ql import reduce
    from llamas_ql.config import CALIB_DIR, LUT_DIR
    from llamas_ql.Extract.extractLlamas import save_extractions
    from llamas_ql.File.llamasRSS import RSSgeneration

    if not job.inputs:
        raise ValueError("reduce job requires a science file in inputs[0]")
    science = job.inputs[0]
    output_dir = job.output_dir or CALIB_DIR
    os.makedirs(output_dir, exist_ok=True)
    opts = job.options or {}
    trace_dir = opts.get("trace_dir")  # None -> run_extraction falls back to mastercalib
    remove_crs = bool(opts.get("remove_cosmic_rays", True))

    stages = protocol.REDUCE_STAGES
    logger = logging.getLogger("llamas_ql.QLook.reduce")

    # 1. validate ------------------------------------------------------------
    _stage_pct(out, stages, 0)
    protocol.emit_stage(out, "validate", protocol.STATUS_START)
    reduce.validate_input_files([science])
    protocol.emit_stage(out, "validate", protocol.STATUS_DONE)

    # (flatfield stage reserved for Phase 2 wiring; skipped in the MVP path)
    protocol.emit_stage(out, "flatfield", protocol.STATUS_SKIP)

    # 2. extract -------------------------------------------------------------
    _stage_pct(out, stages, 2)
    protocol.emit_stage(out, "extract", protocol.STATUS_START)
    extract_pkl = reduce.run_extraction(
        science, output_dir,
        trace_dir=trace_dir, mastercalib_trace_dir=CALIB_DIR,
        remove_cosmic_rays=remove_crs,
    )
    if not extract_pkl or not os.path.exists(extract_pkl):
        raise RuntimeError("extraction produced no output")
    protocol.emit_stage(out, "extract", protocol.STATUS_DONE)

    # 3. wavelength (optional) ----------------------------------------------
    _stage_pct(out, stages, 3)
    arc_path = os.path.join(LUT_DIR, "LLAMAS_reference_arc.pkl")
    rss_input_pkl = extract_pkl
    wavelength_applied = False
    if os.path.exists(arc_path):
        protocol.emit_stage(out, "wavelength", protocol.STATUS_START)
        corr_extr, primary_hdr = reduce.correct_wavelengths(extract_pkl)
        base = os.path.splitext(os.path.basename(extract_pkl))[0]
        corr_pkl = os.path.join(output_dir, f"{base}_corrected_extractions.pkl")
        save_extractions(
            corr_extr["extractions"], primary_header=primary_hdr,
            savefile=corr_pkl, save_dir=output_dir,
            prefix="LLAMASExtract_sci_corrected",
        )
        rss_input_pkl = corr_pkl
        wavelength_applied = True
        protocol.emit_stage(out, "wavelength", protocol.STATUS_DONE)
    else:
        logger.warning(
            "Reference arc not found at %s; skipping wavelength calibration "
            "(products use a pixel-index axis).", arc_path)
        protocol.emit_stage(out, "wavelength", protocol.STATUS_SKIP)

    # 4. RSS (one file per channel) -----------------------------------------
    _stage_pct(out, stages, 4)
    protocol.emit_stage(out, "rss", protocol.STATUS_START)
    rss_base = os.path.join(
        output_dir,
        os.path.splitext(os.path.basename(rss_input_pkl))[0] + "_RSS.fits",
    )
    rss_gen = RSSgeneration(logger=logger)
    rss_files = rss_gen.generate_rss(rss_input_pkl, rss_base) or []
    # generate_rss only records per-channel names when >1 channel; otherwise the
    # single combined file is written to rss_base.  Cover both cases.
    if not rss_files and os.path.exists(rss_base):
        rss_files = [rss_base]
    rss_files = [f for f in rss_files if f and os.path.exists(f)]
    if not rss_files:
        raise RuntimeError("RSS generation produced no output files")
    protocol.emit_stage(out, "rss", protocol.STATUS_DONE)

    protocol.emit_progress(out, 100, "rss")
    return {
        "science": science,
        "extract_pkl": extract_pkl,
        "rss_files": rss_files,
        "wavelength_applied": wavelength_applied,
    }


def _run_calib(job: JobSpec, out) -> dict:
    """Afternoon calibration: generate fibre traces from the dome-flat MEF.

    A LLAMAS dome-flat MEF carries all three channels, so the same file feeds the
    red/green/blue trace generation.  Producing traces in ``trace_dir`` is what
    makes the Quick Look "ready" for science extraction.
    """
    import os

    from llamas_ql import reduce

    opts = job.options or {}
    dome_flat = opts.get("dome_flat") or (job.inputs[0] if job.inputs else None)
    if not dome_flat or not os.path.exists(dome_flat):
        raise ValueError("calibration requires an existing dome-flat MEF")
    trace_dir = job.output_dir
    if not trace_dir:
        raise ValueError("calibration requires output_dir (trace directory)")
    os.makedirs(trace_dir, exist_ok=True)

    stages = protocol.CALIB_STAGES

    protocol.emit_progress(out, 0, "validate")
    protocol.emit_stage(out, "validate", protocol.STATUS_START)
    reduce.validate_input_files([dome_flat])
    protocol.emit_stage(out, "validate", protocol.STATUS_DONE)

    protocol.emit_progress(out, int(100 / len(stages)), "traces")
    protocol.emit_stage(out, "traces", protocol.STATUS_START)
    # One MEF holds all channels; pass it as red/green/blue source.
    reduce.generate_traces(dome_flat, dome_flat, dome_flat, trace_dir)
    protocol.emit_stage(out, "traces", protocol.STATUS_DONE)

    # Pixel/fibre flat-field generation is an optional future stage.
    protocol.emit_stage(out, "flatfield", protocol.STATUS_SKIP)

    protocol.emit_progress(out, 100, "traces")
    return {"trace_dir": trace_dir, "dome_flat": dome_flat}


def _run_cube(job: JobSpec, out) -> dict:
    """Build a Simple Cube from an RSS file and save it as a cube FITS.

    Ray-free and quick; still run in the subprocess to keep the GUI responsive.
    """
    import os

    from llamas_ql.Cube.simple_cube_constructor import SimpleCubeConstructor

    if not job.inputs:
        raise ValueError("cube job requires an RSS file in inputs[0]")
    rss = job.inputs[0]
    if not os.path.exists(rss):
        raise FileNotFoundError(f"RSS file not found: {rss}")
    opts = job.options or {}
    grid_method = opts.get("grid_method", "oversampled")
    out_path = opts.get("output") or os.path.splitext(rss)[0] + "_cube.fits"

    protocol.emit_progress(out, 10, "cube")
    protocol.emit_stage(out, "cube", protocol.STATUS_START)
    constructor = SimpleCubeConstructor(grid_method=grid_method)
    constructor.load_rss_file(rss)
    # Prepare geometry + grids before constructing (required ordering).
    constructor.match_fibers_to_fibermap()
    constructor.create_wavelength_grid()
    constructor.create_spatial_grid()
    protocol.emit_progress(out, 40, "cube")
    constructor.construct_cube()
    try:
        constructor.create_wcs()  # populate spatial WCS; harmless if pointing is (0,0)
    except Exception:
        pass
    protocol.emit_progress(out, 80, "cube")
    constructor.save_cube(out_path, overwrite=True)
    protocol.emit_stage(out, "cube", protocol.STATUS_DONE)
    protocol.emit_progress(out, 100, "cube")
    return {"cube": out_path, "rss": rss}


_HANDLERS = {
    "dummy": _run_dummy,
    "reduce": _run_reduce,
    "calib": _run_calib,
    "cube": _run_cube,
}


def main(argv=None) -> int:
    out = sys.stdout
    _install_log_bridge(out)
    try:
        raw = sys.stdin.read()
        job = JobSpec.from_json(raw)
    except Exception as exc:  # malformed spec
        protocol.emit_error(out, f"invalid job spec: {exc}")
        return 2

    handler = _HANDLERS.get(job.kind)
    if handler is None:
        protocol.emit_error(out, f"unknown job kind: {job.kind!r}")
        return 2

    try:
        payload = handler(job, out)
        protocol.emit_result(out, payload)
        return 0
    except NotImplementedError as exc:
        protocol.emit_error(out, str(exc))
        return 3
    except Exception:
        protocol.emit_error(out, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
