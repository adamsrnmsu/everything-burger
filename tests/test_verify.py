import dataclasses
import json
import subprocess

import yaml

from burger import verify as v
from burger.checks import Row
from burger.compose import health_checks
from burger.switchboard import load_switchboard, resolve
from burger.vcs import board_dir
from tests.conftest import MAN
from tests.fakes import FakeHost


def setup(settings, monkeypatch, name="bennu", results=None, enable=(), linux_only=False):
    board = load_switchboard(name, settings)
    for item in enable:
        board.items[item] = True
    res = resolve(board, settings, linux_only)
    rd = board_dir(settings, name)
    rd.mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps({**MAN, "synced": "now"}))
    host = FakeHost(
        {
            "docker exec phenix cat /tmp/burger/installed.json": subprocess.CompletedProcess(
                [], 0, json.dumps({"apps": MAN["apps"]["head"]}), ""
            )
        },
        settings,
    )
    exp = res.board.exp
    host.api_responses = {
        f"/experiments/{exp}": {"running": True},
        f"/experiments/{exp}/vms": {"vms": [{"name": "a", "state": "running"}]},
    }
    monkeypatch.setattr(v, "collect", lambda *a, **k: {} if results is None else results)
    return res, host


def test_health_attribution(settings, monkeypatch):
    results = {}
    res, host = setup(settings, monkeypatch, results=results)
    checks = health_checks(res, MAN)
    on_host = [c for c in checks if c[1] == checks[0][1]]
    bad = on_host[1]
    err = f"Error occurred: command '{bad[2]['args']}' failed: boom"
    results[("health", "start")] = [{"return": err}]
    rows = [r for r in v.verify(res, settings, host) if r.check.startswith("vm ")]
    assert len(rows) == len(checks)
    n = checks.index(bad)
    assert rows[n].verdict == "FAIL" and "boom" in rows[n].reason
    assert rows[0].verdict == "ok"
    later = [r for r, c in zip(rows, checks, strict=True) if c in on_host[2:]]
    assert later and all(r.verdict == "skipped" and "not reached" in r.reason for r in later)


def test_health_ok_marks_vm_checks_ok(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, results={("health", "start"): [{"return": ""}]})
    rows = [r for r in v.verify(res, settings, host) if r.check.startswith("vm ")]
    assert rows and {r.verdict for r in rows} == {"ok"}


def test_report_paths_and_verdict(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch)
    rows = [Row("pcap", "files", "FAIL", "no output", ["/x/a.pcap"]), Row("a", "b", "ok", "", [])]
    out = v.render_report(res, MAN | {"synced": "t"}, {"linux_only": False}, rows)
    assert "| item | check | verdict | reason | paths |" in out
    assert "`/x/a.pcap`" in out
    assert out.rstrip().endswith("**FAIL**: 1 failing check(s)")


def test_advisory_not_failing(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch)
    rows = [Row("erspan", "status configure", "advisory-FAIL", "x", [])]
    out = v.render_report(res, MAN | {"synced": "t"}, {"linux_only": False}, rows)
    assert "**PASS**" in out and "**FAIL**" not in out


def test_models_yaml_includes_validate_only(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="metadata")  # metadata as shipped
    v.verify(res, settings, host)
    doc = yaml.safe_load((board_dir(settings, "metadata") / "out" / "models.yaml").read_text())
    assert "disruption-dos" in [c["name"] for c in doc["components"]]
    assert (board_dir(settings, "metadata") / "report.md").is_file()


def test_advisory_item_in_explicit_run_stays_advisory(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="iperf", enable=["erspan"])
    runs = [{**res.board.runs[0], "configure": ["iperf", "erspan"]}]
    res.board = dataclasses.replace(res.board, runs=runs)
    rows = [r for r in v.verify(res, settings, host) if r.item == "erspan"]
    assert rows and all(r.verdict == "advisory-FAIL" for r in rows)


def test_windows_checks_skipped_under_linux_only(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="harden", linux_only=True)
    assert "tcpdump -nn" not in json.dumps(health_checks(res, MAN))  # not in the health run
    rows = v.verify(res, settings, host)
    skipped = {
        (r.item, r.check.split(":")[0]) for r in rows if r.reason == "needs the Windows nodes"
    }
    assert skipped == {("pcap", "pcap"), ("tcpdump", "pcap"), ("mirror", "vm monitor")}
    assert all(r.verdict == "skipped" for r in rows if r.reason == "needs the Windows nodes")


def test_windows_checks_run_with_windows(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="harden")
    assert "tcpdump -nn" in json.dumps(health_checks(res, MAN))
    rows = v.verify(res, settings, host)
    assert not [r for r in rows if r.reason == "needs the Windows nodes"]
    assert next(r for r in rows if r.item == "tcpdump" and r.check == "pcap").verdict == "FAIL"
