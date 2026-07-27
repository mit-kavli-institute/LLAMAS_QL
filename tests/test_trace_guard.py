"""The no-overwrite guard blocks nightly writes into a master library, but a
deliberate master build (allow_master=True) is permitted."""
import types

import pytest

from llamas_ql import config
from llamas_ql.Trace.traceLlamasMaster import TraceLlamas


def test_savetraces_blocks_master_write_by_default(tmp_path, monkeypatch):
    master = tmp_path / "mastercalib"
    master.mkdir()
    monkeypatch.setattr(config, "master_calib_dirs_readonly", lambda: (str(master),))
    # nightly/science generation (allow_master default False) must not write here
    with pytest.raises(ValueError):
        TraceLlamas.saveTraces(types.SimpleNamespace(),
                               outfile="t.pkl", newpath=str(master))
    assert not (master / "t.pkl").exists()


def test_savetraces_allows_deliberate_master_build(tmp_path, monkeypatch):
    master = tmp_path / "mastercalib"
    master.mkdir()
    monkeypatch.setattr(config, "master_calib_dirs_readonly", lambda: (str(master),))
    TraceLlamas.saveTraces(types.SimpleNamespace(payload=1),
                           outfile="t.pkl", newpath=str(master), allow_master=True)
    assert (master / "t.pkl").exists()
