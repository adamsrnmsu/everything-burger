import json

from burger.checks.hook import load
from burger.items import load_item
from tests.conftest import assert_one_cc_component


def one(host="rtu-1", grew=0, **kw):
    base = {"host": host, "seconds": 60.0, "grew": grew, "max_per_min": 2048, "error": None}
    return base | kw


def judge(settings, *results):
    return load(load_item("watcher-quiet", settings), "judge")(list(results))


def test_quiet_log_passes_with_the_rates(settings):
    ok, msg = judge(settings, one("rtu-1", 100), one("rtu-2", 0))
    assert ok and "rtu-1 100 B/min" in msg and "rtu-2 0 B/min" in msg


def test_flood_fails_and_names_the_host(settings):
    ok, msg = judge(settings, one("rtu-1"), one("rtu-2", 58_000))
    assert not ok and "rtu-2: watcher-stderr.log grew 58000 B/min" in msg


def test_rate_is_per_minute_over_the_sampled_time(settings):
    assert not judge(settings, one(grew=1500, seconds=30.0))[0]  # 3000 B/min
    assert judge(settings, one(grew=2048))[0]  # the bound itself passes


def test_missing_log_and_no_report_fail(settings):
    ok, msg = judge(settings, one(error="FileNotFoundError: watcher-stderr.log"))
    assert not ok and "FileNotFoundError" in msg
    assert not judge(settings)[0]


def test_reads_every_file_the_rtus_sent(settings, tmp_path):
    fn = load(load_item("watcher-quiet", settings), "judge_watcher")
    ctx = type("C", (), {"dirs": [tmp_path / "a", tmp_path / "b"]})()
    assert "no burger-watcher-<rtu>.json" in fn(ctx)[1]
    for d, grew in zip(ctx.dirs, (0, 99_999), strict=True):
        d.mkdir()
        (d / f"burger-watcher-{d.name}.json").write_text(json.dumps(one(d.name, grew)))
    ok, msg = fn(ctx)
    assert not ok and "b: watcher-stderr.log grew 99999" in msg and "a 0 B/min" in msg


def test_script_compiles_and_samples_both_rtus(settings):
    comp = load_item("watcher-quiet", settings).config["vms"]
    assert [v["hostname"] for v in comp] == ["{route.rtus[0]}", "{route.rtus[1]}"]
    cmd = comp[0]["start"][0]["args"]
    assert 'SECS = int("60")' in cmd and "$" not in cmd
    assert comp[1]["start"][0]["args"] == cmd  # the same script on both
    recv = [v["start"][1]["args"] for v in comp]
    assert recv == [
        "/tmp/burger-watcher-{route.rtus[0]}.json",
        "/tmp/burger-watcher-{route.rtus[1]}.json",
    ]
    compile(cmd.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0], "watcher", "exec")


def test_watcher_board_renders_the_cc_component(settings):
    assert_one_cc_component(settings, "watcher", "watcher-quiet", "rtu-1")
