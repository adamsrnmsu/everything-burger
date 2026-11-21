import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from burger import invalid, verify
from burger.cli import main
from burger.items import all_items, load_item
from burger.vcs import board_dir
from tests.conftest import res
from tests.fakes import FakeHost, StrictHost
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
    r = res(settings, "integrate")
    rows = invalid.scorch_rows(r, settings, FakeHost(settings=settings), tmp_path / "m.yaml")
    assert {x.verdict for x in rows} == {"skipped"} and "burger sync integrate" in rows[0].reason
    synced(settings, "integrate")
    host = FakeHost(run(code=127), settings)
    rows = invalid.scorch_rows(r, settings, host, tmp_path / "m.yaml")
    assert {x.reason for x in rows} == {"uv not found"}


def test_scorch_rows_run_the_validator_from_the_synced_tree(settings, tmp_path):
    synced(settings, "integrate")
    host = FakeHost(run(OUT), settings)
    rows = invalid.scorch_rows(res(settings, "integrate"), settings, host, tmp_path / "m.yaml")
    (cmd,) = host.calls
    assert "--with" in cmd and cmd[-2:] == ["phenix-scorch-validate", str(tmp_path / "m.yaml")]
    got = verdicts(rows)
    assert got["vmstats-bad-name"] == got["qos-bad-rate"] == "ok"
    # `vmstats` in OUT is valid; integrate's other bad-name item is not in OUT, so it fails
    assert {k for k, v in got.items() if v == "FAIL"} == {"pipe-bad-name"}


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
    r, host = setup(settings, monkeypatch, name="integrate")
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


def test_every_expect_invalid_item_is_validate_only(settings):
    # once the PR that adds a rejection merges, the item needs no apps_prs: main rejects it
    items = [i for i in all_items(settings) if i.expect_invalid]
    assert {i.name for i in items} >= {"vmstats-bad-name", "qos-bad-rate", "helics-no-broker"}
    for i in items:
        assert i.routes == [], i.name
        assert not i.verify, f"{i.name}: expect_invalid items run nothing, so verify is dead"


def test_check_fails_when_an_item_is_not_rejected(
    settings, monkeypatch, tmp_path, capsys, topologies
):
    synced(settings, "integrate")
    monkeypatch.setattr(
        "burger.cli.Host",
        lambda s, site=None: FakeHost(run("ok       m.yaml: qos-bad-rate (qos)\n"), s),
    )
    monkeypatch.setenv("BURGER_WORK", str(settings.work))
    assert main(["check", "integrate"]) == 1
    assert "FAIL" in capsys.readouterr().out


def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_judge_ignores_unindented_noise_after_a_record():
    out = "invalid  m.yaml: a (t): bad thing\nnoise that says wanted\n"
    _, rows = invalid.judge(out, {"a": "wanted"})
    assert rows[0].verdict == "FAIL" and "not matching /wanted/" in rows[0].reason
    out = "invalid  m.yaml: a (t): bad thing\n  indented wanted\nnoise\n"
    assert invalid.judge(out, {"a": "wanted"})[1][0].verdict == "ok"


def test_scorch_rows_run_the_validator_like_the_real_host(settings, tmp_path):
    synced(settings, "integrate")
    host = StrictHost(run(OUT), settings)  # exit 1 must not raise; output must be captured
    rows = invalid.scorch_rows(res(settings, "integrate"), settings, host, tmp_path / "m.yaml")
    assert verdicts(rows)["qos-bad-rate"] == "ok"
    host = StrictHost(run(OUT + "invalid  m.yaml: odd (t): " + "x" * 300 + "\n"), settings)
    rows = invalid.scorch_rows(res(settings, "integrate"), settings, host, tmp_path / "m.yaml")
    extra = rows[-1]
    assert (extra.item, extra.check, extra.verdict) == (
        "-",
        "phenix-scorch-validate (offline)",
        "FAIL",
    )
    assert extra.reason.endswith("x" * 200) and len(extra.reason) == 200


