import dataclasses
import json
import subprocess
import sys

import pytest

from burger import probes
from burger.config import BurgerError
from burger.probes import mm_bindings
from burger.switchboard import load_switchboard
from tests.conftest import res as resolved
from tests.fakes import FakeHost

CTR = "docker exec phenix python3 -c"


def host_for(settings, running, responses=None):
    host = FakeHost(responses, settings)
    exp = "ebwes-main"
    if running:
        host.api_responses = {
            f"/experiments/{exp}": {"running": True},
            f"/experiments/{exp}/vms": {"vms": [{"name": "rtu-1", "state": "running"}]},
        }
    return host


def test_probes_skip_when_the_experiment_is_down(settings):
    res = resolved(settings, "main")
    rows = probes.run(host_for(settings, False), settings, res, {}, ["mm-bindings"])
    assert [(r.item, r.verdict) for r in rows] == [("mm-bindings", "skipped")]
    assert "experiment not running" in rows[0].reason


def test_a_raising_probe_is_a_fail_row(settings, monkeypatch):
    def boom(*a):
        raise KeyError("nope")

    monkeypatch.setitem(probes.PROBES, "mm-bindings", boom)
    res = resolved(settings, "main")
    rows = probes.run(host_for(settings, True), settings, res, {}, ["mm-bindings"])
    assert [(r.item, r.verdict) for r in rows] == [("mm-bindings", "FAIL")]
    assert "KeyError" in rows[0].reason


def test_unknown_probe_name(settings):
    res = resolved(settings, "main")
    with pytest.raises(BurgerError, match="unknown probe 'nope'"):
        probes.run(host_for(settings, True), settings, res, {}, ["nope"])


def test_unknown_probe_in_a_board_is_a_load_error(settings, tmp_path):
    (tmp_path / "switchboards").mkdir()
    (tmp_path / "routes").symlink_to(settings.root / "routes")
    (tmp_path / "switchboards" / "x.yaml").write_text("route: waterway\nprobes: [nope]\n")
    with pytest.raises(BurgerError, match="unknown probe 'nope'"):
        load_switchboard("x", dataclasses.replace(settings, root=tmp_path))


def test_bindings_rows_from_the_script_result(settings):
    out = json.dumps([["with block", ""], ["close twice", "RuntimeError: shut"]])
    host = host_for(settings, True, {CTR: subprocess.CompletedProcess([], 0, out, "")})
    rows = mm_bindings.run(host, settings, resolved(settings, "main"), {})
    assert [(r.check, r.verdict, r.reason) for r in rows] == [
        ("with block", "ok", ""),
        ("close twice", "FAIL", "RuntimeError: shut"),
    ]
    assert {r.item for r in rows} == {"mm-bindings"}
    assert host.calls[-1][-2:] == ["ebwes-main", "rtu-1"]


def test_bindings_predates_1686(settings):
    out = json.dumps([["context manager", "minimega.py predates #1686"]])
    host = host_for(settings, True, {CTR: subprocess.CompletedProcess([], 0, out, "")})
    (row,) = mm_bindings.run(host, settings, resolved(settings, "main"), {})
    assert row.verdict == "FAIL" and "predates #1686" in row.reason


def test_bindings_script_error_is_raised(settings):
    host = host_for(settings, True, {CTR: subprocess.CompletedProcess([], 1, "", "ImportError")})
    with pytest.raises(BurgerError, match="ImportError"):
        mm_bindings.run(host, settings, resolved(settings, "main"), {})


# A stand-in minimega.py: a client with #1686's context manager and #1684's cc_filtered.
# FILTER_BAD drops the finally; OLD has no __enter__.
FAKE = """
import contextlib

class Client:
    def __init__(self, namespace):
        self.filter, self.open = None, True
    def _live(self):
        if not self.open:
            raise OSError("closed")
    def vm_info(self):
        self._live()
        return [{"Tabular": [["rtu-1"]]}]
    def cc_filter(self, filter=None):
        self._live()
        if filter is not None:
            self.filter = filter
            return []
        return [{"Tabular": [["", self.filter]] if self.filter else None}]
    def clear_cc_filter(self):
        self.filter = None
    def close(self):
        self.open = False
    def __copy__(self):
        return Client(None)
    def __reduce__(self):
        raise TypeError('socket')
    __ENTER__
    @contextlib.contextmanager
    def cc_filtered(self, *filters):
        self.cc_filter(" ".join(filters))
        __BODY__

def connect(namespace=None):
    return Client(namespace)
"""
ENTER = (
    "def __enter__(self):\n        return self\n    def __exit__(self, *a):\n        self.close()"
)
FINALLY = "try:\n            yield self\n        finally:\n            self.clear_cc_filter()"
NOFINALLY = "yield self\n        self.clear_cc_filter()"


def script_rows(tmp_path, enter=ENTER, body=FINALLY):
    src = FAKE.replace("__ENTER__", enter).replace("__BODY__", body)
    (tmp_path / "minimega.py").write_text(src)
    cmd = [sys.executable, "-c", mm_bindings.SCRIPT, "ebwes-main", "rtu-1"]
    env = {"PYTHONPATH": str(tmp_path)}
    got = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert got.returncode == 0, got.stderr
    return {name: err for name, err in json.loads(got.stdout)}


def test_script_passes_on_a_good_client(tmp_path):
    rows = script_rows(tmp_path)
    assert len(rows) == 7 and not any(rows.values()), rows


def test_script_fails_when_a_raise_leaves_the_filter(tmp_path):
    rows = script_rows(tmp_path, body=NOFINALLY)
    assert "filter left after the block raised" in rows["filtered raises"]
    assert not rows["filtered"]


def test_script_says_when_the_bindings_predate_1686(tmp_path):
    rows = script_rows(tmp_path, enter="")
    assert rows == {"context manager": "minimega.py predates #1686"}


def test_no_probes_named_is_an_error(settings):
    res = resolved(settings, "main")
    with pytest.raises(BurgerError, match="no probes"):
        probes.run(host_for(settings, True), settings, res, {})


def test_bindings_skips_a_route_without_rtus(settings):
    res = resolved(settings, "main")
    res.route.values["rtus"] = []
    (row,) = mm_bindings.run(host_for(settings, True), settings, res, {})
    assert (row.verdict, row.reason) == ("skipped", "the route has no RTUs")
