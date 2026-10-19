import dataclasses
import os
from pathlib import Path

import pytest

from burger.checks import CheckContext
from burger.config import Settings
from burger.items import load_item
from burger.routes import load_route
from burger.switchboard import load_switchboard, resolve

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
ROOT_SETTINGS = Settings.from_env(ROOT)  # for collection-time parametrize; tests use `settings`

MAN = {
    "apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
    "bennu": {"pr": 58, "sha": "c" * 40, "deb_version": "6.0.1.abc1234"},
    "topologies": "d" * 40,
}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(ROOT_SETTINGS, work=tmp_path)


def res(settings, name="baseline", **kw):
    """The named switchboard, resolved."""
    return resolve(load_switchboard(name, settings), settings, **kw)


def make_ctx(settings, tmp_path, item=None, **over):
    """A CheckContext over one empty loop dir; `over` replaces Item fields."""
    item = dataclasses.replace(item or load_item("pcap", settings), **over)
    loop = tmp_path / "run-0" / item.name / "loop-0"
    loop.mkdir(parents=True, exist_ok=True)
    return CheckContext(item, [loop], load_route("soap", settings), {}, tmp_path), loop


@pytest.fixture
def vm_password(monkeypatch):
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")  # ssh's scenario needs it


@pytest.fixture
def site_env(monkeypatch):
    """What the ssh board and the site route need to resolve."""
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")
    monkeypatch.setenv("BURGER_SITE_TOPOLOGY", str(GOLDEN / "site-topology.yaml"))
    monkeypatch.setenv("BURGER_SITE_SCENARIO", str(GOLDEN / "site-scenario.yaml"))
    monkeypatch.setenv("BURGER_SITE_PROVIDER", "pp1")


@pytest.fixture
def topologies():
    path = os.environ.get("BURGER_TOPOLOGIES")
    if not path:
        pytest.skip("BURGER_TOPOLOGIES is not set")
    return Path(path)


@pytest.fixture(autouse=True)
def no_host_site_file(monkeypatch, tmp_path_factory):
    """Tests never pick up the machine's real site file (~/.config/burger/site.yaml)."""
    monkeypatch.delenv("BURGER_SITE_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
