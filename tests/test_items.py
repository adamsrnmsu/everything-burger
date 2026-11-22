import dataclasses
import subprocess

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


def test_modbus_polled_checks_dnp3_on_the_arm64_range(settings):
    # waterway-arm64's OPC gateway polls the RTUs over DNP3 (20000); nothing connects to 502
    item = load_item("bennu/modbus-polled", settings)
    arm = resolve_item(item, load_route("waterway-arm64", settings), None)
    est = "ss -Htn state established '( sport = :{} )' | grep -q ."
    assert [c["vm"]["run"] for c in arm.verify] == [est.format(20000)]
    wat = resolve_item(item, load_route("waterway", settings), None)
    assert [c["vm"]["run"] for c in wat.verify] == [est.format(502)]


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
        ("verify:\n  - vm: {host: rtu-1}\n", "verify[0].vm needs 'run'"),
        ("verify:\n  - status: ok\n", "verify[0]: 'status' is checked for every component"),
        ("verify:\n  - log: {path: '*.log'}\n", "verify[0].log needs 'glob'"),
        ("verify:\n  - json: {glob: '*.json'}\n", "verify[0].json needs 'field'"),
        ("verify:\n  - python: {}\n", "verify[0].python needs 'function'"),
        ("verify:\n  - {vm: {host: a, run: b}, files: {glob: x}}\n", "verify[0] must be one check"),
        ("requires: {app_prs: [170]}\n", "requires.app_prs is not a requirement"),
        ("soap:\n  verify:\n    - pcap: {tcp_port: 1}\n", "soap.verify[0].pcap needs 'glob'"),
        ("expect_invalid: x\nroutes: [waterway]\n", "expect_invalid needs kind scorch or app"),
        ("expect_invalid: '('\nroutes: []\n", "expect_invalid is not a regex"),
        ("expect_nodes: {a: []}\nroutes: []\n", "expect_nodes needs kind app"),
    ],
)
def test_nested_fields_validated_at_load(settings, tmp_path, body, field):
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "zz.yaml").write_text(HEAD + body)
    with pytest.raises(BurgerError) as e:
        load_item("zz", dataclasses.replace(settings, root=tmp_path))
    assert "zz.yaml" in str(e.value) and field in str(e.value)


def test_expect_invalid_rejects_a_bennu_item(settings, tmp_path):
    (tmp_path / "bennu").mkdir()
    (tmp_path / "bennu" / "zz.yaml").write_text(
        "name: x\nkind: bennu\ntype: x\nexpect_invalid: x\n"
    )
    with pytest.raises(BurgerError, match="expect_invalid needs kind scorch or app"):
        load_item("bennu/zz", dataclasses.replace(settings, root=tmp_path))


def test_board_override_verify_validated(settings):
    item = load_item("pcap", settings)
    with pytest.raises(BurgerError, match=r"verify\[0\]\.files needs 'glob'"):
        resolve_item(item, load_route("waterway", settings), {"verify": [{"files": {}}]})


def test_check_keys_cover_every_check():
    from burger.checks import CHECKS, HANDLED_BY_VERIFY
    from burger.items import CHECK_KEYS

    assert set(CHECK_KEYS) == {*CHECKS, *HANDLED_BY_VERIFY, "process", "port"}


@pytest.mark.parametrize(
    "check, run",
    [
        ({"process": {"host": "h", "name": "bennu-x"}}, "pgrep -f '(^|/)bennu-x( |$)'"),
        (
            {"process": {"host": "h", "name": ["a", "b.c"]}},
            "pgrep -f '(^|/)(a|b.c)( |$)'",
        ),
        ({"port": {"host": "h", "port": 502}}, "ss -Hltn '( sport = :502 )' | grep -q ."),
        (
            {"port": {"host": "h", "port": "502", "state": "established"}},
            "ss -Htn state established '( sport = :502 )' | grep -q .",
        ),
    ],
)
def test_process_and_port_expand_to_vm(settings, check, run):
    item = dataclasses.replace(
        load_item("pcap", settings), verify=[{k: {**v, "windows": True} for k, v in check.items()}]
    )
    out = resolve_item(item, load_route("soap", settings), None)
    assert out.verify == [{"vm": {"host": "h", "run": run, "windows": True}}]


@pytest.mark.parametrize(
    "check, why",
    [
        ({"process": {"host": "h", "name": "x; reboot"}}, "must match"),
        ({"process": {"host": "h", "name": "x'y"}}, "must match"),
        ({"process": {"host": "h", "name": ["ok", "x|y"]}}, "must match"),
        ({"process": {"host": "h", "name": []}}, "list is empty"),
        ({"port": {"host": "h", "port": "502; reboot"}}, "must be an integer"),
        ({"port": {"host": "h", "port": 22, "state": "closed"}}, "must be listening or"),
    ],
)
def test_process_and_port_reject_unsafe_values(settings, check, why):
    item = dataclasses.replace(load_item("pcap", settings), verify=[check])
    with pytest.raises(BurgerError, match=why):
        resolve_item(item, load_route("soap", settings), None)


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
    assert item.verify


