import json

import pytest

from burger import fdlogs, verify
from burger.config import BurgerError
from burger.switchboard import load_switchboard
from tests.test_verify import setup


def host_dir(tmp_path, files: dict[str, str], **meta):
    """A fake fdlogs/<host> directory: the tails plus the VM's manifest."""
    tmp_path.mkdir(exist_ok=True)
    entries = []
    for i, (src, text) in enumerate(files.items()):
        name = f"{i:02d}-{src.rsplit('/', 1)[-1]}"
        (tmp_path / name).write_text(text)
        entries.append({"src": src, "name": name, "size": len(text), "kept": len(text)} | meta)
    (tmp_path / "_manifest.json").write_text(json.dumps({"procs": [], "files": entries}))
    return tmp_path


CFG = fdlogs.DEFAULTS | {"repeat": 100}


def test_crash_and_error_classes(tmp_path):
    text = "\n".join(
        [
            "2026-11-10 08:00:01 terminate called after throwing an instance of 'std::bad_alloc'",
            "Segmentation fault (core dumped)",
            "accept: Too many open files",
            "E: could not open",
            "connection 7 failed",
            "all fine",
        ]
    )
    s = fdlogs.scan_host(host_dir(tmp_path, {"/x/err.log": text}), CFG)
    assert (s["crash"], s["error"], s["flood"]) == (2, 3, False)


def test_repeats_collapse_with_first_and_last_timestamp(tmp_path):
    text = "\n".join(f"2026-11-10 08:00:{i:02d} accept failed: fd {i}" for i in range(10))
    s = fdlogs.scan_host(host_dir(tmp_path, {"/x/err.log": text}), CFG)
    (top,) = s["top"]
    assert top["count"] == 10 and top["text"].startswith("accept failed")
    assert top["first"].endswith("08:00:00") and top["last"].endswith("08:00:09")


def test_flood_by_repeat_and_by_rate(tmp_path):
    s = fdlogs.scan_host(host_dir(tmp_path / "a", {"/x/w.log": "pid = 5\n" * 101}), CFG)
    assert s["flood"] and "101x" in s["files"][0]["flood"]
    rate = {"bytes_per_min": 60_000_000}
    s = fdlogs.scan_host(host_dir(tmp_path / "b", {"/x/w.log": "ok\n"}, **rate), CFG)
    assert s["flood"] and "60 MB/min" in s["files"][0]["flood"]
    s = fdlogs.scan_host(host_dir(tmp_path / "c", {"/x/w.log": "ok\n"}), CFG)
    assert not s["flood"]


def test_missing_manifest_is_an_error_entry(tmp_path):
    assert "no manifest" in fdlogs.scan_host(tmp_path, CFG)["error"]


def clean():
    return {"rtu-1": {"crash": 0, "error": 0, "flood": False, "files": [], "top": []}}


def test_verdict_by_fail_on():
    crashed = {"rtu-1": clean()["rtu-1"] | {"crash": 1}}
    noisy = {"rtu-1": clean()["rtu-1"] | {"error": 4, "flood": True}}
    assert fdlogs.verdict(clean(), "crash")[0] == "ok"
    assert fdlogs.verdict(crashed, "crash")[0] == "FAIL"
    assert fdlogs.verdict(crashed, "none")[0] == "WARN"
    assert fdlogs.verdict(noisy, "crash")[0] == "WARN"
    assert fdlogs.verdict(noisy, "error")[0] == "FAIL"
    assert fdlogs.verdict({"rtu-1": {"error": "not registered"}}, "crash")[0] == "WARN"


def test_default_fail_on_follows_the_bennu_block(settings):
    assert fdlogs.settings_for(load_switchboard("robust", settings))["fail_on"] == "crash"
    assert fdlogs.settings_for(load_switchboard("baseline", settings))["fail_on"] == "none"
    board = load_switchboard("robust", settings)
    board.fdlogs = {"fail_on": "none"}
    assert fdlogs.settings_for(board)["fail_on"] == "none"
    board.fdlogs = {"fail_on": "bogus"}
    with pytest.raises(BurgerError, match="fail_on"):
        fdlogs.settings_for(board)


def test_vm_script_compiles_and_command_has_no_dollar():
    cmd = fdlogs.vm_command()
    assert "$" not in cmd and '"' not in cmd
    compile(fdlogs.VM_SCRIPT % {"tail": 1, "maxf": 1}, "vm", "exec")


