import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from burger import cli, experiment
from burger import verify as v
from burger.cli import main
from burger.compose import compose
from burger.config import Settings
from burger.items import STAGES, load_item
from tests.fakes import FakeHost

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def at_root(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("BURGER_WORK", str(tmp_path))
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("burger ")


def test_list_shows_boards_and_items(capsys):
    assert main(["list"]) == 0
    text = capsys.readouterr().out
    assert "metadata" in text
    assert "bennu/field-device" in text
    assert "validate-only" in text


@pytest.mark.parametrize("kind", ["scorch", "app", "bennu"])
def test_new_template_loads(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    assert main(["new", kind, "thing"]) == 0
    path = tmp_path / cli.KIND_DIR[kind] / "thing.yaml"
    item = load_item(
        f"{cli.KIND_DIR[kind]}/thing" if kind != "scorch" else "thing", Settings.from_env()
    )
    assert item.path == path
    assert item.name == "thing"
    assert item.routes == []


def test_new_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["new", "scorch", "thing"]) == 0
    assert main(["new", "scorch", "thing"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_new_rejects_path_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["new", "app", "../evil"]) == 1
    assert not (tmp_path.parent / "evil.yaml").exists()


def test_check_offline(tmp_path, topologies, monkeypatch):
    monkeypatch.setenv("BURGER_TOPOLOGIES", str(topologies))
    assert main(["check", "pybennu"]) == 0
    check = tmp_path / "pybennu" / "check"
    assert json.loads((check / "topology.json").read_text())["metadata"]["name"] == "ebwes-pybennu"
    assert "scorch" in (check / "scenario.json").read_text()


def test_check_without_topologies_is_one_line(monkeypatch, capsys):
    monkeypatch.delenv("BURGER_TOPOLOGIES", raising=False)
    assert main(["check", "pybennu"]) == 1
    assert "BURGER_TOPOLOGIES" in capsys.readouterr().err


def test_error_is_one_line(capsys):
    assert main(["check", "nosuch"]) == 1
    err = capsys.readouterr().err
    assert "no switchboard 'nosuch'" in err
    assert "Traceback" not in err


def test_switchboard_name_cannot_traverse(capsys):
    assert main(["check", "../routes/site"]) == 1
    assert "no switchboard" in capsys.readouterr().err


def test_usage_error_exits_2(capsys):
    assert main(["bogus"]) == 2
    assert main([]) == 2


def test_banner_suppressed_when_not_tty(capsys):
    main(["list"])
    assert "____" not in capsys.readouterr().out


def test_uncaught_urlerror_is_one_red_line(monkeypatch, capsys):
    def boom(*_):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli.vcs, "prs", boom)
    assert main(["prs"]) == 1
    err = capsys.readouterr().err
    assert "connection refused" in err
    assert "Traceback" not in err


def test_preflight_api_failure_is_a_row_not_a_crash(monkeypatch, capsys):
    host = FakeHost()

    def refuse(_path):
        raise urllib.error.URLError("refused")

    host.api = refuse
    monkeypatch.setattr(cli, "Host", lambda _s: host)
    assert main(["preflight", "--linux-only"]) == 1
    assert "REST API" in capsys.readouterr().out


def test_results_table_keeps_bracket_text():
    from rich.console import Console

    from burger.checks import Row
    from burger.ui import results_table

    c = Console(width=120, record=True)
    c.print(results_table([Row("i", "c", "FAIL", "[docs] x [/path]", [])]))
    assert "[docs] x [/path]" in c.export_text()


def _fake_run_chain(monkeypatch, board):
    """`burger run <board>` on a FakeHost: real steps, fake docker builds and SCORCH results."""
    golden = ROOT / "tests" / "golden"
    monkeypatch.setenv("BURGER_SITE_TOPOLOGY", str(golden / "site-topology.yaml"))
    monkeypatch.setenv("BURGER_SITE_SCENARIO", str(golden / "site-scenario.yaml"))
    monkeypatch.setenv("BURGER_MINIMEGA_CONTAINER", "")
    monkeypatch.setenv("BURGER_SITE_PROVIDER", "pp1")
    # FakeHost's rev-parse prints nothing, so the synced apps head is "".
    done = subprocess.CompletedProcess([], 0, json.dumps({"apps": ""}), "")
    host = FakeHost({"docker exec phenix cat /tmp/burger/installed.json": done})
    exp = f"ebwes-{board}"
    host.api_responses = {
        f"/experiments/{exp}": {"running": True},
        f"/experiments/{exp}/vms": {"vms": [{"name": "a", "state": "running"}]},
    }

    def bennu_build(_host, _wt, out, man, *_a):
        man["bennu"]["wheel"] = "pybennu-1-py3-none-any.whl"

    def all_ok(res, settings, _host, i, since=""):
        _, runs = compose(res, json.loads((settings.work / board / "manifest.json").read_text()))
        return {(c, st): [{"return": ""}] for st in STAGES for c in runs[i].get(st, [])}

    monkeypatch.setattr(experiment, "build_bennu", bennu_build)
    monkeypatch.setattr(experiment, "collect", all_ok)
    monkeypatch.setattr(v, "collect", all_ok)
    monkeypatch.setattr(v, "run_checks", lambda *_a: [])  # no real SCORCH output files
    monkeypatch.setattr(cli, "Host", lambda _s: host)
    return host


def test_run_site_route_reaches_report_and_down(tmp_path, monkeypatch, capsys):
    host = _fake_run_chain(monkeypatch, "rtds")
    assert main(["--quiet", "run", "rtds", "--settle", "0", "--timeout", "1"]) == 0
    man = json.loads((tmp_path / "rtds" / "manifest.json").read_text())
    assert man["topologies"] is None
    report = (tmp_path / "rtds" / "report.md").read_text()
    assert "topologies: `site route`" in report and "**PASS**" in report
    assert host.calls[-1][3:6] == ["phenix", "config", "delete"]  # down ran last


def test_run_renders_before_build(tmp_path, monkeypatch, capsys):
    host = _fake_run_chain(monkeypatch, "rtds")
    monkeypatch.setenv("BURGER_SITE_PROVIDER", "nosuch-vm")
    assert main(["--quiet", "run", "rtds", "--settle", "0", "--timeout", "1"]) == 1
    assert "not in the topology" in capsys.readouterr().err
    assert not any(c[:2] in (["docker", "run"], ["docker", "build"]) for c in host.calls)


def test_user_facing_text_says_switchboards():
    import burger

    assert "round" not in cli._parser().description + burger.__doc__
    assert "rounds need" not in Path(cli.__file__).read_text()


def test_banner_draws_the_mascot():
    from io import StringIO

    from rich.console import Console

    from burger import ui

    buf = StringIO()
    ui.banner(Console(file=buf, force_terminal=True, color_system="truecolor", width=100))
    out = buf.getvalue()
    assert "|____/" in out  # the figlet B
    assert "\x1b[38;2;" in out  # the truecolor art, not plain text
    assert len(ui.MASCOT.plain.splitlines()) == 41
