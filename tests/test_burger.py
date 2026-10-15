"""Offline checks for burger: render every round, judge status files.

    BURGER_WATERWAY=/path/to/sceptre-phenix-topologies/waterway python3 -m pytest tests
"""

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("burger", str(ROOT / "burger"))
spec = importlib.util.spec_from_loader("burger", loader)
burger = importlib.util.module_from_spec(spec)
loader.exec_module(burger)

WATERWAY = Path(os.environ.get("BURGER_WATERWAY", ROOT / "work/repos/topologies/waterway"))
needs_waterway = pytest.mark.skipif(not WATERWAY.is_dir(), reason="set BURGER_WATERWAY")
MAN = {"apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
       "bennu": {"pr": 58, "sha": "c" * 40, "deb_version": "6.0.1.abc1234"}, "topologies": "d" * 40}


def scorch_of(scen):
    return next(a for a in scen["spec"]["apps"] if a["name"] == "scorch")["metadata"]


@needs_waterway
@pytest.mark.parametrize("name", burger.all_rounds())
@pytest.mark.parametrize("linux_only", [False, True])
def test_render_every_round(name, linux_only):
    rnd = burger.load_round(name)
    man = MAN if rnd.get("bennu") else {**MAN, "bennu": None}
    topo, scen = burger.render(rnd, man, WATERWAY, linux_only, "/phenix/burger/x")
    assert topo["metadata"]["name"] == scen["metadata"]["name"] == rnd["exp"]
    assert scen["metadata"]["annotations"]["topology"] == rnd["exp"]
    assert burger.STOCK_ASSETS + "/" not in str(scen)
    hosts = {n["general"]["hostname"]: n for n in topo["spec"]["nodes"]}
    assert ("opc" in hosts) is not linux_only
    for h in burger.BENNU_NODES:
        injected = [i["dst"] for i in hosts[h].get("injections", [])]
        assert ("/etc/phenix/startup/00-burger-bennu.sh" in injected) is bool(man["bennu"])
    health = scorch_of(scen)["components"][0]
    rtu = next(v for v in health["metadata"]["vms"] if v["hostname"] == "rtu-1")
    assert any(":502" in c["args"] for c in rtu["start"]) is not linux_only
    if rnd.get("mgmt_vlan"):
        vlans = {i["vlan"] for n in topo["spec"]["nodes"] for i in n["network"]["interfaces"]}
        assert "MGMT" not in vlans and rnd["mgmt_vlan"] in vlans


def test_evaluate():
    spec_ = {"name": "r", "start": ["good", "boom", "gone", "noisy"]}
    results = {
        ("good", "start"): [{"return": "", "logs": ["[t] INFO : fine"]}],
        ("boom", "start"): [{"return": "Error occurred: results validation failed", "logs": []}],
        ("noisy", "start"): [{"return": "", "logs": ["[t] ERROR : odd"]}],
    }
    verdicts = {row[1]: row[3] for row in burger.evaluate(spec_, results)}
    assert verdicts == {"good": "ok", "boom": "FAIL", "gone": "FAIL", "noisy": "WARN"}
