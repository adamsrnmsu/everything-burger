import dataclasses
import json
import subprocess

import yaml

from burger import verify as v
from burger.checks import Row, run_checks
from burger.compose import health_checks
from burger.items import load_item
from burger.routes import load_route
from burger.switchboard import load_switchboard, resolve
from burger.vcs import board_dir
from tests.conftest import MAN, make_ctx
from tests.fakes import FakeHost


def setup(settings, monkeypatch, name="bennu", results=None, enable=(), linux_only=False):
    board = load_switchboard(name, settings)
    for item in enable:
        board.items[item] = True
    res = resolve(board, settings, linux_only)
    rd = board_dir(settings, name)
    rd.mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps({**MAN, "synced": "now"}))
    host = FakeHost(
        {
            "docker exec phenix cat /tmp/burger/installed.json": subprocess.CompletedProcess(
                [], 0, json.dumps({"apps": MAN["apps"]["head"]}), ""
            )
        },
        settings,
    )
    exp = res.board.exp
    host.api_responses = {
        f"/experiments/{exp}": {"running": True},
        f"/experiments/{exp}/vms": {"vms": [{"name": "a", "state": "running"}]},
    }
    monkeypatch.setattr(v, "collect", lambda *a, **k: {} if results is None else results)
    return res, host


def _health_rows(settings, monkeypatch, pick):
    """verify's vm rows when the health check pick(checks) fails, with the checks."""
    results = {}
    res, host = setup(settings, monkeypatch, results=results)
    checks = health_checks(res, MAN)
    bad = pick(checks)
    err = f"Error occurred: command '{bad[2]['args']}' failed: boom"
    results[("health", "start")] = [{"return": err}]
    rows = [r for r in v.verify(res, settings, host) if r.check.startswith("vm ")]
    assert len(rows) == len(checks)
    return checks, rows, bad


def test_health_attribution_unique_command(settings, monkeypatch):
    checks, rows, bad = _health_rows(
        settings,
        monkeypatch,
        lambda cs: next(c for c in cs if "simulink-provider(" in c[2]["args"]),
    )
    sp = [(c, r) for c, r in zip(checks, rows, strict=True) if c[1] == bad[1]]
    n = [c for c, _ in sp].index(bad)
    assert sp[n][1].verdict == "FAIL" and "boom" in sp[n][1].reason
    assert n and all((r.verdict, r.reason) == ("ok", "") for _, r in sp[:n])
    after = sp[n + 1 :]
    assert after and all((r.verdict, r.reason) == ("skipped", "not reached") for _, r in after)
    others = [r for c, r in zip(checks, rows, strict=True) if c[1] != bad[1]]
    assert others and all((r.verdict, r.reason) == ("skipped", "not confirmed") for r in others)


def test_health_attribution_shared_command_does_not_guess(settings, monkeypatch):
    checks, rows, bad = _health_rows(
        settings, monkeypatch, lambda cs: next(c for c in cs if "dpkg-query" in c[2]["args"])
    )
    shared = [r for c, r in zip(checks, rows, strict=True) if c[2] == bad[2]]
    assert len(shared) == 3
    for r in shared:
        assert r.verdict == "skipped"
        assert "one of rtu-1, rtu-2, simulink-provider" in r.reason and "boom" in r.reason
    rest = [r for c, r in zip(checks, rows, strict=True) if c[2] != bad[2]]
    assert rest and all((r.verdict, r.reason) == ("skipped", "not confirmed") for r in rest)


def test_health_warn_does_not_confirm(settings, monkeypatch):
    results = {("health", "start"): [{"return": "", "logs": ["x ERROR y"]}]}
    res, host = setup(settings, monkeypatch, results=results)
    rows = [r for r in v.verify(res, settings, host) if r.check.startswith("vm ")]
    assert rows and {r.verdict for r in rows} == {"skipped"}


def test_health_ok_marks_vm_checks_ok(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, results={("health", "start"): [{"return": ""}]})
    rows = [r for r in v.verify(res, settings, host) if r.check.startswith("vm ")]
    assert rows and {r.verdict for r in rows} == {"ok"}


def test_report_paths_and_verdict(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch)
    rows = [Row("pcap", "files", "FAIL", "no output", ["/x/a.pcap"]), Row("a", "b", "ok", "", [])]
    out = v.render_report(res, MAN | {"synced": "t"}, {"linux_only": False}, rows)
    assert "| item | check | verdict | reason | paths |" in out
    assert "`/x/a.pcap`" in out
    assert out.rstrip().endswith("**FAIL**: 1 failing check(s)")


def test_advisory_not_failing(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch)
    rows = [Row("erspan", "status configure", "advisory-FAIL", "x", [])]
    out = v.render_report(res, MAN | {"synced": "t"}, {"linux_only": False}, rows)
    assert "**PASS**" in out and "**FAIL**" not in out


