import subprocess

from burger import invalid, verify
from burger.cli import main
from burger.items import all_items
from burger.vcs import board_dir
from tests.conftest import res
from tests.fakes import FakeHost
from tests.test_verify import setup

# What phenix-scorch-validate prints: one record per component, pydantic's message
# spans indented lines, and loguru writes to the same stream.
OUT = """\
ok       m.yaml: vmstats (vmstats)
invalid  m.yaml: vmstats-bad-name (vmstats): invalid metadata for 'vmstats-bad-name' (1 problem(s)):
  vms.0: String should match pattern '^[^\\s'"/#\\\\]+$'
2026-10-21 18:09:40 | WARNING | phenix_apps.noise
warning  m.yaml: qos-bad-rate (qos): vms.0.x: unknown key, ignored
invalid  m.yaml: qos-bad-rate (qos): invalid metadata for 'qos-bad-rate' (1 problem(s)):
  vms.0.rate: Value error, must be a whole number of kbit
"""
EXPECT = {"vmstats-bad-name": "should match pattern", "qos-bad-rate": "whole number of kbit"}


def verdicts(rows):
    return {r.item: r.verdict for r in rows}


def test_judge_passes_when_each_is_rejected_with_its_reason():
    unexpected, rows = invalid.judge(OUT, EXPECT)
    assert unexpected == []
    assert verdicts(rows) == {"vmstats-bad-name": "ok", "qos-bad-rate": "ok"}
    assert "noise" not in rows[0].reason and "m.yaml" not in rows[0].reason


def test_judge_fails_on_the_wrong_reason_acceptance_or_silence():
    _, rows = invalid.judge(OUT, {**EXPECT, "vmstats-bad-name": "no such text"})
    assert verdicts(rows)["vmstats-bad-name"] == "FAIL"
    assert "not matching /no such text/" in rows[0].reason
    _, rows = invalid.judge(OUT, {"vmstats": "x"})  # accepted
    assert rows[0].verdict == "FAIL" and "ok, want it rejected" in rows[0].reason
    _, rows = invalid.judge("", {"gone": "x"})
    assert "did not report" in rows[0].reason


def test_judge_returns_invalid_records_nobody_expected():
    unexpected, _ = invalid.judge(OUT, {"qos-bad-rate": "whole number"})
    assert len(unexpected) == 1 and unexpected[0].startswith("invalid m.yaml: vmstats-bad-name")


def synced(settings, board):
    src = board_dir(settings, board) / "apps" / "src" / "python"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text("")


def run(out="", code=1):
    return {"uv run": subprocess.CompletedProcess([], code, out, "")}


def test_scorch_rows_need_a_synced_tree_and_uv(settings, tmp_path):
    r = res(settings, "metadata")
    rows = invalid.scorch_rows(r, settings, FakeHost(settings=settings), tmp_path / "m.yaml")
    assert {x.verdict for x in rows} == {"skipped"} and "burger sync metadata" in rows[0].reason
    synced(settings, "metadata")
    host = FakeHost(run(code=127), settings)
    rows = invalid.scorch_rows(r, settings, host, tmp_path / "m.yaml")
    assert {x.reason for x in rows} == {"uv not found"}


def test_scorch_rows_run_the_validator_from_the_synced_tree(settings, tmp_path):
    synced(settings, "metadata")
    host = FakeHost(run(OUT), settings)
    rows = invalid.scorch_rows(res(settings, "metadata"), settings, host, tmp_path / "m.yaml")
    (cmd,) = host.calls
    assert "--with" in cmd and cmd[-2:] == ["phenix-scorch-validate", str(tmp_path / "m.yaml")]
    got = verdicts(rows)
    assert got["vmstats-bad-name"] == got["qos-bad-rate"] == "ok"
    assert "FAIL" not in got.values()  # `vmstats` in OUT is valid


