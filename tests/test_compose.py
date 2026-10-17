import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from burger.compose import compose, health_component, item_locations
from burger.config import BurgerError, Settings
from burger.switchboard import all_switchboards, load_switchboard, resolve
from tests.conftest import MAN

GOLDEN = Path(__file__).parent / "golden"
STAGES = ("configure", "start", "stop", "cleanup")


def golden_scorch(name):
    doc = json.loads((GOLDEN / f"{name}.scenario.json").read_text())
    return next(a for a in doc["spec"]["apps"] if a["name"] == "scorch")["metadata"]


def res(settings, name, **kw):
    return resolve(load_switchboard(name, settings), settings, **kw)


def rtu1(health):
    return next(v for v in health["metadata"]["vms"] if v["hostname"] == "rtu-1")["start"]


def test_metadata_runs_match_old_membership(settings):
    comps, runs = compose(res(settings, "metadata"), MAN)
    old = golden_scorch("metadata")
    types = {c["name"]: c["type"] for c in comps}
    old_types = {c["name"]: c["type"] for c in old["components"]}
    main = next(r for r in runs if r["name"] == "metadata")
    old_main = next(r for r in old["runs"] if r["name"] == "metadata")
    for stage in STAGES:
        assert {types[n] for n in main.get(stage, [])} == {
            old_types[n] for n in old_main.get(stage, [])
        }
    assert main["start"][-1] == "disruption"
    assert "extras-erspan" in [r["name"] for r in runs]
    assert runs[0]["name"] == "health"


def test_pollers_need_background_and_stop(settings):
    r = res(settings, "metadata")
    r.items = [
        dataclasses.replace(i, background=False) if i.name == "hoststats" else i for i in r.items
    ]
    with pytest.raises(BurgerError, match="background: true"):
        compose(r, MAN)
    r = res(settings, "iperf")
    runs = [{**r.board.runs[0], "start": [*r.board.runs[0]["start"], "nope"]}]
    r.board = dataclasses.replace(r.board, runs=runs)
    with pytest.raises(BurgerError, match="unknown components"):
        compose(r, MAN)


def test_health_install_checks_byte_identical(settings):
    man = {**MAN, "bennu": {**MAN["bennu"], "wheel": "pybennu-6.0.0-py3-none-any.whl"}}
    ours = rtu1(health_component(res(settings, "bennu"), man))
    old = rtu1(golden_scorch("bennu")["components"][0])
    assert ours[:2] == old[:2]
    version = next(c for c in old if c["args"].startswith("dpkg-query"))
    assert ours[2] == version


def test_expect_compiles_to_grep(settings):
    checks = rtu1(health_component(res(settings, "bennu"), MAN))
    probe = next(c for c in checks if "bennu-probe" in c["args"])
    assert probe["args"].endswith("| grep -Eq lock")
    assert "validator" not in probe


def test_health_waterway_covers_old_checks(settings):
    args = "\n".join(c["args"] for c in rtu1(health_component(res(settings, "bennu"), MAN)))
    for want in (
        "-Hltn",
        "state established",
        "bennu-probe",
        "pgrep -f '(^|/)bennu-field-deviced?( |$)'",
    ):
        assert want in args
    linux = "\n".join(
        c["args"] for c in rtu1(health_component(res(settings, "bennu", linux_only=True), MAN))
    )
    assert "state established" not in linux


def test_explicit_runs_override(settings, monkeypatch):
    for v, f in (("SITE_TOPOLOGY", "topology"), ("SITE_SCENARIO", "scenario")):
        monkeypatch.setenv(f"BURGER_{v}", str(GOLDEN / f"site-{f}.yaml"))
    _, runs = compose(res(settings, "provider"), MAN)
    want = yaml.safe_load((settings.root / "switchboards/provider.yaml").read_text())["runs"]
    assert runs[-len(want) :] == want
    assert all(r["name"] == "health" for r in runs[: -len(want)])


def test_item_locations():
    runs = [
        {"name": "health", "start": ["health"]},
        {"name": "m", "start": ["a", "b"], "stop": ["a"]},
    ]
    assert item_locations(runs)["a"] == (1, "a")


def test_advisory_vm_checks_get_own_health_extras(settings):
    comps, runs = compose(res(settings, "pybennu"), MAN)
    hard = next(c for c in comps if c["name"] == "health")
    assert "pybennu-siren.log" not in json.dumps(hard)
    extra = next(c for c in comps if c["name"] == "health-siren")
    assert "pybennu-siren.log" in json.dumps(extra)
    run = next(r for r in runs if r["name"] == "extras-siren")
    assert run["start"] == ["health-siren"]


_ROOT = Settings.from_env(Path(__file__).resolve().parents[1])
WATERWAY = [b for b in all_switchboards(_ROOT) if load_switchboard(b, _ROOT).route == "waterway"]


@pytest.fixture(autouse=True)
def vm_password(monkeypatch):
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")  # ssh's scenario needs it


@pytest.mark.parametrize("linux_only", [False, True])
@pytest.mark.parametrize("name", WATERWAY)
def test_every_waterway_board_has_rtu_health(settings, name, linux_only):
    """The old per-round invariant: rtu-1 health serves Modbus (and is polled, with Windows)."""
    comps, runs = compose(res(settings, name, linux_only=linux_only), MAN)
    assert runs[0] == {"name": "health", "start": ["health"]}
    args = "\n".join(c["args"] for c in rtu1(comps[0]))
    assert "-Hltn" in args
    assert ("state established" in args) == (not linux_only)


def test_no_deb_waterway_asserts_bennu_installed(settings):
    stock = {**MAN, "bennu": None}
    old = next(c for c in rtu1(golden_scorch("baseline")["components"][0]) if "dpkg" in c["args"])
    for name in ("baseline", "iperf", "ssh"):
        assert old in rtu1(health_component(res(settings, name), stock))


def test_provider_health_is_power_solver_only(settings):
    comps, runs = compose(res(settings, "provider"), {**MAN, "bennu": None})
    assert runs[0]["name"] == "health"
    (vm,) = comps[0]["metadata"]["vms"]
    assert vm["hostname"] == "power-provider"
    assert [c["args"] for c in vm["start"]] == ["pgrep -f '(^|/)pybennu-power-solver( |$)'"]


def test_site_wheel_only_has_no_version_check(settings, monkeypatch):
    for v, f in (("SITE_TOPOLOGY", "topology"), ("SITE_SCENARIO", "scenario")):
        monkeypatch.setenv(f"BURGER_{v}", str(GOLDEN / f"site-{f}.yaml"))
    man = {**MAN, "bennu": {"pr": 59, "sha": "c" * 40, "wheel": "pybennu-1-py3-none-any.whl"}}
    assert "dpkg-query" not in json.dumps(health_component(res(settings, "rtds"), man))


def with_items(settings, name, *refs):
    board = load_switchboard(name, settings)
    board.items.update({r: True for r in refs})
    return resolve(board, settings)


def test_explicit_runs_must_place_enabled_items(settings):
    with pytest.raises(BurgerError, match=r"runs leave out enabled items \['pipe'\]"):
        compose(with_items(settings, "provider", "pipe"), MAN)


def test_explicit_runs_keep_advisory_extras(settings):
    comps, runs = compose(with_items(settings, "provider", "erspan", "bennu/siren"), MAN)
    by_name = {r["name"]: r for r in runs}
    assert "erspan" in json.dumps(by_name["extras-erspan"])
    assert by_name["extras-siren"]["start"] == ["health-siren"]
    assert "health-siren" in [c["name"] for c in comps]
    assert [r["name"] for r in runs[:2]] == ["health", "provider"]
