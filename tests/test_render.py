import copy
import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from burger.bennu import bennu_startup
from burger.render import render
from burger.switchboard import all_switchboards, load_switchboard, resolve
from tests.conftest import GOLDEN, MAN, ROOT_SETTINGS, res

STAGE = "/phenix/burger/x"
NAMES = [n for n in (p.stem for p in (Path(__file__).parents[1] / "switchboards").glob("*.yaml"))]
CASES = [
    (n, lo)
    for n in sorted(NAMES)
    for lo in (False, True)
    if (GOLDEN / f"{n}{'.linux' if lo else ''}.topology.json").exists()
]
WHEEL = "pybennu-6.0.0-py3-none-any.whl"


pytestmark = pytest.mark.usefixtures("site_env")


def man_for(board):
    man = copy.deepcopy(MAN)
    if b := board.bennu:
        man["bennu"]["pr"] = b["pr"]
        if "pybennu" in b.get("install", ["deb"]):
            man["bennu"]["wheel"] = WHEEL
        if b.get("pip"):
            man["bennu"]["wheels"] = ["loguru-0.7.3-py3-none-any.whl"]
    else:
        man["bennu"] = None
    return man


def build(settings, topologies, name, linux_only, man=None):
    r = res(settings, name, linux_only=linux_only)
    where = None if r.route.base else topologies / r.route.topology_dir
    man = man or man_for(r.board)
    topo, scen = render(r, copy.deepcopy(man), where, STAGE, settings)
    return r, man, topo, scen


def golden(name, linux_only, kind):
    tag = f"{name}.linux" if linux_only else name
    return json.loads((GOLDEN / f"{tag}.{kind}.json").read_text())


def non_scorch(scen):
    return [a for a in scen["spec"]["apps"] if a["name"] != "scorch"]


def scorch_of(scen):
    return next(a for a in scen["spec"]["apps"] if a["name"] == "scorch")["metadata"]


def hostnames(topo):
    return {n["general"]["hostname"] for n in topo["spec"]["nodes"]}


def test_every_switchboard_has_a_golden_case():
    assert {n for n, _ in CASES} == set(all_switchboards(ROOT_SETTINGS))


@pytest.mark.parametrize("name,linux_only", CASES)
def test_golden_topology(settings, topologies, name, linux_only):
    _, _, topo, _ = build(settings, topologies, name, linux_only)
    want = golden(name, linux_only, "topology")
    if name == "provider":  # soap's drop_nodes now apply
        gone = {"ignition", "netmonitor", "attacker"}
        want["spec"]["nodes"] = [
            n for n in want["spec"]["nodes"] if n["general"]["hostname"] not in gone
        ]
    assert topo == want


@pytest.mark.parametrize("name,linux_only", CASES)
def test_golden_scenario_apps(settings, topologies, name, linux_only):
    _, _, _, scen = build(settings, topologies, name, linux_only)
    want = non_scorch(golden(name, linux_only, "scenario"))
    if name == "provider":
        want = [a for a in want if a["name"] != "mirror"]
        gone = {"ignition", "netmonitor", "attacker"}
        for a in want:
            if a.get("hosts"):
                a["hosts"] = [h for h in a["hosts"] if h["hostname"] not in gone]
    assert non_scorch(scen) == want


@pytest.mark.parametrize("name,linux_only", CASES)
def test_golden_scorch_components(settings, topologies, name, linux_only):
    _, _, _, scen = build(settings, topologies, name, linux_only)

    def shape(doc):
        return Counter(
            (c["type"], json.dumps(c["metadata"], sort_keys=True))
            for c in scorch_of(doc)["components"]
            if not c["name"].startswith("health") and c["name"] != "siren-log"
        )

    want = shape(golden(name, linux_only, "scenario"))
    assert shape(scen) == want


@pytest.mark.parametrize("name", sorted(NAMES))
def test_no_dollar_brace_in_scorch(settings, topologies, name):
    _, _, _, scen = build(settings, topologies, name, False)
    assert "${" not in json.dumps(scorch_of(scen))


