"""Generic invariants over every item, switchboard and check kind."""

from dataclasses import replace

import pytest
import yaml

from burger.checks import CHECKS, run_checks
from burger.checks.hook import load
from burger.compose import compose
from burger.config import BurgerError
from burger.items import CHECK_KEYS, OPTIONAL_KEYS, all_items, load_item, resolve_item
from burger.routes import load_route
from burger.switchboard import all_switchboards, load_switchboard
from tests.conftest import MAN, ROOT_SETTINGS, make_ctx, res

ITEMS = {  # keyed the way a switchboard names them: "pcap", "bennu/field-device"
    str(i.path.relative_to(ROOT_SETTINGS.root).with_suffix("")).removeprefix("components/"): i
    for i in all_items(ROOT_SETTINGS)
}
BOARDS = all_switchboards(ROOT_SETTINGS)
# Specs that are valid but find nothing; python is covered through run_checks.
EMPTY = {
    "files": {"glob": "*.nope"},
    "pcap": {"glob": "*.nope"},
    "csv": {"glob": "*.nope"},
    "log": {"glob": "*.nope"},
    "json": {"glob": "*.nope", "field": "a"},
}
# Items no switchboard turns on, on purpose: validate-only items have no routes and
# are exercised by the tests that name them, not by a board.
ORPHANS: set[str] = set()


def verify_lists():
    """(where, item, verify list): an item, its route blocks, a board's overrides."""
    for ref, item in ITEMS.items():
        yield ref, item, item.verify
        for route, block in item.overrides.items():
            yield f"{ref} {route}", item, block.get("verify") or []
    for name in BOARDS:
        for ref, over in load_switchboard(name, ROOT_SETTINGS).overrides.items():
            if isinstance(over, dict) and over.get("verify"):
                yield f"board {name} {ref}", load_item(ref, ROOT_SETTINGS), over["verify"]


VERIFY = [pytest.param(w, i, v, id=w) for w, i, v in verify_lists()]


def test_every_check_kind_has_a_key_declaration():
    assert set(CHECK_KEYS) == {*CHECKS, "vm", "process", "port"}
    assert set(OPTIONAL_KEYS) <= set(CHECK_KEYS)


@pytest.mark.parametrize("kind", EMPTY)
def test_missing_output_is_fail(settings, tmp_path, kind):
    ctx, _ = make_ctx(settings, tmp_path)
    r = CHECKS[kind](EMPTY[kind], ctx)
    assert r.verdict == "FAIL"
    assert r.reason.startswith("no output found at")


@pytest.mark.parametrize("kind", CHECKS)
def test_checker_exception_is_a_fail_row_and_advisory_downgrades_it(
    settings, tmp_path, monkeypatch, kind
):
    def boom(spec, ctx):
        raise ValueError("boom")

    monkeypatch.setitem(CHECKS, kind, boom)
    for advisory, verdict in ((False, "FAIL"), (True, "advisory-FAIL")):
        ctx, _ = make_ctx(settings, tmp_path, verify=[{kind: {}}], advisory=advisory)
        (r,) = run_checks(ctx.item, ctx)
        assert (r.verdict, r.reason) == (verdict, "check raised ValueError: boom")


def test_missing_python_function_is_a_fail_row(settings, tmp_path):
    ctx, _ = make_ctx(settings, tmp_path, verify=[{"python": "no_such_function"}])
    (r,) = run_checks(ctx.item, ctx)
    assert r.verdict == "FAIL"
    assert "no function 'no_such_function'" in r.reason


@pytest.mark.parametrize("kind", [k for k in CHECK_KEYS if k != "python"])
def test_unknown_spec_key_is_a_load_error(tmp_path, kind):
    spec = {**dict.fromkeys(CHECK_KEYS[kind], "x"), "min_packet": 1}
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "zz.yaml").write_text(
        yaml.dump({"name": "zz", "kind": "scorch", "type": "zz", "verify": [{kind: spec}]})
    )
    with pytest.raises(BurgerError, match=f"verify\\[0\\].{kind}: unknown key 'min_packet'"):
        load_item("zz", replace(ROOT_SETTINGS, root=tmp_path))


@pytest.mark.parametrize("where, item, verify", VERIFY)
def test_verify_specs_use_declared_keys(where, item, verify):
    for entry in verify:
        ((kind, spec),) = entry.items()
        if isinstance(spec, dict):
            known = {*CHECK_KEYS[kind], *OPTIONAL_KEYS.get(kind, ()), "windows"}
            assert set(spec) <= known, f"{where}: {kind} has {sorted(set(spec) - known)}"


@pytest.mark.parametrize("where, item, verify", VERIFY)
def test_python_functions_exist(where, item, verify):
    for entry in verify:
        spec = entry.get("python")
        if spec:
            load(item, spec if isinstance(spec, str) else spec["function"])


@pytest.mark.parametrize("ref", ITEMS, ids=str)
def test_item_has_about(ref):
    assert ITEMS[ref].about


@pytest.mark.parametrize("ref", [r for r, i in ITEMS.items() if i.routes], ids=str)
def test_routed_item_is_on_in_some_board(ref):
    on = {n for b in BOARDS for n, v in load_switchboard(b, ROOT_SETTINGS).items.items() if v}
    assert ref in on or ref in ORPHANS


@pytest.mark.parametrize("linux_only", [False, True])
@pytest.mark.parametrize("name", BOARDS)
@pytest.mark.usefixtures("site_env")
def test_every_board_composes_without_topologies(settings, name, linux_only):
    compose(res(settings, name, linux_only=linux_only), MAN)


@pytest.mark.usefixtures("site_env")
def test_every_vm_process_and_port_host_is_in_the_topology(settings, topologies):
    def nodes(route):
        topo = route.base["topology"] if route.base else topologies / route.topology_dir
        topo = topo if route.base else topo / route.files[0]
        doc = yaml.safe_load(open(topo))
        return {n["general"]["hostname"] for n in doc["spec"]["nodes"]} | {"monitor"}

    problems = []
    for item in ITEMS.values():
        for name in item.routes:
            route = load_route(name, settings)
            hosts = nodes(route)
            for entry in resolve_item(item, route, None).verify:
                if vm := entry.get("vm"):
                    h = vm["host"] if isinstance(vm["host"], list) else [vm["host"]]
                    problems += [f"{item.name} on {name}: {x}" for x in h if x not in hosts]
    assert not problems