def test_app_rows_without_uv_use_the_apps_installed_in_phenix(settings, tmp_path):
    import json

    synced(settings, "base")
    man = board_dir(settings, "base") / "manifest.json"
    man.write_text(json.dumps({"apps": {"head": "abc"}}))
    r = res(settings, "base")
    err = "root broker not found in topology: node missinghost does not exist"
    topo = {"spec": {"nodes": []}}
    answers = {
        "uv run": subprocess.CompletedProcess([], 127, "", "uv not found"),
        "docker exec phenix cat": subprocess.CompletedProcess([], 0, '{"apps": "abc"}', ""),
        "docker exec -i phenix phenix-app-helics": subprocess.CompletedProcess([], 1, "", err),
    }
    host = FakeHost(answers, settings)
    assert [x.verdict for x in invalid.app_rows(r, settings, host, topo, tmp_path)] == ["ok"]
    assert any(c[:4] == ["docker", "exec", "-i", "phenix"] for c in host.calls)
    # installed apps from another commit are not evidence: skip, do not judge stale code
    answers["docker exec phenix cat"] = subprocess.CompletedProcess([], 0, '{"apps": "old"}', "")
    rows = invalid.app_rows(r, settings, FakeHost(answers, settings), topo, tmp_path)
    assert [x.verdict for x in rows] == ["skipped"] and "uv not found" in rows[0].reason


def test_app_rows_stop_at_the_first_failing_stage(settings, tmp_path):
    synced(settings, "base")
    r = res(settings, "base")
    err = "root broker not found in topology: node missinghost does not exist"
    topo = {"spec": {"nodes": []}}
    host = FakeHost(
        {
            "uv run --no-project --quiet --with": subprocess.CompletedProcess([], 1, "", err),
        },
        settings,
    )
    rows = invalid.app_rows(r, settings, host, topo, tmp_path)
    assert [x.verdict for x in rows] == ["ok"]
    assert [c[c.index("phenix-app-helics") + 1] for c in host.calls] == ["configure"]  # fails first
    # a config every stage accepts is the failure
    host = FakeHost(run(code=0), settings)
    assert invalid.app_rows(r, settings, host, topo, tmp_path)[0].verdict == "FAIL"
    assert len(host.calls) == 2  # configure, then pre-start


def validator_host(settings, monkeypatch, out, code):
    r, host = setup(settings, monkeypatch, name="metadata")
    host.responses["docker exec phenix sh -c command -v"] = subprocess.CompletedProcess(
        [], 0, "", ""
    )
    host.responses["docker exec phenix phenix-scorch-validate"] = subprocess.CompletedProcess(
        [], code, out, ""
    )
    return r, host


def test_verify_passes_a_validator_that_rejects_only_what_is_expected(settings, monkeypatch):
    r, host = validator_host(settings, monkeypatch, OUT, 1)
    rows = [x for x in verify.verify(r, settings, host) if x.item in ("-", *EXPECT)]
    got = {(x.item, x.check): x.verdict for x in rows}
    assert got[("-", "phenix-scorch-validate")] == "ok"
    assert got[("vmstats-bad-name", "rejected by phenix-scorch-validate")] == "ok"


def test_verify_fails_an_unexpected_rejection(settings, monkeypatch):
    r, host = validator_host(settings, monkeypatch, OUT + "invalid  m.yaml: qos (qos): bad\n", 1)
    row = next(x for x in verify.verify(r, settings, host) if x.check == "phenix-scorch-validate")
    assert row.verdict == "FAIL" and "qos (qos): bad" in row.reason


def test_verify_fails_a_validator_that_crashes(settings, monkeypatch):
    r, host = validator_host(settings, monkeypatch, "Traceback ...", 1)  # exit 1, nothing rejected
    row = next(x for x in verify.verify(r, settings, host) if x.check == "phenix-scorch-validate")
    assert row.verdict == "FAIL"


def test_every_expect_invalid_item_is_validate_only_and_names_its_pr(settings):
    items = [i for i in all_items(settings) if i.expect_invalid]
    assert {i.name for i in items} >= {"vmstats-bad-name", "qos-bad-rate", "helics-no-broker"}
    for i in items:
        assert i.routes == [] and i.requires.get("apps_prs"), i.name
        assert not i.verify, f"{i.name}: expect_invalid items run nothing, so verify is dead"


def test_check_fails_when_an_item_is_not_rejected(
    settings, monkeypatch, tmp_path, capsys, topologies
):
    synced(settings, "metadata")
    monkeypatch.setattr(
        "burger.cli.Host",
        lambda s, site=None: FakeHost(run("ok       m.yaml: qos-bad-rate (qos)\n"), s),
    )
    monkeypatch.setenv("BURGER_WORK", str(settings.work))
    assert main(["check", "metadata"]) == 1
    assert "FAIL" in capsys.readouterr().out
