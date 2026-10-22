import dataclasses
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from burger import cli, config, experiment
from burger import verify as v
from burger.cli import main
from burger.compose import compose
from burger.config import Settings
from burger.items import STAGES, load_item
from burger.switchboard import load_switchboard
from tests.fakes import FakeHost

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.usefixtures("vm_password")


@pytest.fixture(autouse=True)
def at_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BURGER_WORK", str(tmp_path))


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("burger ")


def test_list_shows_boards_and_items(capsys):
    assert main(["list"]) == 0
    text = capsys.readouterr().out
    assert "metadata" in text
    assert "bennu/field-device" in text
    assert "validate-only" in text


def test_list_shows_branch_boards(capsys, monkeypatch):
    b = dataclasses.replace(
        load_switchboard("baseline", Settings.from_env()),
        apps_prs=[159, 161],
        apps_branch={"branch": "integration", "remote": "fork"},
        bennu={"branch": "main", "remote": "upstream"},
    )
    monkeypatch.setattr(cli, "all_switchboards", lambda s: ["x"])
    monkeypatch.setattr(cli, "load_switchboard", lambda n, s: b)
    assert main(["list"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "integration (159, 161)" in text and "bennu main" in text


def test_synthetic_manifest_names_the_branch_source():
    b = dataclasses.replace(
        load_switchboard("baseline", Settings.from_env()),
        apps_branch={"branch": "integration", "remote": "fork"},
        bennu={"branch": "main", "remote": "upstream"},
    )
    man = cli._synthetic_manifest(b)
    assert man["apps"]["branch"] == "integration" and man["apps"]["remote"] == "fork"
    assert man["bennu"]["branch"] == "main" and "pr" not in man["bennu"]


@pytest.mark.parametrize("kind", ["scorch", "app", "bennu"])
def test_new_template_loads(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(config, "_checkout", lambda: tmp_path)
    assert main(["new", kind, "thing"]) == 0
    path = tmp_path / cli.KIND_DIR[kind] / "thing.yaml"
    item = load_item(
        f"{cli.KIND_DIR[kind]}/thing" if kind != "scorch" else "thing", Settings.from_env()
    )
    assert item.path == path
    assert item.name == "thing"
    assert item.routes == []


def test_new_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "_checkout", lambda: tmp_path)
    assert main(["new", "scorch", "thing"]) == 0
    assert main(["new", "scorch", "thing"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_new_rejects_path_names(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_checkout", lambda: tmp_path)
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
    monkeypatch.setattr(cli, "Host", lambda *_a: host)
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
    monkeypatch.setattr(cli, "Host", lambda *_a: host)
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


# ---------------------------------------------------------------- site profile (preflight, CLI)

SITE = """
ca_certs: [{ca}]
mirrors:
  registry: reg.site/
  pypi: https://pypi.site/simple
  apt: https://apt.site/ubuntu
  go: https://go.site
  git: {{"https://github.com/": "https://gitlab.site/gh/"}}
"""


def _site_env(tmp_path, monkeypatch, extra=""):
    cert = tmp_path / "ca.pem"
    cert.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n")
    cfg = tmp_path / "site.yaml"
    cfg.write_text(SITE.format(ca=cert) + extra)
    monkeypatch.setenv("BURGER_SITE_CONFIG", str(cfg))
    return cfg


def _preflight_host(monkeypatch, site):
    host = FakeHost(site=site)
    host.api = lambda _p: {}
    monkeypatch.setattr(cli, "Host", lambda *_a: host)
    return host


def test_preflight_site_rows(monkeypatch, tmp_path, capsys):
    from burger.site import load_site

    cfg = _site_env(tmp_path, monkeypatch)
    host = _preflight_host(monkeypatch, load_site())
    probes = {}

    def fake_probe(url, site, bundle, timeout=15):
        probes[url] = bundle
        return "connection refused" if "apt.site" in url else None

    monkeypatch.setattr(cli, "probe", fake_probe)
    main(["--quiet", "preflight", "--linux-only"])
    out = capsys.readouterr().out
    assert f"site file {cfg}" in out.replace("\n", "") or "site file" in out
    assert "CA bundle (1 certs)" in out
    assert "registry mirror" in out and "pypi mirror" in out and "go mirror" in out
    assert "apt mirror" in out and "connection refused" in out
    assert "git mirror" in out
    assert ["docker", "pull", "reg.site/ubuntu:22.04"] in host.calls
    assert any(c[0] == "git" and "ls-remote" in c for c in host.calls)
    assert "https://apt.site/ubuntu/dists/jammy/Release" in probes


def test_preflight_gosumdb_off_warns(monkeypatch, tmp_path, capsys):
    from burger.site import load_site

    _site_env(tmp_path, monkeypatch, '  gosumdb: "off"\n')
    _preflight_host(monkeypatch, load_site())
    monkeypatch.setattr(cli, "probe", lambda *_a, **_k: None)
    main(["--quiet", "preflight", "--linux-only"])
    out = capsys.readouterr().out
    assert "GOSUMDB=off" in out and "WARN" in out


def test_preflight_no_site_no_rows(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("BURGER_SITE_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _preflight_host(monkeypatch, None)
    main(["--quiet", "preflight", "--linux-only"])
    assert "site file" not in capsys.readouterr().out


def test_bad_site_file_one_line(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "site.yaml"
    cfg.write_text("mirror: {}\n")
    monkeypatch.setenv("BURGER_SITE_CONFIG", str(cfg))
    assert main(["--quiet", "list"]) == 1
    err = capsys.readouterr().err
    assert "'mirror'" in err and "Traceback" not in err


def test_main_passes_the_site_to_host(monkeypatch, tmp_path):
    _site_env(tmp_path, monkeypatch)
    seen = {}

    def capture(settings, site=None):
        seen["site"] = site
        return FakeHost(site=site)

    monkeypatch.setattr(cli, "Host", capture)
    main(["--quiet", "list"])
    assert seen["site"] is not None and seen["site"].mirrors["registry"] == "reg.site/"


def test_preflight_redacts_mirror_tokens(monkeypatch, tmp_path, capsys):
    from burger.site import load_site

    cert = tmp_path / "ca.pem"
    cert.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n")
    cfg = tmp_path / "site.yaml"
    cfg.write_text(
        f"ca_certs: [{cert}]\nmirrors:\n  pypi: https://u:tok@pypi.site/simple\n"
        "  git: {'https://github.com/': 'https://u:tok@git.site/'}\n"
    )
    monkeypatch.setenv("BURGER_SITE_CONFIG", str(cfg))
    host = _preflight_host(monkeypatch, load_site())
    host.responses["git"] = subprocess.CompletedProcess(
        [], 128, "", "fatal: https://u:tok@git.site/"
    )
    monkeypatch.setattr(cli, "probe", lambda *_a, **_k: None)
    main(["--quiet", "preflight", "--linux-only"])
    assert "tok" not in capsys.readouterr().out


def _site_rows_of(host):
    from burger.checks import Row

    rows = []

    def chk(label, ok, hint=""):
        rows.append(Row("-", label, "ok" if ok else "FAIL", "" if ok else hint, []))

    cli._site_rows(host, rows, chk)
    return rows


def test_preflight_fails_a_bundle_that_cannot_verify_a_public_host(monkeypatch, tmp_path):
    from burger.site import load_site

    _site_env(tmp_path, monkeypatch)
    host = FakeHost(site=load_site())
    seen = []

    def fake_probe(url, site, bundle, timeout=15):
        seen.append(url)
        if "pypi.org" in url:
            return "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"

    monkeypatch.setattr(cli, "probe", fake_probe)
    (row,) = [r for r in _site_rows_of(host) if "pypi.org" in r.check]
    assert "https://pypi.org/simple/" in seen
    assert row.verdict == "FAIL"
    assert "ca-certificates.crt" in row.reason and "whole trust store" in row.reason


def test_preflight_unreachable_public_host_only_warns(monkeypatch, tmp_path):
    from burger.site import load_site

    _site_env(tmp_path, monkeypatch)
    host = FakeHost(site=load_site())
    monkeypatch.setattr(
        cli, "probe", lambda url, *_a, **_k: "timed out" if "pypi.org" in url else None
    )
    (row,) = [r for r in _site_rows_of(host) if "pypi.org" in r.check]
    assert row.verdict == "WARN" and "ca-certificates.crt" not in row.reason
