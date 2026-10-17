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


def test_background_requires_poller(settings):
    item = dataclasses.replace(
        load_item("pcap", settings), background=True, stages=["start", "stop"]
    )
    with pytest.raises(BurgerError, match="not a poller"):
        resolve_item(item, load_route("waterway", settings), None)


def test_every_item_loads(settings):
    items = all_items(settings)
    assert len(items) >= 23
    for item in items:
        for name in item.routes:
            resolve_item(item, load_route(name, settings), None)


def test_validate_only_items(settings):
    assert load_item("disruption-dos", settings).routes == []


HEAD = "name: x\nkind: scorch\ntype: x\n"


@pytest.mark.parametrize(
    "body, field",
    [
        ("verify:\n  - pcaps: {glob: '*.pcap'}\n", "verify[0]: unknown check 'pcaps'"),
        ("verify:\n  - vm: {hosts: rtu-1, run: 'true'}\n", "verify[0].vm needs 'host'"),
        ("verify:\n  - status: ok\n  - vm: {host: rtu-1}\n", "verify[1].vm needs 'run'"),
        ("verify:\n  - log: {path: '*.log'}\n", "verify[0].log needs 'glob'"),
        ("verify:\n  - json: {glob: '*.json'}\n", "verify[0].json needs 'field'"),
        ("verify:\n  - python: {}\n", "verify[0].python needs 'function'"),
        ("verify:\n  - {status: ok, files: {glob: x}}\n", "verify[0] must be one check"),
        ("requires: {app_prs: [170]}\n", "requires.app_prs is not a requirement"),
        ("soap:\n  verify:\n    - pcap: {tcp_port: 1}\n", "soap.verify[0].pcap needs 'glob'"),
    ],
)
def test_nested_fields_validated_at_load(settings, tmp_path, body, field):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "zz.yaml").write_text(HEAD + body)
    with pytest.raises(BurgerError) as e:
        load_item("zz", dataclasses.replace(settings, root=tmp_path))
    assert "zz.yaml" in str(e.value) and field in str(e.value)


def test_board_override_verify_validated(settings):
    item = load_item("pcap", settings)
    with pytest.raises(BurgerError, match=r"verify\[0\]\.files needs 'glob'"):
        resolve_item(item, load_route("waterway", settings), {"verify": [{"files": {}}]})


def test_check_keys_cover_every_check():
    from burger.checks import CHECKS, HANDLED_BY_VERIFY
    from burger.items import CHECK_KEYS

    assert set(CHECK_KEYS) == {*CHECKS, *HANDLED_BY_VERIFY}


@pytest.mark.parametrize("kind", ["scorch", "app", "bennu"])
def test_template_examples_load_uncommented(settings, tmp_path, kind):
    """Every commented example check in a template is valid once uncommented."""
    import re

    text = (settings.root / "src/burger/templates" / f"{kind}.yaml").read_text()
    text = re.sub(r"(?m)^  # (- \w+: )", r"  \1", text).replace("__NAME__", "zz")
    d = tmp_path / {"scorch": "components", "app": "apps", "bennu": "bennu"}[kind]
    d.mkdir()
    (d / "zz.yaml").write_text(text)
    ref = "zz" if kind == "scorch" else f"{d.name}/zz"
    item = load_item(ref, dataclasses.replace(settings, root=tmp_path))
    assert len(item.verify) >= 2