def test_pybennu_render(settings, topologies):
    man = man_for(load_switchboard("pybennu", settings))
    _, man, topo, scen = build(settings, topologies, "pybennu", False, man)
    nodes = {n["general"]["hostname"]: n for n in topo["spec"]["nodes"]}
    assert set(nodes) == {"power-provider", "rtu-1", "rtu-2", "rtu-3", "rtu-4", "siren"}
    apps = {a["name"]: a for a in scen["spec"]["apps"]}
    assert "mirror" not in apps  # its only host, netmonitor, is dropped
    assert "ignition" not in [h["hostname"] for h in apps["sceptre"]["hosts"]]
    dsts = [i["dst"] for i in nodes["power-provider"]["injections"]]
    assert "/root/loguru-0.7.3-py3-none-any.whl" in dsts and "/root/burger-bennu.deb" in dsts
    assert "/root/loguru-0.7.3-py3-none-any.whl /root/" + WHEEL in bennu_startup(man)
    health = scorch_of(scen)["components"][0]
    pp = next(v for v in health["metadata"]["vms"] if v["hostname"] == "power-provider")
    pip_check = next(c for c in pp["start"] if "Successfully" in c.get("validator", ""))
    for line, ok in (
        ("Successfully installed loguru-0.7.3 pybennu-6.0.0", True),
        ("Successfully installed pybennu-6.0.0", True),
        ("ERROR: no matching wheel", False),
    ):
        out = subprocess.run(
            ["bash", "-c", pip_check["validator"], "-", "pp"],
            input=line,
            text=True,
            capture_output=True,
        )
        assert (out.returncode == 0) is ok, line
    siren = next(v for v in health["metadata"]["vms"] if v["hostname"] == "siren")
    assert not any("pybennu-siren.log" in c["args"] for c in siren["start"])
    comps = scorch_of(scen)["components"]
    extra = next(c for c in comps if c["name"] == "health-siren")
    assert "pybennu-siren.log" in json.dumps(extra["metadata"])
    run = next(r for r in scorch_of(scen)["runs"] if r["name"] == "extras-siren")
    assert run["start"] == ["health-siren"]


def test_site_route_render(settings, monkeypatch, tmp_path):
    (tmp_path / "t.yaml").write_text(
        "apiVersion: phenix.sandia.gov/v1\nkind: Topology\nmetadata: {name: site}\nspec:\n"
        "  nodes:\n  - general: {hostname: pp1}\n"
        "    hardware: {os_type: linux, drives: [{image: p.qc2}]}\n"
        "    network: {interfaces: []}\n"
    )
    (tmp_path / "s.yaml").write_text(
        "apiVersion: phenix.sandia.gov/v2\nkind: Scenario\nmetadata: {name: site}\nspec:\n"
        "  apps:\n  - {name: sceptre, assetDir: /phenix/topologies/site}\n"
        "  - {name: scorch, metadata: {}}\n"
    )
    monkeypatch.setenv("BURGER_SITE_TOPOLOGY", str(tmp_path / "t.yaml"))
    monkeypatch.setenv("BURGER_SITE_SCENARIO", str(tmp_path / "s.yaml"))
    man = {**MAN, "bennu": {"pr": 58, "sha": "c" * 40, "wheel": WHEEL}}
    res = resolve(load_switchboard("rtds", settings), settings)
    topo, scen = render(res, man, None, "/s", settings)
    assert [a["name"] for a in scen["spec"]["apps"]] == ["sceptre", "scorch"]
    assert scen["spec"]["apps"][0]["assetDir"] == "/phenix/topologies/site"
    dsts = [i["dst"] for i in topo["spec"]["nodes"][0]["injections"]]
    assert dsts == [
        "/etc/phenix/startup/00-burger-bennu.sh",
        "/root/" + WHEEL,
        "/root/burger-miniccc",
        "/etc/phenix/startup/01-burger-miniccc.sh",
    ]
    sc = scorch_of(scen)
    assert sc["components"][0]["name"] == "health" and sc["runs"][0]["name"] == "health"
    assert next(c for c in sc["components"] if c["type"] == "rtds")["metadata"]["hostname"] == "pp1"
    assert "pip3 install" in bennu_startup(man) and "dpkg" not in bennu_startup(man)