def test_verify_adds_row_report_section_and_manifest(settings, monkeypatch, tmp_path):
    res, host = setup(settings, monkeypatch, name="robust")
    hd = host_dir(tmp_path, {"/x/err.log": "Aborted (core dumped)\n"})
    summary = {"rtu-1": fdlogs.scan_host(hd, CFG)}
    monkeypatch.setattr(fdlogs, "collect", lambda *a: summary)
    rows = verify.verify(res, settings, host)
    (row,) = [r for r in rows if r.check == "field-device logs"]
    assert row.verdict == "FAIL" and "1 crash-class" in row.reason
    rd = settings.work / "robust"
    report = (rd / "report.md").read_text()
    assert "## Field-device logs" in report and "| rtu-1 | 1 |" in report
    assert json.loads((rd / "manifest.json").read_text())["fdlogs"]["rtu-1"]["crash"] == 1


MM = "docker exec minimega minimega -e namespace exp"
Q = ".csv true .headers false .annotate false"


def cc_host(settings, *, filter_rc=0, ids="3\n5\n", answered="5,1", fetch_rc=0):
    from subprocess import CompletedProcess as CP

    from tests.fakes import FakeHost

    return FakeHost(
        {
            f"{MM} cc filter": CP([], filter_rc, "", ""),
            f"{MM} {Q} .columns id cc commands": CP([], 0, ids, ""),
            f"{MM} {Q} .columns id,responses cc commands": CP([], 0, answered, ""),
            "docker cp": CP([], fetch_rc, "", ""),
        },
        settings,
    )


def test_collect_host_fetches_the_vms_logs(settings, tmp_path):
    host = cc_host(settings)
    assert fdlogs.collect_host(host, settings, "exp", "u-1", tmp_path / "rtu-1") == ""
    lines = [" ".join(c) for c in host.calls]
    assert f"{MM} cc filter uuid=u-1" in lines[0]
    assert any("cc exec /bin/sh -c" in x for x in lines) and any("cc recv" in x for x in lines)
    assert f"{MM} clear cc filter" in lines
    assert lines[-1] == (
        f"docker cp phenix:/phenix/images/exp/miniccc_responses/5/u-1{fdlogs.OUT} "
        f"{tmp_path / 'rtu-1'}"
    )


def test_collect_host_says_why_it_failed(settings, tmp_path):
    dst = tmp_path / "rtu-1"
    why = fdlogs.collect_host(cc_host(settings, filter_rc=1), settings, "exp", "u", dst)
    assert why == "minimega in 'minimega' unreachable"
    why = fdlogs.collect_host(cc_host(settings, ids=""), settings, "exp", "u", dst)
    assert why == "no cc response within 90 s"
    host = cc_host(settings, answered="5,0")
    assert fdlogs.collect_host(host, settings, "exp", "u", dst, timeout=0) == (
        "no cc response within 0 s"
    )
    assert f"{MM} clear cc filter" in [" ".join(c) for c in host.calls]  # even when it failed
    why = fdlogs.collect_host(cc_host(settings, fetch_rc=1), settings, "exp", "u", dst)
    assert why == "the VM sent no logs (no bennu field device?)"


def test_answered_polls_until_the_response_count_is_one(settings, monkeypatch):
    sleeps = []
    monkeypatch.setattr(fdlogs.time, "sleep", sleeps.append)
    assert fdlogs._answered(cc_host(settings, answered="5,1"), "exp", "5", 90)
    assert not fdlogs._answered(cc_host(settings, answered="5,0\n15,1"), "exp", "5", 1)
    assert not fdlogs._answered(cc_host(settings, answered="15,1"), "exp", "5", 1)  # 5 != 15
    assert sleeps and set(sleeps) == {2}


def board(rtus=("rtu-1", "rtu-2")):
    from types import SimpleNamespace as NS

    return NS(board=NS(name="robust", exp="exp"), route=NS(values={"rtus": list(rtus)}))


def test_collect_skips_without_minimega_or_rtus(settings, tmp_path):
    import dataclasses

    from tests.fakes import FakeHost

    s = dataclasses.replace(settings, work=tmp_path)
    host = FakeHost(settings=s)
    assert fdlogs.collect(board(), dataclasses.replace(s, mm_ctr=""), host, CFG) == {}
    assert fdlogs.collect(board(()), s, host, CFG) == {}
    assert host.calls == []


