"""Tests for calibration-source resolution and the master-overwrite guard."""
import os

import pytest

from llamas_ql import config


def test_guard_blocks_master_dirs():
    for master in config.master_calib_dirs_readonly():
        with pytest.raises(ValueError):
            config.guard_not_master_dir(master)


def test_guard_allows_nightly_dir(tmp_path):
    nightly = tmp_path / "traces"
    nightly.mkdir()
    # Should not raise for an ordinary nightly output dir.
    config.guard_not_master_dir(str(nightly))
    config.guard_not_master_dir(os.path.join(config.OUTPUT_DIR, "traces"))


def test_resolve_prefers_local_when_populated(tmp_path, monkeypatch):
    local = tmp_path / "mastercalib"
    local.mkdir()
    (local / "LLAMAS_master_green_1_A_traces.pkl").write_bytes(b"x")
    monkeypatch.setattr(config, "_LOCAL_CALIB_DIR", str(local))
    # Even if pyjamas is available, a populated local dir wins.
    monkeypatch.setattr(config, "pyjamas_pkg_dir", lambda: "/some/pyjamas")
    assert config.resolve_master_calib_dir() == str(local)


def test_resolve_falls_back_to_pyjamas(tmp_path, monkeypatch):
    empty_local = tmp_path / "empty_mastercalib"
    empty_local.mkdir()
    pj = tmp_path / "pyjamas"
    (pj / "mastercalib").mkdir(parents=True)
    (pj / "mastercalib" / "LLAMAS_master_blue_1_A_traces.pkl").write_bytes(b"x")
    monkeypatch.setattr(config, "_LOCAL_CALIB_DIR", str(empty_local))
    monkeypatch.setattr(config, "pyjamas_pkg_dir", lambda: str(pj))
    assert config.resolve_master_calib_dir() == str(pj / "mastercalib")


def test_resolve_returns_none_when_nothing_available(tmp_path, monkeypatch):
    empty_local = tmp_path / "empty_mastercalib"
    empty_local.mkdir()
    monkeypatch.setattr(config, "_LOCAL_CALIB_DIR", str(empty_local))
    monkeypatch.setattr(config, "pyjamas_pkg_dir", lambda: None)
    assert config.resolve_master_calib_dir() is None


def test_reference_arc_prefers_local(tmp_path, monkeypatch):
    lut = tmp_path / "LUT"
    lut.mkdir()
    arc = lut / "LLAMAS_reference_arc.pkl"
    arc.write_bytes(b"x")
    monkeypatch.setattr(config, "LUT_DIR", str(lut))
    monkeypatch.setattr(config, "pyjamas_pkg_dir", lambda: None)
    assert config.resolve_reference_arc() == str(arc)
