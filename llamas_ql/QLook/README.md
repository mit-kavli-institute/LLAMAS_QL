# LLAMAS Quick Look GUI

A PySide6 desktop front-end for the LLAMAS Quick Look reduction engine. It wraps
the existing `llamas_ql` backend (called as-is) so observers can, in one window:

1. **Process Calibrations** – when the afternoon cals are taken, point at the
   calib directory; frames are auto-classified from their FITS headers (confirm
   or override each role), then fibre traces are generated so the QL is "ready".
2. **Select + Refresh** the night's science directory as new frames are written.
3. **Quick Reduce** a selected exposure (extract → wavelength → RSS per channel).
4. **Inspect** interactively: click a fibre on the white-light map to plot its
   spectrum (Flux/Counts/Sky), build a Simple Cube and scrub it by wavelength,
   click a spaxel or drag a region for its spectrum, and pin spectra to compare.

## Install & launch

The GUI deps are an optional extra (the headless pipeline stays Qt-free):

```bash
pip install -e '.[gui]'        # or: poetry install -E gui
llamas-ql-gui                  # or: python -m llamas_ql.QLook.app
```

### conda + Qt note

If you run inside a conda environment that ships its own Qt6 (e.g. `lib/qt6`),
its plugins can shadow PySide6's and you'd see *"Could not find the Qt platform
plugin"*. The app self-heals by pinning `QT_PLUGIN_PATH` to PySide6's bundled
plugins before creating the `QApplication` (see `app._ensure_qt_plugin_path`), so
no manual env tweaking is needed.

## Architecture (where things live)

- `app.py` / `main_window.py` – bootstrap, docks/toolbar/menus, wiring.
- `core/` – `Session` (single source of truth + signals), `NightPaths`, `QSettings`.
- `workers/` – reductions run in a **subprocess** (`runner.py`) over an NDJSON
  protocol (`protocol.py`), wrapped by `ReductionProcess` (`reduction_process.py`).
  This isolates Ray/pypeit (signal handlers, the ~4.5 GB arc, possible C-aborts)
  from the long-lived GUI and streams live logs/progress; Cancel reaps the child.
- `controllers/` – `reduce` / `calib` / `cube` controllers + the header-based
  `calib_detect` classifier.
- `models/` – `RSSData`, `CubeData`, `FiberGeometry`, exposure scanner (Qt-free
  where possible; they reuse `WhiteLightModule.FiberMap_LUT`, `SimpleCubeConstructor`).
- `widgets/` – pyqtgraph views: white-light scatter, cube viewer, spectrum plot,
  log dock, exposure browser, calib panel.
- `plugins/` – `QLToolPlugin` ABC + `ToolContext`; discovery merges built-ins
  with the `llamas_ql.qlook_plugins` entry-point group.

## Adding a tool (e.g. PyAPS)

Ship a separate package exposing a `QLToolPlugin` and declare an entry point:

```toml
[project.entry-points."llamas_ql.qlook_plugins"]
pyaps = "pyaps_qlook:PyAPSPlugin"
```

Its `create_widget(ctx)` receives a `ToolContext` with the live `Session`
(current RSS/cube/selection + signals), `paths`, the main window, and `run_job`
to launch backend work through the same subprocess/IPC path. A failing plugin is
logged and skipped — it never takes down the GUI. See
`plugins/builtin/header_inspector.py` for a minimal reference.

## Status / scope

Implemented and tested: the workflow spine, exposure browser, reduce/calib/cube
orchestration, the inspection MVP (fibre→spectrum, cube viewer, pin/overlay),
and the plugin system. Real extraction/trace/wavelength runs require LLAMAS raw
frames + traces (+ the reference arc for wavelengths); when the arc is absent the
pipeline degrades gracefully to a pixel-index axis. Remote viewing is left as a
future hook (run locally at the telescope for now).
