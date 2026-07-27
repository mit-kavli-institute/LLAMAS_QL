"""The quicklook job streams white-light → RSS → cube partials, then one result.

The heavy steps (white-light, extraction, cube) are mocked so the test exercises
only the runner's event ordering and the fast-path options (boxcar, CR off,
wavelength skipped).
"""
import io
import json

import llamas_ql.QLook.workers.runner as runner
from llamas_ql.QLook.workers.protocol import JobSpec


def test_quicklook_streams_partials_then_result(tmp_path, monkeypatch):
    import llamas_ql.reduce as reduce_mod

    monkeypatch.setattr(runner, "_require_master_calibs", lambda trace_dir=None: "cal")
    monkeypatch.setattr(reduce_mod, "validate_input_files", lambda files: None)
    monkeypatch.setattr(runner, "_quick_whitelight",
                        lambda *a, **k: str(tmp_path / "wl.fits"))

    captured = {}

    def fake_extract(science, output_dir, trace_dir, em, **kw):
        captured.update(kw)  # record the fast-path options
        return {"science": science, "extract_pkl": "e.pkl",
                "rss_files": [str(tmp_path / "x_green_RSS.fits")],
                "wavelength_applied": False}

    monkeypatch.setattr(runner, "_extract_to_rss", fake_extract)
    monkeypatch.setattr(runner, "_build_cube", lambda rss, opts: str(tmp_path / "c.fits"))

    spec = JobSpec(kind="quicklook", inputs=[str(tmp_path / "sci.fits")],
                   output_dir=str(tmp_path / "out"), job_id="job-1")
    out = io.StringIO()
    code = runner._dispatch(spec, runner._Emitter(out, "job-1"))

    assert code == 0
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    types = [e["type"] for e in events]
    partial_kinds = [e["payload"]["kind"] for e in events if e["type"] == "partial"]

    # fast-path options were used
    assert captured.get("method") == "boxcar"
    assert captured.get("remove_crs") is False
    assert captured.get("apply_wavelength") is False

    # progressive order: whitelight, then rss, then cube
    assert partial_kinds == ["whitelight", "rss", "cube"]

    # exactly one terminal result, after every partial
    assert types.count("result") == 1
    last_partial = max(i for i, e in enumerate(events) if e["type"] == "partial")
    assert types.index("result") > last_partial

    result = next(e for e in events if e["type"] == "result")["payload"]
    assert result["whitelight"].endswith("wl.fits")
    assert result["cube"].endswith("c.fits")
    assert result["rss_files"]


def test_quicklook_cube_failure_still_returns_whitelight_and_rss(tmp_path, monkeypatch):
    import llamas_ql.reduce as reduce_mod

    monkeypatch.setattr(runner, "_require_master_calibs", lambda trace_dir=None: "cal")
    monkeypatch.setattr(reduce_mod, "validate_input_files", lambda files: None)
    monkeypatch.setattr(runner, "_quick_whitelight",
                        lambda *a, **k: str(tmp_path / "wl.fits"))
    monkeypatch.setattr(runner, "_extract_to_rss",
                        lambda *a, **k: {"science": "s", "extract_pkl": "e",
                                         "rss_files": [str(tmp_path / "g_RSS.fits")],
                                         "wavelength_applied": False})

    def boom(rss, opts):
        raise RuntimeError("cube too slow / failed")

    monkeypatch.setattr(runner, "_build_cube", boom)

    spec = JobSpec(kind="quicklook", inputs=[str(tmp_path / "sci.fits")],
                   output_dir=str(tmp_path / "out"), job_id="job-2")
    out = io.StringIO()
    code = runner._dispatch(spec, runner._Emitter(out, "job-2"))

    assert code == 0  # job still succeeds
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    partial_kinds = [e["payload"]["kind"] for e in events if e["type"] == "partial"]
    assert partial_kinds == ["whitelight", "rss"]  # no cube partial
    result = next(e for e in events if e["type"] == "result")["payload"]
    assert "whitelight" in result and result["rss_files"]
    assert "cube" not in result
