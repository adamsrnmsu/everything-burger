import dataclasses
import json
import subprocess
import sys

import pytest
from loguru import logger

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


def test_a_raising_probe_logs_its_traceback_at_debug(settings, monkeypatch):
    """The FAIL row keeps the console short; -v (debug) must still show where it broke."""

    def boom(*a):
        raise KeyError("nope")

    monkeypatch.setitem(probes.PROBES, "mm-bindings", boom)
    got = []
    hid = logger.add(lambda m: got.append(m.record), level="DEBUG")
    try:
        probes.run(
            host_for(settings, True), settings, resolved(settings, "main"), {}, ["mm-bindings"]
        )
    finally:
        logger.remove(hid)
    raised = [r for r in got if r["message"] == "probe mm-bindings raised"]
    assert raised and raised[0]["level"].name == "DEBUG"
    assert raised[0]["exception"] and raised[0]["exception"].type is KeyError


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
    res = resolved(settings, "arm64")  # a board with no probes
    with pytest.raises(BurgerError, match="no probes"):
        probes.run(host_for(settings, True), settings, res, {})


def test_bindings_skips_a_route_without_rtus(settings):
    res = resolved(settings, "main")
    res.route.values["rtus"] = []
    (row,) = mm_bindings.run(host_for(settings, True), settings, res, {})
    assert (row.verdict, row.reason) == ("skipped", "the route has no RTUs")


def bindings(settings, out="", err="", code=0):
    from burger.probes import mm_bindings
    from tests.test_verify import StrictHost

    host = StrictHost({CTR: subprocess.CompletedProcess([], code, out, err)}, settings)
    return mm_bindings.run(host, settings, resolved(settings, "main"), {})


def test_bindings_read_the_last_line_of_the_probe_output(settings):
    rows = bindings(settings, 'noise line\n[["a", ""], ["b", "boom"]]\n')
    assert [(r.check, r.verdict, r.reason) for r in rows] == [
        ("a", "ok", ""),
        ("b", "FAIL", "boom"),
    ]


def test_bindings_probe_that_cannot_run_says_why(settings):
    for out, err, why in (
        ("", "stderr text", "stderr text"),
        ("out text", "", "out text"),
        ("", "", "exit 3"),
    ):
        with pytest.raises(BurgerError) as e:
            bindings(settings, out, err, code=3)
        assert str(e.value) == why
    with pytest.raises(BurgerError) as e:
        bindings(settings, "", "x" * 400 + "tail", code=1)
    assert str(e.value) == ("x" * 400 + "tail")[-300:]


def _mirror_exp(addr, subnet=None, port="grePort", ip=None):
    exp = {
        "spec": {
            "scenario": {
                "apps": [
                    {
                        "name": "mirror",
                        "hosts": [{"hostname": "monitor", "metadata": {"interface": "mirror"}}],
                    }
                ]
            },
            "topology": {
                "nodes": [
                    {
                        "general": {"hostname": "monitor"},
                        "network": {
                            "interfaces": [{"name": "mirror", "address": addr, "mask": 24}]
                        },
                    }
                ]
            },
        },
        "status": {"apps": {}},
    }
    if subnet:
        exp["status"]["apps"]["mirror"] = {
            "subnet": subnet,
            "mirrors": {"monitor": {"mirrorName": port, "ip": ip or addr}},
        }
    return exp


class _MirrorHost:
    def __init__(self, exps, ovs):
        self.exps, self.ovs = exps, ovs

    def phenix(self, *a, **kw):
        name = a[2].split("/", 1)[1]
        return subprocess.CompletedProcess(a, 0, json.dumps(self.exps[name]), "")

    def run(self, cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, self.ovs.get(tuple(cmd[-2:]), "[]"), "")

    def api(self, path):
        return {"experiments": [{"name": n} for n in self.exps]}


def test_mirror_subnet_probe(settings):
    from burger.probes import mirror_subnet

    res = resolved(settings, "main")
    exp = res.board.exp
    ovs = {("grePort", "type"): "gre", ("grePort", "options:remote_ip"): '"10.248.172.254"'}
    ok = _mirror_exp("10.248.172.254", "10.248.172.0/24")
    decoy = _mirror_exp("10.248.171.254")  # stored, never started: topology only
    rows = mirror_subnet.run(_MirrorHost({exp: ok, "decoy": decoy}, ovs), settings, res, {})
    assert [r.verdict for r in rows] == ["ok"] * 4, rows

    # #224: the decoy's subnet handed out again, and a GRE port OVS did not take
    shared = _mirror_exp("10.248.171.254", "10.248.171.0/24")
    ovs[("grePort", "error")] = '"could not add network device grePort to ofproto (File exists)"'
    rows = mirror_subnet.run(_MirrorHost({exp: shared, "decoy": decoy}, ovs), settings, res, {})
    assert {r.check: r.verdict for r in rows} == {
        "post-start status": "ok",
        "gre port monitor": "FAIL",
        "target address monitor": "ok",
        "subnet unique": "FAIL",
    }

    # post-start failed and exited 0: no status at all
    rows = mirror_subnet.run(_MirrorHost({exp: decoy}, {}), settings, res, {})
    assert [(r.check, r.verdict) for r in rows] == [("post-start status", "FAIL")]