def test_collect_summarises_each_rtu_and_writes_summary_json(settings, monkeypatch, tmp_path):
    from burger import experiment
    from burger.vcs import board_dir

    seen = {}

    def uuids(host, exp, want, timeout):
        seen["uuids"] = (exp, want, timeout)
        return {"rtu-1": "u1", "rtu-2": "u2"}

    monkeypatch.setattr(experiment, "vm_uuids", uuids)
    monkeypatch.setattr(experiment, "cc_registered", lambda host, exp: {"u1"})
    calls = []

    def collect_host(host, s, exp, uuid, dst):
        calls.append((exp, uuid, dst.name))
        dst.parent.mkdir(parents=True, exist_ok=True)
        host_dir(dst, {"/x/err.log": "Aborted (core dumped)\n"})
        return ""

    monkeypatch.setattr(fdlogs, "collect_host", collect_host)
    root = board_dir(settings, "robust") / "results" / "fdlogs"
    root.mkdir(parents=True)
    (root / "stale").write_text("x")  # last run's logs are cleared first
    summary = fdlogs.collect(board(), settings, object(), CFG)
    assert seen["uuids"] == ("exp", {"rtu-1", "rtu-2"}, -1)
    assert calls == [("exp", "u1", "rtu-1")]
    assert summary["rtu-1"]["crash"] == 1
    assert summary["rtu-2"] == {"error": "not registered with cc"}
    assert not (root / "stale").exists()
    assert json.loads((root / "summary.json").read_text()) == summary


def test_collect_reports_a_host_that_could_not_be_collected(settings, monkeypatch):
    from burger import experiment
    from burger.vcs import board_dir

    monkeypatch.setattr(experiment, "vm_uuids", lambda *a, **k: {"rtu-1": "u1"})
    monkeypatch.setattr(experiment, "cc_registered", lambda *a: {"u1"})
    monkeypatch.setattr(fdlogs, "collect_host", lambda *a: "no cc response within 90 s")
    summary = fdlogs.collect(board(("rtu-1",)), settings, object(), CFG)
    assert summary == {"rtu-1": {"error": "no cc response within 90 s"}}

    def boom(*a, **k):
        raise OSError("docker is gone")

    monkeypatch.setattr(experiment, "vm_uuids", boom)
    summary = fdlogs.collect(board(), settings, object(), CFG)
    assert summary == {"-": {"error": "OSError: docker is gone"}}
    assert (board_dir(settings, "robust") / "results" / "fdlogs" / "summary.json").is_file()


def test_a_repeat_count_at_the_limit_is_not_a_flood(tmp_path):
    s = fdlogs.scan_host(host_dir(tmp_path, {"/x/w.log": "pid = 5\n" * 100}), CFG)  # repeat=100
    assert not s["flood"]


def test_rate_at_the_limit_and_missing_rate_are_not_floods(tmp_path):
    cfg = CFG | {"flood_bytes_per_min": 0}
    s = fdlogs.scan_host(host_dir(tmp_path / "a", {"/x/w.log": "ok\n"}, bytes_per_min=0), cfg)
    assert not s["flood"]
    hd = tmp_path / "b"
    hd.mkdir()
    (hd / "_manifest.json").write_text(json.dumps({"procs": [], "files": [{"name": "00-w.log"}]}))
    s = fdlogs.scan_host(hd, cfg)  # no rate in the manifest at all
    assert not s["flood"] and s["files"][0]["size"] == 0 and s["files"][0]["kept"] == 0


def test_top_lines_are_most_frequent_first(tmp_path):
    text = "rare failed\n" + "common failed\n" * 3 + "mid failed\n" * 2
    s = fdlogs.scan_host(host_dir(tmp_path, {"/x/e.log": text}), CFG)
    assert [t["count"] for t in s["top"]] == [3, 2, 1]


def test_any_one_kind_of_trouble_is_a_warn():
    one = clean()["rtu-1"]
    for odd in ({"error": 1}, {"flood": True}, {"crash": 1}):
        assert fdlogs.verdict({"rtu-1": one | odd}, "none")[0] == "WARN"
    assert fdlogs.verdict({"rtu-1": one | {"crash": 1}}, "error")[0] == "FAIL"
    assert fdlogs.verdict({"rtu-1": one | {"error": 1}}, "crash")[0] == "WARN"


def test_render_section_rows():
    cfg = fdlogs.DEFAULTS
    assert fdlogs.render_section({}, cfg) == []
    quiet = {"crash": 0, "error": 0, "flood": False, "top": [], "files": [
        {"src": "/x/err.log", "size": 1234, "flood": ""}]}  # fmt: skip
    loud = quiet | {"flood": True, "top": [{"text": "a|b", "count": 3}],
                    "files": [{"src": "/x/err.log", "size": 9, "flood": "5 MB/min"}]}  # fmt: skip
    out = fdlogs.render_section({"rtu-1": quiet, "rtu-2": loud, "rtu-3": {"error": "gone"}}, cfg)
    assert "| rtu-1 | 1 | err.log 1,234 | 0 | 0 | no | - |" in out
    assert "| rtu-2 | 1 | err.log 9 | 0 | 0 | err.log: 5 MB/min | 3x `a/b` |" in out
    assert "| rtu-3 | - | - | - | - | - | gone |" in out
