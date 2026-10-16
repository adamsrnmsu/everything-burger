import dataclasses

import pytest

from burger.config import BurgerError
from burger.items import all_items, load_item, resolve_item
from burger.routes import load_route


def pcap_port(item):
    return next(c["pcap"]["tcp_port"] for c in item.verify if "pcap" in c)


def test_load_item_unknown_field(settings, tmp_path):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "zz-bad.yaml").write_text("name: x\nverfy: 1\n")
    with pytest.raises(BurgerError) as e:
        load_item("zz-bad", dataclasses.replace(settings, root=tmp_path))
    assert "zz-bad.yaml" in str(e.value)
    assert "'verfy'" in str(e.value)


def test_route_override_and_substitution(settings):
    item = load_item("pcap", settings)
    assert pcap_port(resolve_item(item, load_route("soap", settings), None)) == 20000
    res = resolve_item(item, load_route("waterway", settings), None)
    assert pcap_port(res) == 502
    assert res.config["vms"][0]["hostname"] == "rtu-1"


def test_route_block_replaces_lists_and_types(settings):
    item = load_item("bennu/field-device", settings)
    soap = resolve_item(item, load_route("soap", settings), None)
    assert [c["vm"]["expect"] for c in soap.verify if "expect" in c["vm"]] == ["bus-"]
    assert soap.verify[0]["vm"]["host"] == ["rtu-1", "rtu-2", "rtu-3", "rtu-4"]
    wat = resolve_item(item, load_route("waterway", settings), None)
    assert len(wat.verify) == 3


def test_override_replaces_lists(settings):
    item = load_item("pcap", settings)
    vms = [{"hostname": "rtu-2", "interface": 1}]
    res = resolve_item(item, load_route("waterway", settings), {"config": {"vms": vms}})
    assert res.config["vms"] == vms


def test_vm_dollar_brace_rejected(settings):
    item = load_item("bennu/siren", settings)
    item.verify = [{"vm": {"host": "siren", "run": "echo ${X}"}}]
    with pytest.raises(BurgerError, match=r"\$\{"):
        resolve_item(item, load_route("soap", settings), None)


def test_background_requires_stop(settings):
    item = load_item("hoststats", settings)
    item = dataclasses.replace(item, stages=["start"])
    with pytest.raises(BurgerError, match="background"):
        resolve_item(item, load_route("waterway", settings), None)


def test_every_item_loads(settings):
    items = all_items(settings)
    assert len(items) == 23
    for item in items:
        for name in item.routes:
            resolve_item(item, load_route(name, settings), None)


def test_validate_only_items(settings):
    assert load_item("disruption-dos", settings).routes == []
