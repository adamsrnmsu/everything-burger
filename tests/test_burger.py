"""Offline checks for burger: render every round, judge status files.

    BURGER_TOPOLOGIES=/path/to/sceptre-phenix-topologies python3 -m pytest tests
"""

import importlib.machinery
import copy
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("burger", str(ROOT / "burger"))
spec = importlib.util.spec_from_loader("burger", loader)
burger = importlib.util.module_from_spec(spec)
loader.exec_module(burger)

TOPOLOGIES = Path(os.environ.get("BURGER_TOPOLOGIES", ROOT / "work/repos/topologies"))
needs_topologies = pytest.mark.skipif(not (TOPOLOGIES / "waterway").is_dir(), reason="set BURGER_TOPOLOGIES")
MAN = {"apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
       "bennu": {"pr": 58, "sha": "c" * 40, "deb_version": "6.0.1.abc1234"}, "topologies": "d" * 40}


def scorch_of(scen):
    return next(a for a in scen["spec"]["apps"] if a["name"] == "scorch")["metadata"]


@needs_topologies
@pytest.mark.parametrize("name", burger.all_rounds())
@pytest.mark.parametrize("linux_only", [False, True])
def test_render_every_round(name, linux_only, monkeypatch):
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")  # the ssh round requires it
    rnd = burger.load_round(name)
    if rnd.get("base"):
        pytest.skip("site topology; see test_rtds_round")
    man = MAN if rnd.get("bennu") else {**MAN, "bennu": None}
    where = burger.topology(rnd)
    topo, scen = burger.render(rnd, man, TOPOLOGIES / where, linux_only, "/phenix/burger/x")
    assert topo["metadata"]["name"] == scen["metadata"]["name"] == rnd["exp"]
    assert scen["metadata"]["annotations"]["topology"] == rnd["exp"]
    stock = f"/phenix/topologies/{where}"
    assert stock + "/" not in json.dumps(topo) and stock not in json.dumps(scen)  # both rewritten
    hosts = {n["general"]["hostname"]: n for n in topo["spec"]["nodes"]}
    assert "${" not in json.dumps(scorch_of(scen))  # SCORCH templating eats it
    debs = (rnd.get("debs") or {}).get("nodes") or []
    for h, n in hosts.items():
        assert ("/etc/phenix/startup/02-burger-debs.sh" in str(n.get("injections"))) is (h in debs)
    if where != "waterway":
        for h in rnd.get("cc_nodes") or burger.bennu_nodes(rnd):
            assert "/etc/phenix/startup/01-burger-miniccc.sh" in str(hosts[h]["injections"])
        health = scorch_of(scen)["components"][0]
        vms = {v["hostname"]: v["start"] for v in health["metadata"]["vms"]}
        own = {v["hostname"]: v["start"] for v in rnd["health"]}
        assert set(own) <= set(vms)
        for h, cmds in vms.items():  # install checks first, then the round's own
            installed = man["bennu"] and h in burger.bennu_nodes(rnd)
            assert [c["args"] for c in cmds[len(cmds) - len(own.get(h, [])):]] == own.get(h, [])
            assert any("burger-bennu.log" in c["args"] for c in cmds) is bool(installed)
        assert not set(rnd.get("drop_nodes") or []) & set(hosts)
        return
    assert ("opc" in hosts) is not linux_only
    for h in burger.BENNU_NODES:
        injected = [i["dst"] for i in hosts[h].get("injections", [])]
        assert ("/etc/phenix/startup/00-burger-bennu.sh" in injected) is bool(man["bennu"])
        assert "/etc/phenix/startup/01-burger-miniccc.sh" in injected  # every round, bennu or not
    if "monitor" in hosts:
        assert "/root/burger-miniccc" in [i["dst"] for i in hosts["monitor"]["injections"]]
        mirror = next(i for i in hosts["monitor"]["network"]["interfaces"] if i["name"] == "mirror")
        assert (mirror["vlan"], mirror["address"], mirror["mask"]) == ("mirror", "10.248.171.254", 24)
    assert not any("burger-miniccc" in str(n.get("injections")) for n in hosts.values()
                   if n["hardware"]["os_type"] == "windows")
    health = scorch_of(scen)["components"][0]
    rtu = next(v for v in health["metadata"]["vms"] if v["hostname"] == "rtu-1")
    assert any("state established" in c["args"] for c in rtu["start"]) is not linux_only
    assert any("-Hltn" in c["args"] for c in rtu["start"])
    if rnd.get("mgmt_vlan"):
        vlans = {i["vlan"] for n in topo["spec"]["nodes"] for i in n["network"]["interfaces"]}
        assert "MGMT" not in vlans and rnd["mgmt_vlan"] in vlans


