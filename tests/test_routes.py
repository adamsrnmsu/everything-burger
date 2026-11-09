import pytest

from burger.config import BurgerError
from burger.loader import load_yaml
from burger.routes import load_route


def test_expand_default_and_unset(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("a: ${A:-x}\nb: ${B}\n")
    doc, unset = load_yaml(f, env={})
    assert doc == {"a": "x", "b": "${B}"}
    assert unset == ["B"]


def test_unknown_field_names_file(tmp_path):
    f = tmp_path / "item.yaml"
    f.write_text("verfy: 1\n")
    with pytest.raises(BurgerError) as e:
        load_yaml(f, allowed={"verify"})
    assert "item.yaml" in str(e.value)
    assert "'verfy'" in str(e.value)


def test_waterway_route(settings):
    r = load_route("waterway", settings)
    assert r.values["ics_port"] == 502
    assert r.bennu_nodes == ["rtu-1", "rtu-2", "simulink-provider"]
    assert r.files == ("waterway-topology.yaml", "waterway-scenario.yaml")


def test_soap_route(settings):
    r = load_route("soap", settings)
    assert r.drop_nodes == ["ignition", "netmonitor", "attacker"]
    assert r.windows_images == []


def test_site_route_unset(settings, monkeypatch):
    for v in ("TOPOLOGY", "SCENARIO", "PROVIDER"):
        monkeypatch.delenv(f"BURGER_SITE_{v}", raising=False)
    r = load_route("site", settings)
    assert r.unset == ["BURGER_SITE_TOPOLOGY", "BURGER_SITE_SCENARIO"]
    assert r.values["provider"] == "power-provider"
    assert r.bennu_nodes == ["power-provider"]


def test_unknown_route(settings):
    with pytest.raises(BurgerError, match="mitm, mitm-arm64, site, soap, waterway"):
        load_route("waterwy", settings)


def test_waterway_arm64_route(settings):
    r = load_route("waterway-arm64", settings)
    assert r.unset == []
    assert r.base["topology"].endswith("waterway-arm64/waterway-topology.yaml")
    assert r.bennu_nodes == ["rtu-1", "rtu-2", "waterway-provider"]
    assert "bennu-arm64.qc2" in r.images and r.windows_images == []
    assert load_route("waterway", settings).images == []  # stock bennu.qc2, minirouter.qc2


def test_mitm_route_and_ettercap_board(settings):
    from burger.switchboard import load_switchboard

    r = load_route("mitm", settings)
    assert (r.topology_dir, r.files) == ("mitm", ("mitm-topology.yaml", "mitm-scenario.yaml"))
    assert load_switchboard("ettercap", settings).route == "mitm"


def _images(topo):
    return {d["image"] for n in topo["spec"]["nodes"] for d in n["hardware"]["drives"]}


def _render(settings, name, route, **requires):
    import copy
    import dataclasses

    from burger.render import render
    from tests.conftest import MAN, res

    r = res(settings, name, route_name=route)
    r = dataclasses.replace(r, requires=dataclasses.replace(r.requires, **requires))
    return render(r, copy.deepcopy(MAN), None, "/phenix/burger/x", settings)


@pytest.mark.usefixtures("site_env")
def test_arm64_routes_render_arm64_images_only(settings):
    mitm = load_route("mitm-arm64", settings)
    assert mitm.arm64 and mitm.drop_nodes == ["blue"] and not load_route("mitm", settings).arm64
    topo, _ = _render(settings, "ettercap", "mitm-arm64")
    assert all(i.endswith("-arm64.qc2") for i in _images(topo))
    assert "blue" not in {n["general"]["hostname"] for n in topo["spec"]["nodes"]}
    # every node burger injects: mapped image plus the arm64 hardware block
    topo, _ = _render(
        settings, "ettercap", "waterway-arm64", monitor=True, traffic_gen=True, caldera_server=True
    )
    assert all(i.endswith("-arm64.qc2") for i in _images(topo))
    injected = {"kali", "monitor", "traffic-server", "background-gen", "malware-gen", "caldera"}
    nodes = [n for n in topo["spec"]["nodes"] if n["general"]["hostname"] in injected]
    assert len(nodes) == len(injected)
    for n in nodes:
        assert n["hardware"]["cpu"] == "host"
        assert n["hardware"]["drives"][0]["inject_partition"] == 2
        assert n["advanced"]["machine"] == "virt" and "AAVMF" in n["advanced"]["qemu-append"]
        assert n["overrides"] == {"bus=usb-bus.0": "bus=xhci.0"}


@pytest.mark.usefixtures("site_env")
def test_arm64_route_goart_and_bennu_pr(settings, tmp_path):
    from burger.switchboard import load_switchboard, resolve

    _, scen = _render(settings, "art", "waterway-arm64")
    assert scen["metadata"]["name"] == "ebwes-art"
    board = load_switchboard("bennu", settings)
    assert board.bennu
    with pytest.raises(BurgerError, match="arm64"):
        resolve(board, settings, route_name="waterway-arm64")
