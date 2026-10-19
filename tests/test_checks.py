import dataclasses
import json

from burger.checks import CHECKS, run_checks
from burger.checks.status import collect_status, evaluate
from burger.items import load_item
from tests.conftest import make_ctx


def test_evaluate():
    spec_ = {"name": "r", "start": ["good", "boom", "gone", "noisy"]}
    results = {
        ("good", "start"): [{"return": "", "logs": ["[t] INFO : fine"]}],
        ("boom", "start"): [{"return": "Error occurred: results validation failed", "logs": []}],
        ("noisy", "start"): [{"return": "", "logs": ["[t] ERROR : odd"]}],
    }
    verdicts = {row[1]: row[3] for row in evaluate(spec_, results)}
    assert verdicts == {"good": "ok", "boom": "FAIL", "gone": "FAIL", "noisy": "WARN"}

    # a background component's missing start status is ok; its stop is still judged
    bg = {"name": "r", "start": ["poll"], "stop": ["poll"]}
    rows = {(r[1], r[2]): r[3] for r in evaluate(bg, {}, {"poll"})}
    assert rows == {("poll", "start"): "ok", ("poll", "stop"): "FAIL"}


def test_collect_skips_non_status_json(tmp_path):
    loop = tmp_path / "erspan" / "loop-0-count-0"
    loop.mkdir(parents=True)
    (loop / "meta.json").write_text("[1, 2]")
    (loop / "start.json").write_text('{"component": "erspan", "stage": "start", "start": "2026"}')
    assert list(collect_status(tmp_path)) == [("erspan", "start")]
    assert collect_status(tmp_path, since="2027") == {}


def test_files_check_missing_is_fail(settings, tmp_path):
    ctx, _ = make_ctx(settings, tmp_path)
    r = CHECKS["files"]({"glob": "*.pcap"}, ctx)
    assert r.verdict == "FAIL"
    assert r.reason.startswith("no output found at")


def test_files_min_bytes(settings, tmp_path):
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "a.bin").write_bytes(b"x" * 10)
    assert CHECKS["files"]({"glob": "*.bin", "min_bytes": 10}, ctx).verdict == "ok"
    r = CHECKS["files"]({"glob": "*.bin", "min_bytes": 11}, ctx)
    assert r.verdict == "FAIL"
    assert str(loop / "a.bin") in r.paths


def test_json_equals_and_exists(settings, tmp_path):
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "o.json").write_text(json.dumps({"a": {"b": [1, "up"]}}))
    run = lambda **s: CHECKS["json"]({"glob": "o.json", **s}, ctx).verdict  # noqa: E731
    assert run(field="a.b.1", equals="up") == "ok"
    assert run(field="a.b.1", matches="^u") == "ok"
    assert run(field="a.b.1", equals="down") == "FAIL"
    assert run(field="a.c", exists=False) == "ok"
    assert run(field="a.c") == "FAIL"
    assert run(field="a.b", exists=False) == "FAIL"


def test_csv_min_rows(settings, tmp_path):
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "s.csv").write_text("t,v\n1,2\n3,4\n")
    run = lambda **s: CHECKS["csv"]({"glob": "s.csv", **s}, ctx)  # noqa: E731
    assert run(min_rows=2, columns=["v"]).verdict == "ok"
    assert run(min_rows=3).verdict == "FAIL"
    assert "no column x" in run(columns=["x"]).reason


def test_log_lacks(settings, tmp_path):
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "a.log").write_text("INFO fine\nERROR boom\n")
    run = lambda **s: CHECKS["log"]({"glob": "a.log", **s}, ctx)  # noqa: E731
    assert run(contains="fine").verdict == "ok"
    assert run(lacks="ERROR").verdict == "FAIL"
    assert "boom" in run(lacks="ERROR").reason
    assert run(lacks="CRITICAL").verdict == "ok"


def test_python_hook(settings, tmp_path):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "x.yaml").write_text("name: x\n")
    (tmp_path / "components" / "x.py").write_text("def chk(ctx):\n    return False, 'nope'\n")
    item = load_item("pcap", settings)
    item = dataclasses.replace(item, name="x", path=tmp_path / "components" / "x.yaml")
    ctx, _ = make_ctx(settings, tmp_path, item=item)
    r = CHECKS["python"]({"function": "chk"}, ctx)
    assert (r.verdict, r.reason) == ("FAIL", "nope")


def test_advisory_downgrade(settings, tmp_path):
    verify = [{"vm": {"run": "true"}}, {"files": {"glob": "*.pcap"}}]
    ctx, _ = make_ctx(settings, tmp_path, verify=verify, advisory=True)
    assert [r.verdict for r in run_checks(ctx.item, ctx)] == ["advisory-FAIL"]
    ctx = dataclasses.replace(ctx, item=dataclasses.replace(ctx.item, advisory=False))
    assert [r.verdict for r in run_checks(ctx.item, ctx)] == ["FAIL"]


def test_check_exception_is_fail_row(settings, tmp_path):
    item = dataclasses.replace(
        load_item("pcap", settings),
        verify=[
            {"files": {"glob": "["}},
            {"csv": {"glob": "*.csv", "columns": []}},
            {"files": {"glob": "*"}},
        ],
    )
    ctx, loop = make_ctx(settings, tmp_path, item=item)
    (loop / "a.csv").write_bytes(b"\xff\xfe\x00bad")
    rows = run_checks(item, ctx)
    assert len(rows) == 3
    assert [r.verdict for r in rows[:2]] == ["FAIL", "FAIL"]
    assert any("Error" in r.reason for r in rows[:2])


def test_pcap_rtu1_has_no_modbus(settings, tmp_path):
    from tests.test_pcap import classic, eth, ip_packet

    ctx, loop = make_ctx(settings, tmp_path)
    spec = {"function": "rtu1_has_no_modbus", "windows": True}
    assert CHECKS["python"](spec, ctx).reason == "no rtu-1 capture"

    (loop / "rtu-1-0.pcap").write_bytes(classic([eth(ip_packet(dport=80))]))
    assert CHECKS["python"](spec, ctx).verdict == "ok"

    (loop / "rtu-1-0.pcap").write_bytes(classic([eth(ip_packet(dport=502))]))
    assert CHECKS["python"](spec, ctx).verdict == "FAIL"


def test_status_error_lines_fail_the_metadata_log_check(settings, tmp_path):
    ctx, loop = make_ctx(settings, tmp_path)
    spec = {"glob": "*.json", "lacks": r"\] ERROR :"}  # as in switchboards/metadata.yaml
    (loop / "stop-x.json").write_text(json.dumps({"logs": ["[t] INFO : fine"]}))
    assert CHECKS["log"](spec, ctx).verdict == "ok"
    (loop / "stop-x.json").write_text(json.dumps({"logs": ["[t] ERROR : kill failed"]}))
    assert CHECKS["log"](spec, ctx).verdict == "FAIL"
