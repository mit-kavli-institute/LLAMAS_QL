"""Reduction subprocess entry point (Qt-free).

Two modes:
  * one-shot  ``python -m llamas_ql.QLook.workers.runner`` — read one JobSpec as
    JSON on stdin, run it, stream NDJSON events, exit (used by tests / fallback).
  * persistent ``python -m llamas_ql.QLook.workers.runner --serve`` — read one
    JobSpec per stdin line and serve many jobs in one process, so the heavy
    backend (Ray, pypeit, the ~4.5 GB reference arc) is initialised once. Events
    carry the job's ``job_id`` so the GUI can correlate them.

The heavy backend is imported lazily *inside* each handler so the ``dummy``
self-test and process start-up stay cheap.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback

# Headless worker: force a non-interactive matplotlib backend before any backend
# module (e.g. Arc/arcLlamasMulti) can import pyplot, so a stray ``plt.show()``
# can never block the reduction process forever.
os.environ.setdefault("MPLBACKEND", "Agg")

from llamas_ql.QLook.workers import protocol
from llamas_ql.QLook.workers.protocol import JobSpec

_START, _DONE, _SKIP = protocol.STATUS_START, protocol.STATUS_DONE, protocol.STATUS_SKIP


class _Emitter:
    """Binds the output stream to one job so every event carries its ``job_id``."""

    def __init__(self, stream, job_id: str | None = None):
        self.stream = stream
        self.job_id = job_id

    def stage(self, stage, status, detail=None):
        protocol.emit_stage(self.stream, stage, status, detail, job_id=self.job_id)

    def progress(self, pct, stage=None):
        protocol.emit_progress(self.stream, pct, stage, job_id=self.job_id)

    def log(self, level, msg, name=""):
        protocol.emit_log(self.stream, level, msg, name, job_id=self.job_id)

    def partial(self, payload):
        """Publish a non-terminal intermediate artefact (job stays open)."""
        protocol.emit_partial(self.stream, payload, job_id=self.job_id)

    def result(self, payload):
        protocol.emit_result(self.stream, payload, job_id=self.job_id)

    def error(self, msg):
        protocol.emit_error(self.stream, msg, job_id=self.job_id)


class NDJSONLogHandler(logging.Handler):
    """Re-emit ``llamas_ql`` log records as NDJSON events tagged with the live job."""

    def __init__(self, stream):
        super().__init__()
        self._stream = stream
        self.job_id: str | None = None  # set per-job by the serve loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            protocol.emit_log(self._stream, record.levelname, self.format(record),
                              record.name, job_id=self.job_id)
        except Exception:  # never let logging crash the job
            self.handleError(record)


def _install_log_bridge(out) -> NDJSONLogHandler:
    handler = NDJSONLogHandler(out)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("llamas_ql")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler


# --- shared helpers ---------------------------------------------------------


def _extract_to_rss(science, output_dir, trace_dir, em: _Emitter, *,
                    method="optimal", remove_crs=True, apply_wavelength=True,
                    edge_bias=None) -> dict:
    """Shared extract → (reference-arc) wavelength → per-channel RSS path.

    Used by the cube ``reduce`` flow (optimal) and ``extract_only`` (boxcar, no
    CR). Wavelength calibration is skipped gracefully when the reference arc is
    not deployed (products then carry a pixel-index axis). ``apply_wavelength``
    False force-skips it even when the arc is present — the fast ``quicklook``
    path uses this to avoid loading the ~4.5 GB reference arc, showing per-fibre
    spectra on a pixel-index axis first.
    """
    import os

    from llamas_ql import reduce
    from llamas_ql.config import CALIB_DIR, LUT_DIR, resolve_reference_arc
    from llamas_ql.Extract.extractLlamas import save_extractions
    from llamas_ql.File.llamasRSS import RSSgeneration

    logger = logging.getLogger("llamas_ql.QLook.reduce")

    # bias pre-flight (warn-only; in-path region fallback handles substitution)
    opts_bias = None
    try:
        from llamas_ql.QLook.workers.qa import ql_bias_guard
        _ok, warns = ql_bias_guard(science)
        for w in warns:
            em.log("WARNING", f"bias: {w}")
    except Exception as exc:
        logger.warning(f"bias guard skipped: {exc}")

    em.stage("extract", _START)
    extract_pkl = reduce.run_extraction(
        science, output_dir, trace_dir=trace_dir, mastercalib_trace_dir=CALIB_DIR,
        remove_cosmic_rays=remove_crs, method=method, edge_bias=edge_bias,
    )
    if not extract_pkl or not os.path.exists(extract_pkl):
        raise RuntimeError("extraction produced no output")
    em.stage("extract", _DONE)

    em.stage("wavelength", _START)
    arc_path = resolve_reference_arc()
    rss_input_pkl = extract_pkl
    wavelength_applied = False
    if apply_wavelength and arc_path and os.path.exists(arc_path):
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
        em.stage("wavelength", _DONE)
    elif not apply_wavelength:
        logger.info("quick look: skipping wavelength calibration for speed "
                    "(products use a pixel-index axis).")
        em.stage("wavelength", _SKIP)
    else:
        logger.warning(
            "Reference arc not found at %s; skipping wavelength calibration "
            "(products use a pixel-index axis).", arc_path)
        em.stage("wavelength", _SKIP)

    em.stage("rss", _START)
    rss_base = os.path.join(
        output_dir, os.path.splitext(os.path.basename(rss_input_pkl))[0] + "_RSS.fits")
    rss_files = RSSgeneration(logger=logger).generate_rss(rss_input_pkl, rss_base) or []
    if not rss_files and os.path.exists(rss_base):
        rss_files = [rss_base]
    rss_files = [f for f in rss_files if f and os.path.exists(f)]
    if not rss_files:
        raise RuntimeError("RSS generation produced no output files")
    em.stage("rss", _DONE)
    return {
        "science": science, "extract_pkl": extract_pkl,
        "rss_files": rss_files, "wavelength_applied": wavelength_applied,
    }


def _resolve_io(job: JobSpec):
    """Common (science, output_dir, options, trace_dir) resolution for science jobs."""
    from llamas_ql.config import OUTPUT_DIR

    if not job.inputs:
        raise ValueError(f"{job.kind} job requires a science file in inputs[0]")
    science = job.inputs[0]
    # Products go to a writable output dir; never default to the (now possibly
    # read-only, pyjamas-resolved) master calibration directory.
    output_dir = job.output_dir or OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    opts = job.options or {}
    return science, output_dir, opts, opts.get("trace_dir")


def _require_master_calibs(trace_dir=None) -> str:
    """Preflight: ensure trace calibrations are resolvable before heavy work.

    Returns the master-calibration directory. Raises a clear, actionable error
    (surfaced as an ``error`` event by :func:`_dispatch`) when neither nightly
    traces nor any master library (local packaged dir or an installed
    llamas-pyjamas) can be found — instead of failing opaquely deep inside Ray.
    """
    from llamas_ql.config import (resolve_master_calib_dir, pyjamas_pkg_dir,
                                  _LOCAL_CALIB_DIR)

    master = resolve_master_calib_dir()
    if master:
        return master
    # A non-empty nightly trace dir is itself sufficient to extract against.
    if trace_dir and os.path.isdir(trace_dir) and os.listdir(trace_dir):
        return trace_dir
    searched = [_LOCAL_CALIB_DIR]
    pj = pyjamas_pkg_dir()
    searched.append(os.path.join(pj, "mastercalib") if pj
                    else "(llamas-pyjamas not installed on sys.path)")
    raise RuntimeError(
        "No master calibrations found. Run 'Process Cals' to generate nightly "
        "traces for tonight, or deploy the master trace pickles. Searched: "
        + "; ".join(searched))


# --- job handlers -----------------------------------------------------------


def _run_dummy(job: JobSpec, em: _Emitter) -> dict:
    """Phase-0 plumbing self-test: exercise the event vocabulary without Ray."""
    stages = protocol.REDUCE_STAGES
    n = len(stages)
    logger = logging.getLogger("llamas_ql.QLook.dummy")
    for i, stage in enumerate(stages):
        em.stage(stage, _START)
        logger.info("dummy: entering stage %s", stage)
        for sub in range(2):
            time.sleep(0.25)
            em.progress(int(100 * (i + (sub + 1) / 2) / n), stage)
        em.stage(stage, _DONE)
    return {"dummy": True, "echo_inputs": job.inputs}


def _run_reduce(job: JobSpec, em: _Emitter) -> dict:
    """Science quick reduction → per-channel RSS (optimal extraction, for cubes)."""
    science, output_dir, opts, trace_dir = _resolve_io(job)
    _require_master_calibs(trace_dir)
    from llamas_ql import reduce

    em.progress(0, "validate")
    em.stage("validate", _START)
    reduce.validate_input_files([science])
    em.stage("validate", _DONE)
    em.stage("flatfield", _SKIP)

    em.progress(30, "extract")
    payload = _extract_to_rss(
        science, output_dir, trace_dir, em,
        method=opts.get("extraction_method", "optimal"),
        remove_crs=bool(opts.get("remove_cosmic_rays", True)),
        edge_bias=reduce.build_edge_bias_config(opts, output_dir),
    )
    em.progress(100, "rss")
    return payload


def _run_extract_only(job: JobSpec, em: _Emitter) -> dict:
    """Boxcar extraction → per-channel RSS (no flat-field, CR, sky, or cube)."""
    science, output_dir, opts, trace_dir = _resolve_io(job)
    _require_master_calibs(trace_dir)
    from llamas_ql import reduce

    em.progress(0, "validate")
    em.stage("validate", _START)
    reduce.validate_input_files([science])
    em.stage("validate", _DONE)

    em.progress(25, "extract")
    payload = _extract_to_rss(
        science, output_dir, trace_dir, em, method="boxcar", remove_crs=False,
        edge_bias=reduce.build_edge_bias_config(opts, output_dir))
    payload["mode"] = "extract_only"
    em.progress(100, "rss")
    return payload


def _pick_channel_rss(rss_files, channel):
    """Pick the RSS file for ``channel`` (matches ``_<channel>`` in the basename),
    falling back to the first file."""
    if not rss_files:
        return None
    if channel:
        for f in rss_files:
            if f"_{channel.lower()}" in os.path.basename(f).lower():
                return f
    return rss_files[0]


def _quick_whitelight(science, output_dir, opts, trace_dir) -> str:
    """Fast trace-based white-light image off the MEF (no optimal extraction)."""
    from llamas_ql.Image.WhiteLightModule import QuickWhiteLightCube
    from llamas_ql.QLook.workers.qa import resolve_bias

    try:
        _mode, master = resolve_bias(science, opts.get("slow_bias"), opts.get("fast_bias"))
    except Exception:  # unreadable frame/master — let QuickWhiteLight auto-select
        master = None
    out_path = opts.get("whitelight_output") or os.path.join(
        output_dir, os.path.splitext(os.path.basename(science))[0] + "_wlc.fits")
    result = QuickWhiteLightCube(science, bias=master, outfile=out_path, use_dir=trace_dir)
    out_path = result or out_path
    if not out_path or not os.path.exists(out_path):
        raise RuntimeError(f"white-light output was not created: {out_path}")
    return out_path


def _build_cube(rss, opts) -> str:
    """Build a Simple Cube from one RSS file (fast nearest_hex grid by default)."""
    from llamas_ql.Cube.simple_cube_constructor import SimpleCubeConstructor

    grid_method = opts.get("grid_method", "nearest_hex")
    out_path = opts.get("cube_output") or (os.path.splitext(rss)[0] + "_cube.fits")
    constructor = SimpleCubeConstructor(grid_method=grid_method)
    constructor.load_rss_file(rss)
    constructor.match_fibers_to_fibermap()
    constructor.create_wavelength_grid()
    constructor.create_spatial_grid()
    constructor.construct_cube()
    try:
        constructor.create_wcs()
    except Exception:
        pass
    constructor.save_cube(out_path, overwrite=True)
    return out_path


def _run_quicklook(job: JobSpec, em: _Emitter) -> dict:
    """Progressive quick look (the default Reduce action).

    Streams products as they become ready so observers get information fast:
      1. a trace-based **white-light image** (no optimal extraction) → shown ASAP;
      2. **boxcar per-fibre spectra** (CR off, pixel-index axis — the 4.5 GB arc
         is skipped for speed) → the hex fibre map becomes clickable;
      3. a **simple cube** (nearest_hex), best-effort — a slow/failed cube never
         drops the white-light or spectra already displayed.
    A single terminal ``result`` carries the full product manifest.
    """
    logger = logging.getLogger("llamas_ql.QLook.quicklook")
    science, output_dir, opts, trace_dir = _resolve_io(job)
    _require_master_calibs(trace_dir)
    from llamas_ql import reduce

    channel = opts.get("inspect_channel", "green")
    payload = {"science": science, "mode": "quicklook"}

    em.progress(0, "validate")
    em.stage("validate", _START)
    reduce.validate_input_files([science])
    em.stage("validate", _DONE)
    em.stage("flatfield", _SKIP)

    # 1) White-light image ASAP.
    em.progress(10, "whitelight")
    em.stage("whitelight", _START)
    try:
        wl_path = _quick_whitelight(science, output_dir, opts, trace_dir)
        payload["whitelight"] = wl_path
        em.partial({"kind": "whitelight", "path": wl_path, "science": science})
        em.stage("whitelight", _DONE)
    except Exception as exc:
        logger.exception("white-light step failed")
        em.log("WARNING", f"white-light image failed: {exc}")
        em.stage("whitelight", _SKIP)

    # 2) Boxcar per-fibre spectra (fast: CR off, pixel-index axis).
    em.progress(40, "extract")
    rss = _extract_to_rss(science, output_dir, trace_dir, em,
                          method="boxcar", remove_crs=False, apply_wavelength=False,
                          edge_bias=reduce.build_edge_bias_config(opts, output_dir))
    payload.update(rss)
    em.partial({"kind": "rss", "rss_files": rss["rss_files"],
                "channel": channel, "science": science})

    # 3) Simple cube (best-effort).
    rss_for_cube = _pick_channel_rss(rss["rss_files"], channel)
    if rss_for_cube:
        em.progress(80, "cube")
        em.stage("cube", _START)
        try:
            cube_path = _build_cube(rss_for_cube, opts)
            payload["cube"] = cube_path
            em.partial({"kind": "cube", "path": cube_path, "science": science})
            em.stage("cube", _DONE)
        except Exception as exc:
            logger.exception("cube step failed")
            em.log("WARNING", f"cube build failed (white-light + spectra still "
                              f"available): {exc}")
            em.stage("cube", _SKIP)

    em.progress(100, "cube")
    return payload


def _run_whitelight(job: JobSpec, em: _Emitter) -> dict:
    """Quick binary white-light image straight from the science MEF (no extraction)."""
    import os

    science, output_dir, opts, trace_dir = _resolve_io(job)
    from llamas_ql import reduce
    from llamas_ql.Image.WhiteLightModule import QuickWhiteLightCube
    from llamas_ql.QLook.workers.qa import ql_bias_guard, resolve_bias

    em.progress(0, "validate")
    em.stage("validate", _START)
    reduce.validate_input_files([science])
    em.stage("validate", _DONE)

    em.stage("bias", _START)
    try:
        _mode, master = resolve_bias(science, opts.get("slow_bias"), opts.get("fast_bias"))
    except Exception as exc:  # unreadable frame/master — let QuickWhiteLight auto-select
        em.log("WARNING", f"bias: could not resolve master ({exc}); auto-selecting.")
        master = None
    _ok, warns = ql_bias_guard(science, opts.get("slow_bias"), opts.get("fast_bias"))
    for w in warns:
        em.log("WARNING", f"bias: {w}")
    em.stage("bias", _DONE)

    em.progress(50, "whitelight")
    em.stage("whitelight", _START)
    out_path = opts.get("output") or os.path.join(
        output_dir, os.path.splitext(os.path.basename(science))[0] + "_wlc.fits")
    result = QuickWhiteLightCube(science, bias=master, outfile=out_path, use_dir=trace_dir)
    out_path = result or out_path
    if not os.path.exists(out_path):
        raise RuntimeError(f"white-light output was not created: {out_path}")
    em.stage("whitelight", _DONE)
    em.progress(100, "whitelight")
    return {"whitelight": out_path, "science": science, "bias_warnings": warns}


def _run_calib(job: JobSpec, em: _Emitter) -> dict:
    """Afternoon calibration: (optional bias) → traces+validate/fallback → lamp flats."""
    import os

    from llamas_ql import reduce
    from llamas_ql.QLook.workers import calib_pipeline

    opts = job.options or {}
    dome_flat = opts.get("dome_flat") or (job.inputs[0] if job.inputs else None)
    if not dome_flat or not os.path.exists(dome_flat):
        raise ValueError("calibration requires an existing dome-flat MEF")
    trace_dir = job.output_dir
    if not trace_dir:
        raise ValueError("calibration requires output_dir (trace directory)")
    os.makedirs(trace_dir, exist_ok=True)
    slow_bias, fast_bias = opts.get("slow_bias"), opts.get("fast_bias")
    output_dir = opts.get("flat_output_dir") or trace_dir

    em.progress(0, "validate")
    em.stage("validate", _START)
    reduce.validate_input_files([dome_flat])
    em.stage("validate", _DONE)

    # Optional: build nightly bias masters from raw bias MEFs supplied in options.
    raw_biases = opts.get("bias_files") or []
    if raw_biases:
        em.stage("bias", _START)
        masters, warns = calib_pipeline.build_bias_masters(raw_biases, output_dir)
        for w in warns:
            em.log("WARNING", f"bias: {w}")
        slow_bias = masters.get("SLOW", slow_bias)
        fast_bias = masters.get("FAST", fast_bias)
        em.stage("bias", _DONE)
    else:
        em.stage("bias", _SKIP)

    em.progress(33, "traces")
    em.stage("traces", _START)
    trace_warns = calib_pipeline.generate_and_validate_traces(
        dome_flat, trace_dir, slow_bias=slow_bias, fast_bias=fast_bias)
    for w in trace_warns:
        em.log("WARNING", f"trace fallback: {w}")
    # Detail on the stage event lets the GUI raise its persistent banner.
    em.stage("traces", _DONE,
             detail=f"{len(trace_warns)} camera(s) on default masters" if trace_warns else None)

    em.progress(75, "flatfield")
    em.stage("flatfield", _START)
    flat_map, flat_warns = calib_pipeline.extract_lamp_flats(
        dome_flat, output_dir, trace_dir, slow_bias=slow_bias, fast_bias=fast_bias)
    for w in flat_warns:
        em.log("WARNING", f"flat: {w}")
    em.stage("flatfield", _DONE if flat_map else _SKIP)

    em.progress(100, "flatfield")
    return {
        "trace_dir": trace_dir, "dome_flat": dome_flat, "lamp_flat": flat_map,
        "trace_fallback_cameras": trace_warns,
    }


def _run_cube(job: JobSpec, em: _Emitter) -> dict:
    """Build a Simple Cube from an RSS file (default fast nearest_hex grid)."""
    import os

    from llamas_ql.Cube.simple_cube_constructor import SimpleCubeConstructor

    if not job.inputs:
        raise ValueError("cube job requires an RSS file in inputs[0]")
    rss = job.inputs[0]
    if not os.path.exists(rss):
        raise FileNotFoundError(f"RSS file not found: {rss}")
    opts = job.options or {}
    grid_method = opts.get("grid_method", "nearest_hex")  # fast default for live use
    out_path = opts.get("output") or os.path.splitext(rss)[0] + "_cube.fits"

    em.progress(10, "cube")
    em.stage("cube", _START)
    constructor = SimpleCubeConstructor(grid_method=grid_method)
    constructor.load_rss_file(rss)
    constructor.match_fibers_to_fibermap()
    constructor.create_wavelength_grid()
    constructor.create_spatial_grid()
    em.progress(40, "cube")
    constructor.construct_cube()
    try:
        constructor.create_wcs()
    except Exception:
        pass
    em.progress(80, "cube")
    constructor.save_cube(out_path, overwrite=True)
    em.stage("cube", _DONE)
    em.progress(100, "cube")
    return {"cube": out_path, "rss": rss}


_HANDLERS = {
    "dummy": _run_dummy,
    "quicklook": _run_quicklook,
    "reduce": _run_reduce,
    "extract_only": _run_extract_only,
    "whitelight": _run_whitelight,
    "calib": _run_calib,
    "cube": _run_cube,
}


def _dispatch(job: JobSpec, em: _Emitter) -> int:
    """Run one job, emitting its result or error. Returns a process-style code."""
    handler = _HANDLERS.get(job.kind)
    if handler is None:
        em.error(f"unknown job kind: {job.kind!r}")
        return 2
    try:
        em.result(handler(job, em))
        return 0
    except NotImplementedError as exc:
        em.error(str(exc))
        return 3
    except Exception:
        em.error(traceback.format_exc())
        return 1


def _run_once(raw: str, out, log_handler) -> int:
    try:
        job = JobSpec.from_json(raw)
    except Exception as exc:
        protocol.emit_error(out, f"invalid job spec: {exc}")
        return 2
    log_handler.job_id = job.job_id
    return _dispatch(job, _Emitter(out, job.job_id))


def _serve(out, log_handler) -> int:
    """Persistent loop: one JobSpec per stdin line; heavy deps load once."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        _run_once(line, out, log_handler)
        log_handler.job_id = None
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LLAMAS Quick Look reduction worker")
    parser.add_argument("--serve", action="store_true",
                        help="persistent mode: serve many JobSpecs (one per stdin line)")
    args = parser.parse_args(argv)

    out = sys.stdout
    handler = _install_log_bridge(out)
    if args.serve:
        return _serve(out, handler)
    return _run_once(sys.stdin.read(), out, handler)


if __name__ == "__main__":
    raise SystemExit(main())
