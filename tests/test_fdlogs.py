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
