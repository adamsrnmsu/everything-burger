import json
import re

from burger.checks.hook import load
from burger.items import load_item
from burger.switchboard import load_switchboard
from tests.conftest import assert_one_cc_component


def result(**kw):
    names = ["socket modes", "status", "on x3", "one fd", "tail", "dupes", "reverts", "off"]
    base = {"error": None, "pid": 7, "user": "root", "expected": 8}
    return base | {"checks": [{"name": n, "ok": True, "detail": f"{n} fine"} for n in names]} | kw


def judge(settings):
    return load(load_item("logctl", settings), "judge_logctl")  # the file-reading wrapper


def run_judge(settings, doc, tmp_path):
    ctx = type("C", (), {"dirs": [tmp_path]})()
    (tmp_path / "burger-logctl.json").write_text(json.dumps(doc))
    return judge(settings)(ctx)


def test_pass_names_the_process_and_counts(settings, tmp_path):
    ok, msg = run_judge(settings, result(), tmp_path)
    assert ok and "8 checks ok (pid 7 as root)" in msg


def test_a_failed_check_is_named(settings, tmp_path):
    doc = result()
    doc["checks"][3] = {"name": "one fd", "ok": False, "detail": "2 fds on /tmp/burger-fd.log"}
    ok, msg = run_judge(settings, doc, tmp_path)
    assert not ok and "one fd: 2 fds on /tmp/burger-fd.log" in msg and "7/8 ok" in msg


def test_stock_bennu_error_fails_cleanly(settings, tmp_path):
    doc = result(
        error="RuntimeError: no log control socket /tmp/bennu-0/*.7.sock for pid 7", checks=[]
    )
    ok, msg = run_judge(settings, doc, tmp_path)
    assert not ok and "no log control socket" in msg


def test_fewer_checks_than_expected_fails(settings, tmp_path):
    doc = result(expected=12)
    ok, msg = run_judge(settings, doc, tmp_path)
    assert not ok and "only 8 of 12 checks ran" in msg


def test_missing_result_file_fails(settings, tmp_path):
    ctx = type("C", (), {"dirs": [tmp_path]})()
    ok, msg = judge(settings)(ctx)
    assert not ok and "no burger-logctl.json" in msg


def test_script_compiles_has_no_dollar_and_never_runs_stock_binary_unguarded(settings):
    cmd = load_item("logctl", settings).config["vms"][0]["start"][0]["args"]
    assert "$" not in cmd
    body = cmd.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    compile(body, "logctl", "exec")
    # the socket test comes before the first use of the binary
    assert body.index("no log control socket") < body.index('[exe, "log"')
    # one result entry per check() call
    expected = int(re.search(r'"expected": (\d+)', body).group(1))
    assert expected == len(re.findall(r"\bcheck\(\s*\"", body)) == 12


def test_logctl_board_renders_one_cc_component(settings):
    assert_one_cc_component(settings, "logctl", "logctl", "rtu-1")
    assert len("ebwes-logctl") <= 15


def test_board_builds_the_pr_deb(settings):
    assert load_switchboard("logctl", settings).bennu["pr"] == 80
