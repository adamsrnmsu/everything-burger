import json

from burger.checks.base import CheckContext
from burger.checks.hook import load
from burger.compose import compose
from burger.items import load_item
from burger.switchboard import load_switchboard, resolve


def result(**kw):
    return {"tags": 800, "window_s": 20.0, "max_cycle_s": 2.0, "requests": 20,
            "tag_writes": 16000, "seconds_per_cycle": 1.03, "error": None, **kw}  # fmt: skip


def judge(settings, **kw):
    return load(load_item("field-device-bench", settings), "judge")(result(**kw))


def test_pass_shows_the_number(settings):
    ok, msg = judge(settings)
    assert ok
    assert "1.030 s per cycle at 800 tags" in msg


def test_over_limit_fails(settings):
    ok, msg = judge(settings, seconds_per_cycle=398.0)
    assert not ok
    assert "398.000 s" in msg


def test_too_few_cycles_fails(settings):
    # Before #55 a cycle took ~400 s, so one tag is written once in the window.
    ok, msg = judge(settings, seconds_per_cycle=None, requests=40, tag_writes=40)
    assert not ok
    assert "more than 20.0 s" in msg


def test_error_fails(settings):
    ok, msg = judge(settings, error="no WRITE reached the responder in 60 s")
    assert not ok
    assert "no WRITE" in msg


def test_hook_reads_the_received_file(settings, tmp_path):
    item = load_item("field-device-bench", settings)
    (tmp_path / "burger-bench.json").write_text(json.dumps(result()))
    ctx = CheckContext(item, [tmp_path], None, {}, tmp_path)
    assert load(item, "judge_bench")(ctx)[0]
    ctx.dirs = []
    assert not load(item, "judge_bench")(ctx)[0]


def test_tunables_come_from_env(settings, monkeypatch):
    monkeypatch.setenv("BURGER_BENCH_TAGS", "1234")
    cmd = load_item("field-device-bench", settings).config["vms"][0]["start"][0]["args"]
    assert 'N = int("1234")' in cmd
    assert "${" not in cmd


def test_bench_board_renders_one_cc_component(settings):
    res = resolve(load_switchboard("bench", settings), settings)
    comps, runs = compose(res, {"bennu": {"deb_version": "1"}})
    bench = next(c for c in comps if c["name"] == "field-device-bench")
    assert bench["type"] == "cc"
    assert bench["metadata"]["vms"][0]["hostname"] == "rtu-1"
    assert any(r.get("start") == ["field-device-bench"] for r in runs)