def test_fifo_sends_the_tunable_tag_count(settings, monkeypatch):
    assert "N=1000\n" in load_item("fifo", settings).config["vms"][0]["start"][0]["args"]
    monkeypatch.setenv("BURGER_FIFO_TAGS", "300")
    assert "N=300\n" in load_item("fifo", settings).config["vms"][0]["start"][0]["args"]


def _item_settings(settings, tmp_path, text, where="components"):
    (tmp_path / where).mkdir(exist_ok=True)
    (tmp_path / where / "zz.yaml").write_text(text)
    return dataclasses.replace(settings, root=tmp_path)


@pytest.mark.parametrize(
    "text, msg",
    [
        ("kind: scorch\ntype: x\n", "missing field 'name'"),
        ("name: x\ntype: x\n", "missing field 'kind'"),
        ("name: x\nkind: scorch\n", "missing field 'type'"),
        (HEAD.replace("scorch", "thing"), "kind 'thing' must be one of scorch, app, bennu"),
        (HEAD + "stages: [start, boom]\n", "unknown stage 'boom'"),
    ],
)
def test_bad_item_header_is_a_burger_error(settings, tmp_path, text, msg):
    with pytest.raises(BurgerError, match=msg) as e:
        load_item("zz", _item_settings(settings, tmp_path, text))
    assert "zz.yaml" in str(e.value)


def test_unknown_item_names_where_it_looked(settings, tmp_path):
    with pytest.raises(BurgerError, match=r"no item 'nope' \(looked for .*components/nope\.yaml"):
        load_item("nope", dataclasses.replace(settings, root=tmp_path))


def test_item_fields_are_loaded(settings, tmp_path):
    text = HEAD + "about: ' hi '\nroutes: [soap]\nstages: [start, stop]\nholds: 1\n"
    text += "background: 1\nadvisory: 1\nexpect_error: e\nconfig: {a: 1}\nhosts: [h]\n"
    text += "requires: {monitor: true}\nassets: [f]\nsoap: {config: {a: 2}}\n"
    s = _item_settings(settings, tmp_path, text, "apps")
    i = load_item("apps/zz", s)
    assert (i.name, i.kind, i.type, i.about, i.routes) == ("x", "scorch", "x", "hi", ["soap"])
    assert (i.stages, i.holds, i.background, i.advisory) == (["start", "stop"], True, True, True)
    assert (i.expect_error, i.expect_invalid, i.config, i.hosts) == ("e", "", {"a": 1}, ["h"])
    assert (i.requires, i.assets, i.path) == ({"monitor": True}, ["f"], tmp_path / "apps/zz.yaml")
    assert i.overrides == {"soap": {"config": {"a": 2}}}


def test_override_replaces_a_dict_with_a_scalar_and_back(settings):
    item = load_item("pcap", settings)
    route = load_route("waterway", settings)
    out = resolve_item(item, route, {"config": [1], "hosts": {"a": 1}})
    assert out.config == [1] and out.hosts == {"a": 1}


@pytest.mark.parametrize(
    "want, ok",
    [
        ("{a: [/x]}", True),
        ("{a: []}", True),
        ("[a]", False),
        ("{a: x}", False),
        ("{a: [1]}", False),
        ("[]", False),
        ("''", False),
        ("null", False),
        ("{}", False),
        ("{1: []}", False),
    ],
)
def test_expect_nodes_is_a_hostname_to_injection_list_map(settings, tmp_path, want, ok):
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "zz.yaml").write_text(
        f"name: x\nkind: app\ntype: x\nroutes: []\nexpect_nodes: {want}\n"
    )
    s = dataclasses.replace(settings, root=tmp_path)
    if ok:
        assert load_item("apps/zz", s).expect_nodes == {"a": [] if want == "{a: []}" else ["/x"]}
    else:
        with pytest.raises(BurgerError, match=r"expect_nodes is \{hostname: \[injection dst"):
            load_item("apps/zz", s)


@pytest.mark.parametrize("extra", ["routes: [waterway]", "expect_invalid: x"])
def test_expect_nodes_needs_a_validate_only_app_without_expect_invalid(settings, tmp_path, extra):
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "zz.yaml").write_text(
        f"name: x\nkind: app\ntype: x\n{extra}\nexpect_nodes: {{a: []}}\n"
    )
    with pytest.raises(BurgerError, match="expect_nodes needs kind app"):
        load_item("apps/zz", dataclasses.replace(settings, root=tmp_path))


@pytest.mark.parametrize(
    "journal,out",
    [
        ("1.0 startup/sceptre-start.sh\n2.0 Sandcat agent started\n", "sceptre-start did not wait"),
        (
            "2.0 Sandcat agent started\n3.0 startup/sceptre-start.sh\n",
            "sceptre-start waited for the agent",
        ),
        ("1.0 startup/sceptre-start.sh\n", "no agent marker in the journal"),
    ],
)
def test_caldera_agent_journal_check_needs_the_marker(settings, journal, out):
    # absence of the marker must not pass: a missing line proves nothing
    run = load_item("bennu/caldera-agent-background", settings).verify[1]["vm"]["run"]
    awk = run.split("--no-pager | ", 1)[1]
    got = subprocess.run(
        ["sh", "-c", f"awk {awk[4:]}"], input=journal, capture_output=True, text=True
    )
    assert got.stdout.strip() == out