def test_installed_apps_are_read_like_the_real_host(settings):
    import json

    synced(settings, "base")
    board_dir(settings, "base").joinpath("manifest.json").write_text(
        json.dumps({"apps": {"head": "abc"}})
    )
    r = res(settings, "base")
    cat = "docker exec phenix cat /tmp/burger/installed.json"
    assert invalid._installed(r, settings, StrictHost({cat: cp(0, '{"apps": "abc"}')}, settings))
    assert not invalid._installed(r, settings, StrictHost({cat: cp(0, '{"apps": "x"}')}, settings))
    assert not invalid._installed(
        r, settings, StrictHost({cat: cp(1, '{"apps": "abc"}')}, settings)
    )
    assert not invalid._installed(r, settings, StrictHost({cat: cp(0, "not json")}, settings))


def test_app_stage_in_phenix_runs_like_the_real_host(settings):
    host = StrictHost({"docker exec -i phenix e s --dry-run": cp(3, "out", "err")}, settings)
    got = invalid._stage(host, "e", "s", "stdin")
    assert (got.returncode, got.stdout, got.stderr) == (3, "out", "err")


def test_app_rows_read_the_rejection_like_the_real_host(settings, tmp_path):
    synced(settings, "base")
    r = res(settings, "base")
    err = "root broker not found in topology: node missinghost does not exist"
    host = StrictHost({"uv run": cp(1, "", err)}, settings)
    assert [x.verdict for x in invalid.app_rows(r, settings, host, {"spec": {}}, tmp_path)] == [
        "ok"
    ]
    host = StrictHost({"uv run": cp(1, "", "some other " + "y" * 300)}, settings)
    (row,) = invalid.app_rows(r, settings, host, {"spec": {}}, tmp_path)
    assert row.verdict == "FAIL" and row.reason.endswith("y" * 200)
    assert row.reason.startswith("not matching /") and ": " + "y" * 200 in row.reason
    assert len(row.reason.rsplit(": ", 1)[1]) == 200


def test_app_row_names_the_stage_that_decided(settings, tmp_path):
    synced(settings, "base")
    r = res(settings, "base")
    err = "root broker not found in topology: node missinghost does not exist"
    topo = {"spec": {}}
    host = StrictHost({"uv run": cp(1, "", err)}, settings)
    assert (
        invalid.app_rows(r, settings, host, topo, tmp_path)[0].check == "helics configure rejects"
    )
    host = StrictHost({"uv run": cp(0)}, settings)
    assert (
        invalid.app_rows(r, settings, host, topo, tmp_path)[0].check == "helics pre-start rejects"
    )
    host = StrictHost(settings=settings)  # configure passes, pre-start rejects
    calls = iter([cp(0), cp(1, "", err)])
    host.run = lambda cmd, check=True, capture=False, **kw: next(calls)
    assert (
        invalid.app_rows(r, settings, host, topo, tmp_path)[0].check == "helics pre-start rejects"
    )


def test_a_warning_for_an_expected_item_is_not_a_rejection():
    _, rows = invalid.judge("warning  m.yaml: a (t): unknown key, ignored\n", {"a": "x"})
    assert rows[0].reason == "the validator did not report this component"


def exp_out(*nodes):
    """What `configure` prints: an experiment whose topology holds (hostname, [dst]) nodes."""
    return json.dumps(
        {
            "spec": {
                "topology": {
                    "nodes": [
                        {"general": {"hostname": h}, "injections": [{"dst": d} for d in dsts]}
                        for h, dsts in nodes
                    ]
                }
            }
        }
    )


OLD = {"spec": {"nodes": [{"general": {"hostname": "rtu-1"}}]}}
WANT = {"w-1": ["/a", "/b"], "w-2": []}
GOOD = exp_out(("rtu-1", []), ("w-1", ["/a", "/b", "/c"]), ("w-2", []))


def test_nodes_problem_passes_exactly_the_wanted_new_nodes_with_their_injections():
    assert invalid._nodes_problem(WANT, OLD, GOOD) == ""


@pytest.mark.parametrize(
    ("out", "problem"),
    [
        (exp_out(("w-1", ["/a", "/b"])), "added nodes ['w-1'], want ['w-1', 'w-2']"),
        (exp_out(("w-1", ["/a", "/b"]), ("w-2", []), ("w-3", [])), "added nodes ['w-1', 'w-2', "),
        (exp_out(("w-1", ["/a"]), ("w-2", [])), "w-1 lacks injections ['/b']"),
        ("not json", "configure output is not an experiment"),
        ("{}", "configure output is not an experiment"),
        ("[]", "configure output is not an experiment"),
    ],
)
def test_nodes_problem_names_what_differs(out, problem):
    assert invalid._nodes_problem(WANT, OLD, out).startswith(problem)


