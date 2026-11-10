from burger.checks.hook import load
from burger.items import load_item
from burger.switchboard import load_switchboard, resolve


def judge(settings, bennu):
    ctx = type("C", (), {"manifest": {"bennu": bennu}})()
    return load(load_item("bennu/deb-arm64", settings), "judge_deb_arm64")(ctx)


NATIVE = {"deb_file": "bennu-6.0.1-g1_arm64.deb", "deb_arch": "arm64", "relabel_needed": False}


def test_native_arm64_deb_passes(settings):
    ok, msg = judge(settings, NATIVE)
    assert ok and "no relabel" in msg and "bennu-6.0.1-g1_arm64.deb" in msg


def test_older_head_needing_the_relabel_fails(settings):
    old = {"deb_file": "bennu-6.0.1-g1_amd64.deb", "deb_arch": "amd64", "relabel_needed": True}
    ok, msg = judge(settings, old)
    assert not ok and "had to relabel" in msg and "does not end _arm64.deb" in msg


def test_file_name_must_end_arm64_deb(settings):
    ok, msg = judge(settings, NATIVE | {"deb_file": "bennu-6.0.1-g1_amd64.deb"})
    assert not ok and "does not end _arm64.deb" in msg and "had to relabel" not in msg


def test_stock_or_non_arm64_build_fails(settings):
    assert not judge(settings, {})[0]
    assert not judge(settings, {"deb_version": "1"})[0]


def test_board(settings):
    board = load_switchboard("arm64deb", settings)
    assert board.bennu["pr"] == 78 and len("ebwes-arm64deb") <= 15
    names = {i.name for i in resolve(board, settings).items}
    assert {"deb-arm64", "field-device", "simulink-provider", "modbus-polled"} <= names
