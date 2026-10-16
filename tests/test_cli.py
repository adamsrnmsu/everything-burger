import json
import urllib.error
from pathlib import Path

import pytest

from burger import cli
from burger.cli import main
from burger.config import Settings
from burger.items import load_item

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
    from tests.fakes import FakeHost

    host = FakeHost()

    def refuse(_path):
        raise urllib.error.URLError("refused")

    host.api = refuse
    monkeypatch.setattr(cli, "Host", lambda _s: host)
    assert main(["preflight", "--linux-only"]) == 1
    assert "REST API" in capsys.readouterr().out