def test_pollers_need_background_and_stop(monkeypatch):
    docs = {"t": {"metadata": {}, "spec": {"nodes": []}}, "s": {"metadata": {}, "spec": {"apps": []}}}
    monkeypatch.setattr(burger, "load_yaml", lambda p: copy.deepcopy(docs[p]))
    hs = {"name": "hs", "type": "hoststats", "metadata": {}}
    run = {"name": "r", "start": ["hs"], "stop": ["hs"]}
    rnd = {"name": "x", "exp": "ebwes-x", "base": {"topology": "t", "scenario": "s"}, "unset": [],
           "scorch": {"components": [hs], "runs": [run]}}
    with pytest.raises(SystemExit, match="background: true"):
        burger.render(rnd, {"bennu": None}, None, False, "/s")
    hs["background"] = True
    burger.render(rnd, {"bennu": None}, None, False, "/s")
    run["stop"] = []
    with pytest.raises(SystemExit, match="list it in stop"):
        burger.render(rnd, {"bennu": None}, None, False, "/s")


def test_evaluate():
    spec_ = {"name": "r", "start": ["good", "boom", "gone", "noisy"]}
    results = {
        ("good", "start"): [{"return": "", "logs": ["[t] INFO : fine"]}],
        ("boom", "start"): [{"return": "Error occurred: results validation failed", "logs": []}],
        ("noisy", "start"): [{"return": "", "logs": ["[t] ERROR : odd"]}],
    }
    verdicts = {row[1]: row[3] for row in burger.evaluate(spec_, results)}
    assert verdicts == {"good": "ok", "boom": "FAIL", "gone": "FAIL", "noisy": "WARN"}

    # a background component's missing start status is ok; its stop is still judged
    bg = {"name": "r", "start": ["poll"], "stop": ["poll"]}
    rows = {(r[1], r[2]): r[3] for r in burger.evaluate(bg, {}, {"poll"})}
    assert rows == {("poll", "start"): "ok", ("poll", "stop"): "FAIL"}


def test_collect_skips_non_status_json(tmp_path, monkeypatch):
    def fake_fetch(src, dst):
        loop = Path(dst) / "erspan" / "loop-0-count-0"
        loop.mkdir(parents=True)
        (loop / "meta.json").write_text("[1, 2]")
        (loop / "start.json").write_text('{"component": "erspan", "stage": "start", "start": "2026"}')
        return True

    monkeypatch.setattr(burger, "WORK", tmp_path)
    monkeypatch.setattr(burger, "fetch_from_phenix", fake_fetch)
    results = burger.collect({"name": "x", "exp": "ebwes-x"}, 0)
    assert list(results) == [("erspan", "start")]


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
    assert dsts == ["/etc/phenix/startup/00-burger-bennu.sh", "/root/pybennu-6.0.0-py3-none-any.whl",
                    "/root/burger-miniccc", "/etc/phenix/startup/01-burger-miniccc.sh"]
    sc = scorch_of(scen)
    assert sc["components"][0]["name"] == "health" and sc["runs"][0]["name"] == "health"
    assert next(c for c in sc["components"] if c["type"] == "rtds")["metadata"]["hostname"] == "pp1"
    assert "pip3 install" in burger.bennu_startup(man) and "dpkg" not in burger.bennu_startup(man)


def test_fix_routers(monkeypatch):
    sent = []

    def fake_mm(exp, *a):
        sent.append(" ".join(a))
        out = "r1,u1\nrtu-1,u9\n" if a[-1].endswith("vm info") else ""
        return burger.subprocess.CompletedProcess(a, 0, out, "")

    monkeypatch.setattr(burger, "MM_CTR", "minimega")
    monkeypatch.setattr(burger, "mm", fake_mm)
    iface = lambda addr: {"address": addr, "mask": 24} if addr else {"name": "mgmt"}
    topo = {"spec": {"nodes": [
        {"general": {"hostname": "r1"}, "hardware": {"drives": [{"image": "minirouter.qc2"}]},
         "network": {"interfaces": [iface("10.1.2.1"), iface(None), iface("10.1.1.1")]}},
        {"general": {"hostname": "r2"}, "type": "Router", "network": {"interfaces": []}},
        {"general": {"hostname": "rtu-1"}, "hardware": {"os_type": "linux", "drives": [{"image": "bennu.qc2"}]},
         "network": {"interfaces": [iface("10.1.1.101")]}},
    ]}}
    monkeypatch.setattr(burger, "vm_uuids", lambda exp, want: {"r1": "u1", "rtu-1": "u9"})
    burger.fix_routers("e", topo)
    execs = [c for c in sent if c.startswith("cc exec")]
    assert sent[0] == "cc filter uuid=u1" and sent[-1] == "clear cc filter"
    assert len(execs) == 2  # eth0 and eth2; eth1 has no address, r2 is not listed, rtu-1 is no router
    assert execs[0].startswith('cc exec /bin/sh -c "ip link set eth0 up; ip addr add 10.1.2.1/24 dev eth0')
    assert "${" not in execs[0] and "replace" not in execs[0]
    assert "eth2" in execs[1] and "10.1.1.1/24" in execs[1]

    sent.clear()
    monkeypatch.undo()
    monkeypatch.setattr(burger, "mm", fake_mm)
    assert burger.vm_uuids("e", {"r1"}) == {"r1": "u1", "rtu-1": "u9"}
    assert sent == [".csv true .headers false .annotate false .columns name,uuid vm info"]

    sent.clear()
    monkeypatch.setattr(burger, "MM_CTR", "")
    burger.fix_routers("e", topo)
    assert sent == []


