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
    if rnd.get("base"):
        pytest.skip("site topology; see test_rtds_round")
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
    assert any("state established" in c["args"] for c in rtu["start"]) is not linux_only
    assert any("-Hltn" in c["args"] for c in rtu["start"])
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


def test_rtds_round(tmp_path, monkeypatch):
    for var in ("BURGER_RTDS_TOPOLOGY", "BURGER_RTDS_SCENARIO", "BURGER_RTDS_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    rnd = burger.load_round("rtds")
    assert set(rnd["unset"]) == {"BURGER_RTDS_TOPOLOGY", "BURGER_RTDS_SCENARIO"}
    with pytest.raises(SystemExit, match="BURGER_RTDS_SCENARIO"):
        burger.render(rnd, MAN, None, False, "/s")

    (tmp_path / "t.yaml").write_text(
        "apiVersion: phenix.sandia.gov/v1\nkind: Topology\nmetadata: {name: site}\nspec:\n"
        "  nodes:\n  - general: {hostname: pp1}\n    hardware: {os_type: linux, drives: [{image: p.qc2}]}\n"
        "    network: {interfaces: []}\n")
    (tmp_path / "s.yaml").write_text(
        "apiVersion: phenix.sandia.gov/v2\nkind: Scenario\nmetadata: {name: site}\nspec:\n"
        "  apps:\n  - {name: sceptre, assetDir: /phenix/topologies/site}\n  - {name: scorch, metadata: {}}\n")
    monkeypatch.setenv("BURGER_RTDS_TOPOLOGY", str(tmp_path / "t.yaml"))
    monkeypatch.setenv("BURGER_RTDS_SCENARIO", str(tmp_path / "s.yaml"))
    monkeypatch.setenv("BURGER_RTDS_PROVIDER", "pp1")
    rnd = burger.load_round("rtds")
    man = {**MAN, "bennu": {"pr": 58, "sha": "c" * 40, "wheel": "pybennu-6.0.0-py3-none-any.whl"}}
    topo, scen = burger.render(rnd, man, None, False, "/s")
    assert [a["name"] for a in scen["spec"]["apps"]] == ["sceptre", "scorch"]
    assert scen["spec"]["apps"][0]["assetDir"] == "/phenix/topologies/site"
    dsts = [i["dst"] for i in topo["spec"]["nodes"][0]["injections"]]
    assert dsts == ["/etc/phenix/startup/00-burger-bennu.sh", "/root/pybennu-6.0.0-py3-none-any.whl"]
    sc = scorch_of(scen)
    assert sc["components"][0]["name"] == "health" and sc["runs"][0]["name"] == "health"
    assert next(c for c in sc["components"] if c["type"] == "rtds")["metadata"]["hostname"] == "pp1"
    assert "pip3 install" in burger.bennu_startup(man) and "dpkg" not in burger.bennu_startup(man)
