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
    with pytest.raises(BurgerError, match="site, soap, waterway"):
        load_route("waterwy", settings)
