import dataclasses
from pathlib import Path

import pytest

from burger.config import BurgerError
from burger.switchboard import all_switchboards, load_switchboard, resolve

GOLDEN = Path(__file__).parent / "golden"


def board(settings, name, **kw):
    return dataclasses.replace(load_switchboard(name, settings), **kw)


def test_resolve_rejects_unsupported_route(settings):
    b = board(settings, "baseline", items={"providerdata": True})
    with pytest.raises(BurgerError) as e:
        resolve(b, settings)
    assert "supports" in str(e.value)
    assert "waterway" in str(e.value)


def test_resolve_reports_all_unset_env(settings, monkeypatch):
    for v in ("BURGER_VM_PASSWORD", "BURGER_SITE_TOPOLOGY", "BURGER_SITE_SCENARIO"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(BurgerError) as e:
        resolve(load_switchboard("ssh", settings), settings)
    assert "BURGER_VM_PASSWORD" in str(e.value)
    with pytest.raises(BurgerError) as e:
        resolve(load_switchboard("rtds", settings), settings)
    assert "BURGER_SITE_TOPOLOGY" in str(e.value)
    assert "BURGER_SITE_SCENARIO" in str(e.value)


def test_resolve_missing_pr(settings):
    with pytest.raises(BurgerError, match="#165"):
        resolve(board(settings, "iperf", apps_prs=[]), settings)


def test_switchboard_unknown_route(settings, tmp_path):
    (tmp_path / "switchboards").mkdir()
    (tmp_path / "switchboards" / "bad.yaml").write_text("route: waterwy\n")
    with pytest.raises(BurgerError) as e:
        load_switchboard("bad", dataclasses.replace(settings, root=tmp_path))
    assert "bad.yaml" in str(e.value)
    assert "waterwy" in str(e.value)


def test_linux_only_skips_windows_items(settings):
    b = load_switchboard("baseline", settings)
    assert "modbus-polled" in [i.name for i in resolve(b, settings).items]
    assert "modbus-polled" not in [i.name for i in resolve(b, settings, linux_only=True).items]


def test_required_app_follows_its_requirer(settings, monkeypatch):
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")
    r = resolve(load_switchboard("ssh", settings), settings)
    assert [i.name for i in r.items][:2] == ["ssh", "mgmt_tap"]
    assert r.requires.env == ["BURGER_VM_PASSWORD"]


def test_requires_aggregate(settings):
    r = resolve(load_switchboard("iperf", settings), settings)
    assert r.requires.debs_packages == ["iperf3", "libiperf0", "libsctp1"]
    assert r.requires.debs_nodes == ["rtu-1", "rtu-2"]
    assert resolve(load_switchboard("harden", settings), settings).requires.monitor


def test_experiment_name_too_long(settings):
    with pytest.raises(BurgerError, match="experiment name"):
        resolve(board(settings, "baseline", exp="ebwes-" + "x" * 10), settings)


def test_every_switchboard_resolves(settings, monkeypatch):
    monkeypatch.setenv("BURGER_VM_PASSWORD", "lab")
    monkeypatch.setenv("BURGER_SITE_TOPOLOGY", str(GOLDEN / "site-topology.yaml"))
    monkeypatch.setenv("BURGER_SITE_SCENARIO", str(GOLDEN / "site-scenario.yaml"))
    names = all_switchboards(settings)
    assert len(names) == 9
    for name in names:
        resolve(load_switchboard(name, settings), settings)


def test_override_for_unknown_item(settings):
    for items in ({"disruption": False}, {}):
        b = board(settings, "baseline", items=items, overrides={"disruption": {"config": {}}})
        with pytest.raises(BurgerError) as e:
            resolve(b, settings)
        assert "baseline.yaml" in str(e.value)
        assert "overrides.disruption" in str(e.value)


@pytest.mark.parametrize("body", ["items: [pcap]\n", "apps: [158]\n", "overrides: [x]\n"])
def test_switchboard_shapes(settings, tmp_path, body):
    (tmp_path / "switchboards").mkdir()
    (tmp_path / "switchboards" / "zz.yaml").write_text("route: waterway\n" + body)
    with pytest.raises(BurgerError, match=r"zz\.yaml: \w+ must be a mapping"):
        load_switchboard("zz", dataclasses.replace(settings, root=tmp_path))


def test_harden_tcpdump_counts_gre(settings):
    res = resolve(load_switchboard("harden", settings), settings)
    tcpdump = next(i for i in res.items if i.name == "tcpdump")
    spec = next(v["pcap"] for v in tcpdump.verify if "pcap" in v)
    assert spec["tcp_port"] == 502 and spec["ip_proto"] == 47
    assert [next(iter(v)) for v in tcpdump.verify] == ["files", "pcap"]
