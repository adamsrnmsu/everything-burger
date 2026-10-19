import json

from burger.checks.base import CheckContext
from burger.checks.hook import load, python
from burger.compose import compose
from burger.items import load_item
from burger.switchboard import load_switchboard, resolve


def result(**kw):
    both = {"load_a": {"before": True, "after": False}, "load_b": {"before": True, "after": False}}
    return {"skipped": None, "error": None, "write": "ACK=ok", "devices": both, **kw}


def judge(settings, **kw):
    return load(load_item("opendss-batched-write", settings), "judge")(result(**kw))


def test_both_changed_passes(settings):
    ok, msg = judge(settings)
    assert ok
    assert "2 devices" in msg


def test_unchanged_device_fails_by_name(settings):
    # stock main: only the last device in the batch reaches OpenDSS
    devices = {
        "load_a": {"before": True, "after": True},
        "load_b": {"before": True, "after": False},
    }
    ok, msg = judge(settings, devices=devices)
    assert ok is False
    assert "load_a" in msg
    assert "load_b" not in msg.split("(write")[0]


def test_error_and_empty_fail(settings):
    assert judge(settings, error="RuntimeError: boom")[0] is False
    assert judge(settings, devices={})[0] is False


def test_skipped_is_none(settings):
    ok, msg = judge(settings, skipped="opendssdirect not installed on bennu.qc2", devices={})
    assert ok is None
    assert "opendssdirect" in msg


def test_hook_rows(settings, tmp_path):
    item = load_item("opendss-batched-write", settings)
    out = tmp_path / "burger-opendss.json"
    ctx = CheckContext(item, [tmp_path], None, {}, tmp_path)
    assert not load(item, "judge_opendss")(ctx)[0]  # nothing received
    out.write_text(json.dumps(result()))
    assert python("judge_opendss", ctx).verdict == "ok"
    out.write_text(json.dumps(result(skipped="x", devices={})))
    assert python("judge_opendss", ctx).verdict == "WARN"
    out.write_text(json.dumps(result(devices={"load_a": {"before": True, "after": True}})))
    assert python("judge_opendss", ctx).verdict == "FAIL"


def test_opendss_board_renders_one_cc_component(settings):
    res = resolve(load_switchboard("opendss", settings), settings)
    comps, runs = compose(res, {"bennu": {"deb_version": "1"}})
    comp = next(c for c in comps if c["name"] == "opendss-batched-write")
    assert comp["type"] == "cc"
    assert comp["metadata"]["vms"][0]["hostname"] == "power-provider"
    assert "${" not in comp["metadata"]["vms"][0]["start"][0]["args"]
    assert any(r.get("start") == ["opendss-batched-write"] for r in runs)
