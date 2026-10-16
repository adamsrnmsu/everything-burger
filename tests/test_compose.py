import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from burger.compose import compose, health_component, item_locations
from burger.config import BurgerError
from burger.switchboard import load_switchboard, resolve
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
    r = res(settings, "metadata")
    r.board = dataclasses.replace(r.board, runs=[{"name": "x", "start": ["nope"]}])
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