def test_nodes_problem_names_the_exception_and_rejects_duplicate_hostnames():
    got = invalid._nodes_problem(WANT, OLD, "{}")
    assert "KeyError" in got and "'spec'" in got
    dup = exp_out(("w-1", ["/a", "/b"]), ("w-2", []), ("w-1", []))
    assert "duplicate added hostnames ['w-1']" in invalid._nodes_problem(WANT, OLD, dup)


def test_nodes_problem_reads_the_real_scale_configure_output():
    # captured from `phenix-app-scale configure --dry-run` (builtin, count 2, one old node)
    out = (Path(__file__).parent / "fixtures" / "scale-configure.json").read_text()
    want = {f"v2-node-{n}": ["/etc/phenix/startup/999-scale.sh"] for n in (1, 2)}
    assert (
        invalid._nodes_problem(want, {"spec": {"nodes": [{"general": {"hostname": "rtu-1"}}]}}, out)
        == ""
    )


def test_nodes_item_with_configure_ok_and_no_pre_start_is_judged(settings, tmp_path):
    good = exp_out(
        ("wf-1", ["/etc/phenix/startup/999-scale.sh", "/wind-configs.tgz"]),
        ("wf-2", ["/etc/phenix/startup/999-scale.sh", "/wind-configs.tgz"]),
    )
    host = StrictHost({"uv run": cp(0, good)}, settings)
    assert nodes_rows(settings, tmp_path, host)[0].verdict == "ok"
    assert len(host.calls) == 1  # configure only: a missing pre-start cannot discard its verdict


def test_skipped_row_is_labelled_by_what_the_item_expects(settings, tmp_path):
    (row,) = nodes_rows(settings, tmp_path, StrictHost({"uv run": cp(127)}, settings))
    assert (row.verdict, row.check) == ("skipped", "expect_nodes")


def nodes_rows(settings, tmp_path, host, name="scale-wind-expands"):
    if not (board_dir(settings, "main") / "apps").exists():
        synced(settings, "main")
    item = load_item(f"apps/{name}", settings)
    r = dataclasses.replace(res(settings, "main"), items=[], validate_only=[item])
    return invalid.app_rows(r, settings, host, OLD, tmp_path)


def test_app_rows_judge_what_configure_added(settings, tmp_path):
    want = exp_out(
        ("rtu-1", []),
        *((f"wf-{n}", ["/etc/phenix/startup/999-scale.sh", "/wind-configs.tgz"]) for n in (1, 2)),
    )
    host = StrictHost({"uv run": cp(0, want)}, settings)
    (row,) = nodes_rows(settings, tmp_path, host)
    assert (row.check, row.verdict, row.reason) == ("scale configure adds nodes", "ok", "")
    assert len(host.calls) == 1
    host = StrictHost({"uv run": cp(0, exp_out(("rtu-1", [])))}, settings)
    (row,) = nodes_rows(settings, tmp_path, host)
    assert row.verdict == "FAIL" and row.reason == "added nodes [], want ['wf-1', 'wf-2']"


def test_app_rows_fail_a_rejected_expansion_and_skip_without_uv(settings, tmp_path):
    host = StrictHost({"uv run": cp(1, "", "boom " + "y" * 300)}, settings)
    (row,) = nodes_rows(settings, tmp_path, host)
    assert row.verdict == "FAIL" and row.reason.startswith("configure rejected the config: ")
    assert row.reason.endswith("y" * 200) and "boom" not in row.reason
    (row,) = nodes_rows(settings, tmp_path, StrictHost({"uv run": cp(127)}, settings))
    assert row.verdict == "skipped"


def test_every_expect_nodes_item_is_validate_only(settings):
    items = [i for i in all_items(settings) if i.expect_nodes]
    assert {i.name for i in items} >= {"scale-builtin-expands", "scale-wind-expands"}
    for i in items:
        assert (i.kind, i.routes, i.verify, i.expect_invalid) == ("app", [], [], ""), i.name