def test_scorch_settle_is_a_hard_cap(monkeypatch):
    clock = {"now": 0.0}
    attempts = []

    class FakeTime:
        time = staticmethod(lambda: clock["now"])

        @staticmethod
        def sleep(n):
            clock["now"] += n

    def fake_phenix(*a, **kw):
        attempts.append(clock["now"])
        clock["now"] += 300  # an unregistered VM costs a full cc client grace
        return burger.subprocess.CompletedProcess(a, 0, None, "")

    monkeypatch.setattr(burger, "time", FakeTime)
    monkeypatch.setattr(burger, "phenix", fake_phenix)
    monkeypatch.setattr(burger, "load_round", lambda n: {"name": n, "exp": "ebwes-x"})
    monkeypatch.setattr(burger, "manifest", lambda n: {})
    monkeypatch.setattr(burger, "state_of", lambda r: {"linux_only": False})
    monkeypatch.setattr(burger, "runs_of", lambda r, m, l: ([{"name": "health", "start": ["health"]}], set()))
    monkeypatch.setattr(burger, "collect", lambda r, i, since="": {})  # never healthy

    burger.cmd_scorch(burger.argparse.Namespace(round="x", run=None, settle=1000))
    # the clock starts when the first attempt ends (300): deadline 1300; a
    # fourth attempt at 1080 would end at 1380, so three fit
    assert attempts == [0, 360, 720]
    assert clock["now"] <= 1300

    attempts.clear()
    clock["now"] = 0.0
    burger.cmd_scorch(burger.argparse.Namespace(round="x", run=None, settle=0))
    assert attempts == [0]  # the first attempt always runs


@needs_topologies
def test_pybennu_round():
    rnd = burger.load_round("pybennu")
    man = {**MAN, "bennu": {**MAN["bennu"], "pr": 59, "wheel": "pybennu-6.0.0-py3-none-any.whl",
                            "wheels": ["loguru-0.7.3-py3-none-any.whl"]}}
    topo, scen = burger.render(rnd, man, TOPOLOGIES / "soap", False, "/s")
    hosts = {n["general"]["hostname"]: n for n in topo["spec"]["nodes"]}
    assert set(hosts) == {"power-provider", "rtu-1", "rtu-2", "rtu-3", "rtu-4", "siren"}
    apps = {a["name"]: a for a in scen["spec"]["apps"]}
    assert "mirror" not in apps  # its only host, netmonitor, is dropped
    assert "ignition" not in [h["hostname"] for h in apps["sceptre"]["hosts"]]
    dsts = [i["dst"] for i in hosts["power-provider"]["injections"]]
    assert "/root/loguru-0.7.3-py3-none-any.whl" in dsts and "/root/burger-bennu.deb" in dsts
    start = burger.bennu_startup(man)
    assert "/root/loguru-0.7.3-py3-none-any.whl /root/pybennu-6.0.0-py3-none-any.whl" in start
    health = scorch_of(scen)["components"][0]
    pp = next(v for v in health["metadata"]["vms"] if v["hostname"] == "power-provider")
    pip_check = next(c for c in pp["start"] if "Successfully" in c.get("validator", ""))
    for line, ok in (("Successfully installed loguru-0.7.3 pybennu-6.0.0", True),
                     ("Successfully installed pybennu-6.0.0", True), ("ERROR: no matching wheel", False)):
        res = subprocess.run(["bash", "-c", pip_check["validator"], "-", "pp"], input=line, text=True,
                             capture_output=True)
        assert (res.returncode == 0) is ok, line
    assert scorch_of(scen)["runs"][-1]["name"].startswith("extras")  # siren is advisory
