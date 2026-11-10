import json

from burger.checks.hook import load
from burger.items import load_item
from tests.conftest import assert_one_cc_component


def result(**kw):
    base = {"conns": 1500, "slack": 10, "max_log_bytes": 5_000_000, "done": 1500, "error": None}
    base |= {"sockets_before": 12, "sockets_after": 12, "log_grew": 70_000, "answers": True}
    return base | kw


def judge(settings, **kw):
    return load(load_item("modbus-churn", settings), "judge")(result(**kw))


def test_pass_shows_the_numbers(settings):
    ok, msg = judge(settings)
    assert ok and "socket fds 12 -> 12" in msg


def test_leak_fails(settings):
    err = "connection 1009 of 1500 failed: x"
    ok, msg = judge(settings, sockets_after=1036, done=1008, error=err, answers=False)
    assert not ok
    assert "grew by 1024" in msg and "no longer answers" in msg and "connection 1009" in msg


def test_log_flood_fails(settings):
    ok, msg = judge(settings, log_grew=6_000_000)
    assert not ok and "stderr log grew" in msg


def test_slack_allows_a_few_fds(settings):
    assert judge(settings, sockets_after=22)[0]
    assert not judge(settings, sockets_after=23)[0]


def test_missing_result_file_fails_and_found_file_judged(settings, tmp_path):
    fn = load(load_item("modbus-churn", settings), "judge_churn")
    ctx = type("C", (), {"dirs": [tmp_path]})()
    ok, msg = fn(ctx)
    assert not ok and "no burger-churn.json" in msg
    (tmp_path / "burger-churn.json").write_text(json.dumps(result()))
    assert fn(ctx)[0]


def test_tunables_come_from_env(settings, monkeypatch):
    monkeypatch.setenv("BURGER_CHURN_CONNS", "2500")
    cmd = load_item("modbus-churn", settings).config["vms"][0]["start"][0]["args"]
    assert 'N = int("2500")' in cmd and "PORT, OUT = {route.ics_port}," in cmd
    assert "${" not in cmd and "$" not in cmd
    script = cmd.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0].replace("{route.ics_port}", "502")
    compile(script, "churn", "exec")


def test_churn_board_renders_one_cc_component(settings):
    assert_one_cc_component(settings, "fdleak", "modbus-churn", "rtu-1")
