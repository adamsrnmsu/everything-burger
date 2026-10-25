import dataclasses

import pytest

from burger.config import BurgerError
from burger.switchboard import all_switchboards, load_switchboard, resolve
from tests.conftest import res


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
    with pytest.raises(BurgerError, match="#168"):
        resolve(board(settings, "metadata", apps_prs=[166]), settings)


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


@pytest.mark.usefixtures("vm_password")
def test_required_app_follows_its_requirer(settings):
    r = resolve(load_switchboard("ssh", settings), settings)
    assert [i.name for i in r.items][:3] == ["ssh-password", "ssh", "mgmt_tap"]
    assert r.requires.env == ["BURGER_VM_PASSWORD"]


def test_requires_aggregate(settings):
    r = resolve(load_switchboard("iperf", settings), settings)
    assert r.requires.iperf_static == "3.15"
    assert r.requires.iperf_nodes == ["rtu-1", "rtu-2"]
    assert resolve(load_switchboard("harden", settings), settings).requires.monitor


def test_experiment_name_too_long(settings):
    with pytest.raises(BurgerError, match="experiment name"):
        resolve(board(settings, "baseline", exp="ebwes-" + "x" * 10), settings)


@pytest.mark.usefixtures("site_env")
def test_every_switchboard_resolves(settings):
    names = all_switchboards(settings)
    assert len(names) == 21
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


def test_elastic_board_expects_192_error_and_adds_mgmt_tap(settings):
    r = res(settings, "elastic")
    items = {i.name: i for i in r.items}
    assert items["elastic-empty"].expect_error == "returned no cluster info"
    assert "mgmt_tap" in items  # elastic-empty requires it: the host reaches the stub over MGMT
    assert r.board.apps_prs == []  # #177 and #192 are on main


def test_pcap_alias_is_validated_but_not_run(settings):
    r = res(settings, "metadata")
    assert "pcap-alias" in [i.name for i in r.validate_only]
    assert "pcap-alias" not in [i.name for i in r.items]
    new = next(i for i in r.items if i.name == "pcap")
    assert new.config["convert_to_json"] is True and "convertToJSON" not in new.config


def write_board(settings, tmp_path, body):
    (tmp_path / "switchboards").mkdir(exist_ok=True)
    (tmp_path / "switchboards" / "t.yaml").write_text(f"route: waterway\n{body}")
    return dataclasses.replace(settings, root=tmp_path)


def test_apps_branch_defaults_to_fork_and_records_contains(settings, tmp_path):
    s = write_board(settings, tmp_path, "apps: {branch: integration, contains: [159, 161]}\n")
    b = load_switchboard("t", s)
    assert b.apps_branch == {"branch": "integration", "remote": "fork"}
    assert b.apps_prs == [159, 161]


def test_bennu_branch_defaults_to_upstream(settings, tmp_path):
    s = write_board(settings, tmp_path, "bennu: {branch: main, install: [deb]}\n")
    assert load_switchboard("t", s).bennu == {
        "branch": "main",
        "remote": "upstream",
        "install": ["deb"],
    }


@pytest.mark.parametrize(
    "body, msg",
    [
        ("apps: {prs: [1], branch: x}\n", "'prs' or 'branch'"),
        ("bennu: {pr: 1, branch: x}\n", "'pr' or 'branch'"),
        ("apps: {prs: [1], remote: fork}\n", "apps.remote needs 'branch'"),
        ("apps: {prs: [1], contains: [2]}\n", "apps.contains needs 'branch'"),
        ("apps: {branch: x, remote: origin}\n", "fork or upstream"),
        ("apps: {prs: [one]}\n", "apps.prs must be a list of integers"),
        ("apps: {prs: 5}\n", "apps.prs must be a list of integers"),
        ("apps: {branch: x, contains: ['1']}\n", "apps.contains must be a list of integers"),
        ("bennu: {pr: '58'}\n", "bennu.pr must be an integer"),
        ("bennu: {pr: true}\n", "bennu.pr must be an integer"),
        ("apps: {pr: [1]}\n", "unknown apps field 'pr'"),
        ("bennu: {prs: 1}\n", "unknown bennu field 'prs'"),
        ("bennu: {install: [deb]}\n", "needs 'pr' or 'branch'"),
        ("apps: {branch: 5}\n", "branch must be a branch name"),
        ("bennu: {remote: fork, pr: 1}\n", "bennu.remote needs 'branch'"),
        ("bennu: {contains: [1], pr: 1}\n", "unknown bennu field 'contains'"),
        ("bennu: {pr: 1, contains: [2]}\n", "unknown bennu field 'contains'"),
        ("bennu: [main]\n", "bennu must be a mapping"),
        ("bennu: {branch: main, remote: origin}\n", "fork or upstream"),
        ("bennu: {branch: 'a b'}\n", "branch must be a branch name"),
        ("apps: {branch: 'a:b'}\n", "branch must be a branch name"),
        ("apps: {branch: 'a..b'}\n", "branch must be a branch name"),
        ("apps: {branch: '-x'}\n", "branch must be a branch name"),
        ("apps: {branch: 'a/'}\n", "branch must be a branch name"),
        ("apps: {branch: 'x.lock'}\n", "branch must be a branch name"),
    ],
)
def test_bad_sources_are_burger_errors(settings, tmp_path, body, msg):
    with pytest.raises(BurgerError, match=msg):
        load_switchboard("t", write_board(settings, tmp_path, body))


def test_requires_apps_prs_resolve_against_contains(settings):
    ok = board(
        settings, "metadata", apps_prs=[166, 168], apps_branch={"branch": "b", "remote": "fork"}
    )
    assert resolve(ok, settings).board.apps_branch
    with pytest.raises(BurgerError, match="#168"):
        resolve(board(settings, "metadata", apps_prs=[], apps_branch=ok.apps_branch), settings)


def test_branch_names_with_slashes_and_dots_are_fine(settings, tmp_path):
    s = write_board(settings, tmp_path, "apps: {branch: feat/x-1.2_a}\n")
    assert load_switchboard("t", s).apps_branch["branch"] == "feat/x-1.2_a"


def test_requires_message_names_prs_or_contains(settings):
    with pytest.raises(BurgerError, match=r"apps prs$"):
        resolve(board(settings, "metadata", apps_prs=[]), settings)
    b = board(settings, "metadata", apps_prs=[], apps_branch={"branch": "b", "remote": "fork"})
    with pytest.raises(BurgerError, match="apps contains"):
        resolve(b, settings)


def test_iperf_322_requires_and_config(settings):
    r = resolve(load_switchboard("iperf-322", settings), settings)
    assert r.requires.iperf_static == "3.22"
    assert r.requires.iperf_nodes == ["rtu-1", "rtu-2"]
    assert r.requires.debs_packages == []
    cfg = next(i for i in r.items if i.name == "iperf-322").config
    assert cfg["iperf_version"] == "3.22"
    assert cfg["iperf_paths"] == {"linux": "/usr/local/bin/iperf3-3.22"}
    assert resolve(load_switchboard("iperf", settings), settings).requires.iperf_static == "3.15"
