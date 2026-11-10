import json
import re

import pytest

from burger.checks.hook import load
from burger.compose import compose
from burger.items import load_item
from burger.switchboard import load_switchboard, resolve


def script(settings, name):
    """The python the cc command runs on the RTU, with the route placeholder filled in."""
    cmd = load_item(name, settings).config["vms"][0]["start"][0]["args"]
    assert "$" not in cmd and "${" not in cmd
    body = cmd.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0].replace("{route.ics_port}", "502")
    compile(body, name, "exec")
    return body


def bad(**kw):
    cases = [{"name": n, "response": ""} for n in ("truncated request", "MBAP length 0", "x", "y")]
    base = {"max_log_bytes": 1_000_000, "error": None, "pid_before": 7, "pid_after": 7}
    return base | {"cases": cases, "log_grew": 900, "answers": True} | kw


def judge_bad(settings, **kw):
    return load(load_item("modbus-badframes", settings), "judge")(bad(**kw))


def test_badframes_pass(settings):
    ok, msg = judge_bad(settings)
    assert ok and "dropped" in msg and "4 cases" in msg


def test_badframes_answered_truncated_request_fails(settings):
    cases = [{"name": "truncated request", "response": "000100000005010302abcd"}]
    ok, msg = judge_bad(settings, cases=cases + bad()["cases"][1:])
    assert not ok and "answered a malformed frame: truncated request" in msg


@pytest.mark.parametrize(
    "kw,want",
    [
        ({"pid_after": None}, "pid 7 -> None"),
        ({"pid_after": 9}, "pid 7 -> 9"),
        ({"answers": False}, "no longer answers"),
        ({"log_grew": 2_000_000}, "stderr log grew"),
        ({"error": "RuntimeError: no bennu-field-device process"}, "no bennu-field-device"),
    ],
)
def test_badframes_failures(settings, kw, want):
    ok, msg = judge_bad(settings, **kw)
    assert not ok and want in msg


@pytest.mark.parametrize("item,tag", [("dnp3-accept", "dnp3"), ("iec104-masters", "iec104")])
def test_steps_judge(settings, tmp_path, item, tag):
    it = load_item(item, settings)
    fn = load(it, f"judge_{tag}")
    ctx = type("C", (), {"dirs": [tmp_path]})()
    assert f"no burger-{tag}.json" in fn(ctx)[1]
    (tmp_path / f"burger-{tag}.json").write_text(
        json.dumps({"ok": True, "error": None, "steps": ["a", "b"]})
    )
    assert fn(ctx) == (True, "a; b")
    (tmp_path / f"burger-{tag}.json").write_text(
        json.dumps({"ok": False, "error": "RuntimeError: prlimit not permitted", "steps": ["a"]})
    )
    ok, msg = fn(ctx)
    assert not ok and "prlimit not permitted" in msg and "after: a" in msg


def test_scripts_compile_and_use_spare_ports(settings):
    script(settings, "modbus-badframes")
    assert "PORT = 20999" in script(settings, "dnp3-accept")
    assert "PORT = 25404" in script(settings, "iec104-masters")
    for item in ("dnp3-accept", "iec104-masters"):
        s = script(settings, item)
        port = re.search(r"PORT = (\d+)", s).group(1)
        assert f"tcp://127.0.0.1:{port}<" in s


def test_robust_board_renders_the_three_components_on_the_right_rtus(settings):
    res = resolve(load_switchboard("robust", settings), settings)
    comps, runs = compose(res, {"bennu": {"deb_version": "1"}})
    where = {c["name"]: c["metadata"]["vms"][0]["hostname"] for c in comps if c["name"] != "health"}
    assert where == {"modbus-badframes": "rtu-1", "dnp3-accept": "rtu-2", "iec104-masters": "rtu-2"}
    started = {n for r in runs for n in r.get("start", [])}
    assert set(where) <= started
    assert len("ebwes-robust") <= 15
