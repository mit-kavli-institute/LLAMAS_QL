# LLAMAS_QL

Quick Look (QL) reduction engine for the Magellan **LLAMAS** Integral Field
Spectrograph. This repo hosts a new, from-scratch QL GUI plus the reduction code it
drives. QL runs *fast* nightly reduction on observer data and afternoon calibrations —
it is **not** the full `llamas-pyjamas` pipeline.

The reduction modules in `llamas_ql/` are copied from `llamas-pyjamas` (package renamed
`llamas_pyjamas` → `llamas_ql`). The frontend GUI is built separately on top of them.

## Reduction chain in scope

bias check/subtract → fiber tracing → GUI-style optimal extraction → flat fielding
(generation + application) → reference-arc wavelength application → fiber map → simple
(MUSE-like) cube + white-light image.

Out of scope (kept only in `llamas-pyjamas`): sky subtraction, flux calibration, the
ThAr arc *solve* (`arcSolve`) workflow, and the traditional/CRR cube constructors.

## Package layout (`llamas_ql/`)

- `Bias/` — master bias + bias-quality checks (inter-fibre residuals); fallback bias.
- `Trace/` — fiber tracing (`TraceLlamas`, Ray-parallel `TraceRay`).
- `GUI/guiExtract.py` — headless extraction engine (`GUI_extract()`), called by the GUI.
- `Extract/` — optimal 1D extraction (`ExtractLlamas`) with variance propagation.
- `Flat/` — pixel-to-pixel + fibre-to-fibre flats (lamp + twilight); generation & apply.
- `Arc/` — `arcTransfer` only: applies the pre-solved reference arc as the wavelength soln.
- `Image/WhiteLightModule.py` — `FiberMap_LUT` (fiber→IFU position) + white-light images.
- `Cube/simple_cube_constructor.py` — `SimpleCubeConstructor` (MUSE-like cube).
- `File/`, `DataModel/`, `Utils/`, `Masking/`, `QA/` (just `plot_ds9`) — shared infra.
- `reduce.py` — trimmed orchestration layer (trace gen, extraction, wavelength apply,
  flat gen/apply). No `main()`/sky/full-cube.

## Wavelengths

QL applies the **pre-solved reference arc** via `reduce.correct_wavelengths()` →
`Arc.arcTransfer` (loads `LUT/LLAMAS_reference_arc.pkl`). If the reference arc is absent,
RSS generation and the cube degrade gracefully to a **pixel-index** spectral axis.

## Required calibration assets (deployed, not committed)

These are git-ignored and "follow" the install to the observing machine, at the same
**package-relative** paths `config.py` expects:

- `llamas_ql/Bias/slow_master_bias.fits`, `fast_master_bias.fits`
- `llamas_ql/mastercalib/LLAMAS_master_<color>_<bench>_<side>_traces.pkl` (×24)
- `llamas_ql/LUT/LLAMAS_reference_arc.pkl` (~4.5 GB; for wavelength application)

**Automatic fallback to `llamas-pyjamas`.** When these local copies are absent (e.g. a
dev checkout), `config.py` resolves the master traces, master biases and the reference
arc from an **installed `llamas_pyjamas`** package instead (located on `sys.path` via
`importlib.machinery.PathFinder`, so the pickle-compat alias in `__init__.py` doesn't
interfere). Precedence for science reduction is: freshly-processed **nightly** cals →
local packaged masters → `llamas-pyjamas` masters. The master libraries are treated as
**read-only**: nightly trace generation writes to `output/traces` and a guard
(`config.guard_not_master_dir`) refuses any write into a master directory, so masters are
never overwritten.

Small text LUTs (`LLAMAS_FiberMap_rev04.dat`, `traceLUT.json`, `*_peaks.csv`,
`ThAr_MagE_lines.dat`) are committed. If the master bias is missing, the bias stage
falls back to an estimated constant bias (`generate_fallback_bias_hdu`).

## Quick Look GUI (`llamas-ql-gui`)

The default **Reduce** action is a fast, *progressive* quick look (`quicklook` job):
a white-light image is shown first, then per-fibre boxcar spectra on a clickable hex
fibre map (pixel-index axis — the 4.5 GB arc is skipped for speed), then a simple cube
if it finishes in time. The older `whitelight` / `cube` / `extract` modes remain in the
"Reduce As" selector. Cosmic-ray removal is off by default in quick look.

The reduction subprocess keeps one warm Ray session across jobs (no per-frame re-init),
and a per-job watchdog surfaces a hung job as a failure instead of blocking forever —
tune it with `LLAMAS_QL_JOB_TIMEOUT` (seconds, default 600).

> Note on trace pickles: master traces created by the original `llamas_pyjamas` package
> load via a compatibility shim in `llamas_ql/__init__.py`. Preferably regenerate traces
> with QL (`reduce.generate_traces`) so pickles reference `llamas_ql` natively.

## Install

```
pip install -e .
```

Python 3.11–3.12. Heavy deps (`pypeit`, `ray`, `astropy`, `scipy`, `lacosmic`) are
required by tracing/extraction/arc/flat.