def test_models_yaml_includes_validate_only(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="integrate")  # integrate as shipped
    v.verify(res, settings, host)
    doc = yaml.safe_load((board_dir(settings, "integrate") / "out" / "models.yaml").read_text())
    assert "disruption-dos" in [c["name"] for c in doc["components"]]
    assert (board_dir(settings, "integrate") / "report.md").is_file()


def test_advisory_item_in_explicit_run_stays_advisory(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="iperf", enable=["erspan"])
    runs = [{**res.board.runs[0], "configure": ["iperf", "erspan"]}]
    res.board = dataclasses.replace(res.board, runs=runs)
    rows = [r for r in v.verify(res, settings, host) if r.item == "erspan"]
    assert rows and all(r.verdict == "advisory-FAIL" for r in rows)


def test_windows_checks_skipped_under_linux_only(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="harden", linux_only=True)
    assert "tcpdump -nn" not in json.dumps(health_checks(res, MAN))  # not in the health run
    rows = v.verify(res, settings, host)
    skipped = {
        (r.item, r.check.split(":")[0]) for r in rows if r.reason == "needs the Windows nodes"
    }
    assert skipped == {
        ("pcap", "pcap"),
        ("tcpdump", "pcap"),
        ("mm", "pcap"),
        ("mirror", "vm monitor"),
    }
    assert all(r.verdict == "skipped" for r in rows if r.reason == "needs the Windows nodes")


def test_windows_checks_run_with_windows(settings, monkeypatch):
    res, host = setup(settings, monkeypatch, name="harden")
    assert "tcpdump -nn" in json.dumps(health_checks(res, MAN))
    rows = v.verify(res, settings, host)
    assert not [r for r in rows if r.reason == "needs the Windows nodes"]
    assert next(r for r in rows if r.item == "tcpdump" and r.check == "pcap").verdict == "FAIL"


def test_report_names_a_branch_source():
    res = type("R", (), {"board": type("B", (), {"name": "n", "exp": "e"})()})()
    man = {
        **MAN,
        "apps": {
            "branch": "integration",
            "remote": "fork",
            "sha": "a" * 40,
            "head": "a" * 40,
            "prs": {},
        },
        "bennu": {"branch": "main", "remote": "upstream", "sha": "c" * 40},
    }
    text = v.render_report(res, man, {"linux_only": False}, [])
    assert "branch fork/integration" in text and "upstream/main" in text


def test_models_yaml_resolves_asset_dir(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch, name="integrate")
    comp = next(i for i in res.items if i.kind == "scorch")
    comp.config["script"] = "{asset_dir}/x.py"
    out = board_dir(settings, "integrate") / "m.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    v.models_yaml(res, out, "/stage")
    assert "/stage/assets/x.py" in out.read_text() and "{asset_dir}" not in out.read_text()


def _mitm_capture_rows(settings, tmp_path, route, packets):
    """mitm-capture's verify rows under --linux-only for a kali capture holding `packets`."""
    from burger.items import resolve_item
    from tests.test_pcap import classic, eth, ip_packet

    item = resolve_item(load_item("mitm-capture", settings), load_route(route, settings), None)
    ctx, loop = make_ctx(settings, tmp_path, item=item)
    ctx.route, ctx.linux_only = load_route(route, settings), True
    (loop / "kali.pcap").write_bytes(classic([eth(ip_packet(src=s, dst=d)) for s, d in packets]))
    return run_checks(item, ctx)


def test_mitm_capture_proves_interception_on_linux_poller_route(settings, tmp_path):
    opc, rtu = bytes([10, 1, 2, 50]), bytes([10, 1, 1, 101])
    both = [(opc, rtu), (rtu, opc)] * 10
    rows = _mitm_capture_rows(settings, tmp_path, "mitm-arm64", both)
    assert [r.verdict for r in rows] == ["ok"] * 3  # no skipped row: the poller is Linux
    rows = _mitm_capture_rows(settings, tmp_path / "b", "mitm-arm64", [(opc, rtu)] * 20)
    assert [r.verdict for r in rows] == ["ok", "ok", "FAIL"]  # replies never seen
    rows = _mitm_capture_rows(settings, tmp_path / "c", "mitm-arm64", [])
    assert "FAIL" in {r.verdict for r in rows}


def test_mitm_capture_pcap_still_skipped_on_windows_route(settings, tmp_path):
    rows = _mitm_capture_rows(settings, tmp_path, "mitm", [])
    assert [r.verdict for r in rows] == ["ok", "skipped"]  # unchanged: the pcap check needs Windows


# --- minimega rows ---

MM_SHA = "e" * 40
MM_LABELS = "docker inspect -f {{json .Config.Labels}} minimega"
MM_FILE = "docker exec minimega sha256sum /opt/minimega/lib/minimega.py"
MM_IN_PHENIX = "docker exec phenix python3 -c import hashlib, minimega"


def _mm_rows(
    settings, monkeypatch, want, have, hashes=("abc", "abc"), mm_ctr="minimega", images=(None, None)
):
    """verify's minimega rows: the manifest wants `want`, the container is labelled `have`;
    `images` are the manifest's and the container's image tags, when set."""
    if not mm_ctr:
        settings = dataclasses.replace(settings, mm_ctr="")
    res, host = setup(settings, monkeypatch)
    man = {**MAN, "synced": "now"}
    if want:
        man["minimega"] = {"pr": 1782, "sha": want, "from": "flag"}
        if images[0]:
            man["minimega"]["image"] = images[0]
    (board_dir(settings, "bennu") / "manifest.json").write_text(json.dumps(man))
    done = lambda out: subprocess.CompletedProcess([], 0, out, "")  # noqa: E731
    got = {"burger.minimega": have} if have else {}
    if images[1]:
        got["burger.minimega.image"] = images[1]
    host.responses[MM_LABELS] = done(json.dumps(got))
    host.responses[MM_FILE] = done(f"{hashes[0]}  /opt/minimega/lib/minimega.py\n")
    host.responses[MM_IN_PHENIX] = done(f"{hashes[1]}\n")
    rows = v.verify(res, settings, host)
    assert rows[1].check == "minimega container"  # right after the install row
    return [r for r in rows if r.check.startswith("minimega")]


def test_minimega_rows_ok(settings, monkeypatch):
    rows = _mm_rows(settings, monkeypatch, MM_SHA, MM_SHA)
    assert [(r.check, r.verdict, r.reason) for r in rows] == [
        ("minimega container", "ok", ""),
        ("minimega.py in phēnix", "ok", ""),
    ]


def test_stock_board_has_only_the_container_row(settings, monkeypatch):
    # nothing says a stock phēnix image and a stock minimega image carry the same minimega.py
    rows = _mm_rows(settings, monkeypatch, None, None, hashes=("abc", "xyz"))
    assert [(r.check, r.verdict) for r in rows] == [("minimega container", "ok")]


def test_minimega_container_still_stock_fails(settings, monkeypatch):
    row = _mm_rows(settings, monkeypatch, MM_SHA, None)[0]
    assert row.verdict == "FAIL"
    assert "stock" in row.reason and MM_SHA[:12] in row.reason
    assert "burger install bennu" in row.reason


def test_leftover_swap_fails_a_board_without_minimega(settings, monkeypatch):
    (row,) = _mm_rows(settings, monkeypatch, None, MM_SHA)
    assert row.verdict == "FAIL" and MM_SHA[:12] in row.reason and "stock" in row.reason


MM_PLAIN, MM_LAYERED = f"burger/minimega:{MM_SHA[:12]}", f"burger/minimega:{MM_SHA[:12]}-0123abcd"


def test_the_plain_build_of_the_right_commit_fails_a_layered_board(settings, monkeypatch):
    # burger-fr8: the commit matches, but the route's layer (arm64 kvm wrapper) is missing
    row = _mm_rows(settings, monkeypatch, MM_SHA, MM_SHA, images=(MM_LAYERED, MM_PLAIN))[0]
    assert row.verdict == "FAIL" and "0123abcd" in row.reason


def test_the_layered_build_passes_a_layered_board(settings, monkeypatch):
    row = _mm_rows(settings, monkeypatch, MM_SHA, MM_SHA, images=(MM_LAYERED, MM_LAYERED))[0]
    assert row.verdict == "ok"


def test_minimega_py_mismatch_fails(settings, monkeypatch):
    row = _mm_rows(settings, monkeypatch, MM_SHA, MM_SHA, hashes=("abc", "xyz"))[1]
    assert row.verdict == "FAIL" and "burger install bennu" in row.reason


def test_minimega_py_unreadable_is_not_ok(settings, monkeypatch):
    # two empty answers must not compare equal
    row = _mm_rows(settings, monkeypatch, MM_SHA, MM_SHA, hashes=("", ""))[1]
    assert row.verdict == "FAIL"


def test_minimega_rows_skipped_without_the_container(settings, monkeypatch):
    rows = _mm_rows(settings, monkeypatch, MM_SHA, None, mm_ctr="")
    assert [(r.verdict, r.reason) for r in rows] == [
        ("skipped", "BURGER_MINIMEGA_CONTAINER is empty")
    ] * 2


def test_report_names_the_minimega(settings, monkeypatch):
    res, _ = setup(settings, monkeypatch)
    man = MAN | {"synced": "t"}
    assert "- minimega: stock" in v.render_report(res, man, {"linux_only": False}, [])
    man["minimega"] = {"pr": 1782, "sha": "c" * 40, "from": "flag"}
    text = v.render_report(res, man, {"linux_only": False}, [])
    assert "- minimega: #1782 `cccccccccc` (flag)" in text
    man["minimega"] = {"branch": "foo", "remote": "fork", "sha": "c" * 40, "from": "board"}
    text = v.render_report(res, man, {"linux_only": False}, [])
    assert "- minimega: fork/foo `cccccccccc` (board)" in text
