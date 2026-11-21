import argparse
import dataclasses
import json
import subprocess

import pytest
import yaml
from loguru import logger

from burger import cli, experiment, minimega, update
from burger.config import BurgerError
from tests.conftest import MAN, res
from tests.fakes import FakeHost, StrictHost, watch_loud

SHA = "c" * 40
TAG = "burger/minimega:cccccccccccc"


def cp(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


@pytest.fixture
def messages():
    got = []
    hid = logger.add(lambda m: got.append((m.record["level"].name, m.record["message"])), level=0)
    yield got
    logger.remove(hid)


# --- build ---


def test_build_image_runs_the_repo_dockerfile(settings, tmp_path):
    host = FakeHost({"docker image inspect": cp(rc=1)}, settings)
    wt = tmp_path / "minimega"
    assert minimega.build_image(host, wt, SHA) == TAG
    assert host.calls[-1] == [
        "docker", "build", "-f", str(wt / "docker" / "Dockerfile"), "-t", TAG, str(wt)
    ]  # fmt: skip


def test_build_image_skips_an_existing_tag(settings, tmp_path):
    host = FakeHost(settings=settings)
    assert minimega.build_image(host, tmp_path / "minimega", SHA) == TAG
    assert host.calls == [["docker", "image", "inspect", TAG]]


def test_build_image_puts_the_routes_layer_on_the_pr_build(settings, tmp_path):
    host = FakeHost({"docker image inspect": cp(rc=1)}, settings)
    wt, rng = tmp_path / "minimega", tmp_path / "range" / "docker"
    (rng / "tmp").mkdir(parents=True)  # bind-mount state next to the range's Dockerfiles
    (rng / "kvm-wrapper.sh").write_text("#!/bin/sh\n")
    (rng / "minimega-arm64.Dockerfile").write_text(
        "# a comment\nFROM minimega:arm64\nCOPY kvm-wrapper.sh /usr/local/bin/kvm\n"
    )
    base = "burger/minimega-base:cccccccccccc"
    tag = minimega.build_image(host, wt, SHA, str(rng / "minimega-arm64.Dockerfile"))
    assert tag.startswith(TAG + "-") and tag != TAG
    ctx = settings.work / "minimega" / "layer"
    assert host.calls[-2] == [
        "docker", "build", "-f", str(wt / "docker" / "Dockerfile"), "-t", base, str(wt)
    ]  # fmt: skip
    assert host.calls[-1] == ["docker", "build", "-t", tag, str(ctx)]
    assert (ctx / "Dockerfile").read_text().splitlines()[1] == f"FROM {base}"
    assert (ctx / "kvm-wrapper.sh").is_file() and not (ctx / "tmp").exists()


def layer(tmp_path, wrapper="#!/bin/sh\n"):
    rng = tmp_path / "range" / "docker"
    (rng / "tmp").mkdir(parents=True, exist_ok=True)
    (rng / "kvm-wrapper.sh").write_text(wrapper)
    dockerfile = rng / "minimega-arm64.Dockerfile"
    dockerfile.write_text("FROM minimega:arm64\nCOPY kvm-wrapper.sh /k\n")
    return str(rng / "minimega-arm64.Dockerfile")


def test_a_plain_build_of_the_sha_does_not_stand_in_for_the_layered_one(settings, tmp_path):
    # burger-fr8: a board without the route's layer built TAG; an arm64 board then reused it
    host = FakeHost(settings=settings)  # every tag "exists"
    tag = minimega.build_image(host, tmp_path / "minimega", SHA, layer(tmp_path))
    assert tag != TAG
    assert host.calls[0] == ["docker", "image", "inspect", tag]


def test_editing_the_layer_or_its_files_changes_the_tag(settings, tmp_path):
    host = FakeHost(settings=settings)
    one = minimega.build_image(host, tmp_path / "minimega", SHA, layer(tmp_path))
    two = minimega.build_image(host, tmp_path / "minimega", SHA, layer(tmp_path, "#!/bin/sh\nx\n"))
    (tmp_path / "range" / "docker" / "tmp" / "state").write_text("bind-mount state")
    three = minimega.build_image(
        host, tmp_path / "minimega", SHA, layer(tmp_path, "#!/bin/sh\nx\n")
    )
    assert one != two == three  # tmp/ is not copied into the build, so it is not hashed


def test_build_image_with_a_missing_layer_file(settings, tmp_path):
    host = FakeHost({"docker image inspect": cp(rc=1)}, settings)
    with pytest.raises(BurgerError, match="minimega_layer"):
        minimega.build_image(host, tmp_path / "minimega", SHA, str(tmp_path / "nope.Dockerfile"))


# --- swap and restore ---

INSPECT = "docker inspect -f {{json .Config.Labels}} minimega"
BASE = "docker compose -p stack --project-directory /srv/phenix -f /srv/phenix/a.yml"
UP = "up -d --no-deps --force-recreate minimega"


PY_AT = "/usr/local/lib/python3.12/dist-packages/minimega.py"


def labels(settings, sha=None, work=None, image=None):
    """The minimega container's labels: stock, or swapped by burger to `sha` (from `work`);
    `image` is the tag the swap ran, absent on swaps from before burger-fr8."""
    files = "/srv/phenix/a.yml"
    if sha:
        files += f",{work or settings.work}/{update.OVERRIDE}"
    got = {
        "com.docker.compose.project.working_dir": "/srv/phenix",
        "com.docker.compose.project.config_files": files,
        "com.docker.compose.service": "minimega",
        "com.docker.compose.project": "stack",
        **({"burger.minimega": sha} if sha else {}),
        **({minimega.IMAGE_LABEL: image} if image else {}),
    }
    return cp(json.dumps(got))


class MmHost(FakeHost):
    """A FakeHost whose minimega labels follow the compose `up` it is given, as docker's do,
    unless `sticky` keeps them (a restore that did not take)."""

    sticky = False

    def run(self, cmd, check=True, capture=False, **kw):
        got = super().run(cmd, check, capture, **kw)
        line = " ".join(str(c) for c in cmd)
        if line.startswith("docker compose") and " up " in line and not self.sticky:
            override = next((c for c in cmd if str(c).endswith(update.OVERRIDE)), None)
            got_labels = yaml.safe_load(open(override))["services"]["minimega"]["labels"] if (
                override
            ) else {}  # fmt: skip
            self.responses[INSPECT] = labels(
                self.s, got_labels.get(minimega.LABEL), image=got_labels.get(minimega.IMAGE_LABEL)
            )
        return got


class StrictMmHost(MmHost, StrictHost):
    """MmHost that keeps the real Host's contract (exit codes raise, output needs capture)."""


def mm_host(settings, sha=None, running=(), work=None, image=None, cls=MmHost):
    host = cls(
        {INSPECT: labels(settings, sha, work, image),
         f"docker exec phenix python3 -c {minimega.PY_FILE}": cp(PY_AT + "\n")},
        settings,
    )  # fmt: skip
    host.api_responses["/experiments"] = {
        "experiments": [{"name": n, "running": True} for n in running]
    }
    return host


def joined(host):
    return [" ".join(c) for c in host.calls]


def test_swap_recreates_minimega_with_the_override(settings):
    host = mm_host(settings)
    minimega.ensure(host, settings, SHA)
    override = update.burger_override(settings)
    assert override == settings.work / "minimega" / "compose.override.yml"
    assert yaml.safe_load(override.read_text()) == {
        "services": {
            "minimega": {
                "image": TAG,
                "pull_policy": "never",
                "labels": {"burger.minimega": SHA, minimega.IMAGE_LABEL: TAG},
            }
        }
    }
    calls = joined(host)
    up = calls.index(f"{BASE} -f {override} {UP}")
    poll = calls.index("docker exec phenix phenix mm version")
    cp_out = calls.index(
        f"docker cp minimega:/opt/minimega/lib/minimega.py {settings.work}/minimega/minimega.py"
    )
    put = calls.index(f"docker cp {settings.work}/minimega/minimega.py phenix:{PY_AT}")
    assert up < poll < cp_out < put
    assert not any("pip install" in c for c in calls)  # minimega's lib needs setuptools>=77


def test_swap_to_another_sha_names_the_override_once(settings):
    host = mm_host(settings, "d" * 40)
    minimega.ensure(host, settings, SHA)
    assert f"{BASE} -f {update.burger_override(settings)} {UP}" in joined(host)


def test_same_sha_is_a_no_op_even_while_up(settings):
    host = mm_host(settings, SHA, running=["ebwes-x"])
    minimega.ensure(host, settings, SHA)
    assert not any("compose" in c for c in joined(host))
    # a recreated phēnix container has the stock minimega.py again: put ours back
    assert any(f"phenix:{PY_AT}" in c for c in joined(host))


def test_a_plain_build_running_is_swapped_for_the_layered_build_of_the_same_sha(settings):
    # burger-fr8: same commit, but the board wants its route's layer on top
    layered = TAG + "-0123abcd"
    host = mm_host(settings, SHA)  # a swap from before burger-fr8: no image label, plain TAG
    minimega.ensure(host, settings, SHA, layered)
    override = yaml.safe_load(update.burger_override(settings).read_text())
    assert override["services"]["minimega"]["image"] == layered
    assert minimega.running_image(host, settings) == layered


def test_the_same_image_is_a_no_op(settings):
    layered = TAG + "-0123abcd"
    host = mm_host(settings, SHA, running=["ebwes-x"], image=layered)
    minimega.ensure(host, settings, SHA, layered)
    assert not any("compose" in c for c in joined(host))


def test_stock_board_on_a_stock_host_does_nothing(settings):
    host = mm_host(settings)
    minimega.ensure(host, settings, None)
    assert joined(host) == [INSPECT]


def test_swap_refuses_while_an_experiment_runs(settings):
    host = mm_host(settings, running=["ebwes-other"])
    with pytest.raises(BurgerError, match="experiments running: ebwes-other"):
        minimega.ensure(host, settings, SHA)
    assert not any("compose" in c for c in joined(host))


def _api_down(path):
    raise OSError("refused")


@pytest.mark.parametrize("api", [_api_down, lambda path: None])
def test_api_errors_do_not_name_a_force_flag_install_up_and_down_lack(settings, api):
    for call in (
        lambda h: minimega.ensure(h, settings, SHA),
        lambda h: minimega.restore(h, settings),
    ):
        host = mm_host(settings, "f" * 40)
        host.api = api
        with pytest.raises(BurgerError) as e:
            call(host)
        assert "experiments" in str(e.value) and "--force" not in str(e.value)


def test_restore_uses_the_original_files_only(settings):
    host = mm_host(settings, SHA)
    minimega.restore(host, settings)
    calls = joined(host)
    assert f"{BASE} {UP}" in calls
    assert not any("compose.override.yml" in c for c in calls)
    assert any(f"phenix:{PY_AT}" in c for c in calls)  # stock minimega.py back in phēnix


def test_restore_is_a_no_op_on_stock(settings):
    host = mm_host(settings)
    minimega.restore(host, settings)
    assert joined(host) == [INSPECT]


def test_restore_skips_while_another_experiment_runs(settings, messages):
    host = mm_host(settings, SHA, running=["ebwes-other"])
    minimega.restore(host, settings)
    assert not any("compose" in c for c in joined(host))
    assert [m for lvl, m in messages if lvl == "WARNING" and "ebwes-other" in m]


def test_board_without_minimega_restores_a_leftover_swap(settings):
    host = mm_host(settings, SHA)
    minimega.ensure(host, settings, None)
    assert f"{BASE} {UP}" in joined(host)


def test_no_minimega_container_setting(settings):
    s = dataclasses.replace(settings, mm_ctr="")
    host = FakeHost(settings=s)
    with pytest.raises(BurgerError, match="BURGER_MINIMEGA_CONTAINER"):
        minimega.ensure(host, s, SHA)
    minimega.ensure(host, s, None)
    minimega.restore(host, s)
    assert host.calls == []


def test_minimega_not_under_compose(settings):
    host = FakeHost({INSPECT: cp("null")}, settings)
    with pytest.raises(BurgerError, match="must run under docker compose"):
        minimega.ensure(host, settings, SHA)


def board_built(settings, mm):
    rd = settings.work / "baseline"
    (rd / "out").mkdir(parents=True)
    man = {**MAN, "minimega": mm}
    (rd / "manifest.json").write_text(json.dumps(man))
    (rd / "out" / "built.json").write_text(json.dumps(experiment._built_key(man)))


def test_install_swaps_after_the_apps(settings):
    board_built(settings, {"pr": 1782, "sha": SHA, "from": "board"})
    host = mm_host(settings)
    experiment.install(res(settings), settings, host)
    calls = joined(host)
    marker = next(i for i, c in enumerate(calls) if "installed.json" in c)
    assert marker < calls.index(f"{BASE} -f {update.burger_override(settings)} {UP}")


def test_up_puts_the_boards_minimega_back_after_a_down(settings, monkeypatch):
    # `burger down` restored stock; `burger up` alone must not start the board on it
    board_built(settings, {"pr": 1782, "sha": SHA, "from": "board"})
    monkeypatch.setattr(experiment, "render", lambda *a: ({}, {}))
    monkeypatch.setattr(experiment, "wait_running", lambda *a: None)
    monkeypatch.setattr(experiment, "fix_routers", lambda *a: None)
    host = mm_host(settings)
    experiment.up(res(settings), settings, host, 1)
    calls = joined(host)
    swap = calls.index(f"{BASE} -f {update.burger_override(settings)} {UP}")
    # before miniccc is copied out, so the VMs get the swapped minimega's miniccc
    assert swap < next(i for i, c in enumerate(calls) if "/opt/minimega/bin/miniccc" in c)
    assert swap < next(i for i, c in enumerate(calls) if "experiment start" in c)


def test_down_command_restores_stock(settings, monkeypatch):
    host = mm_host(settings, SHA)
    cli._one(argparse.Namespace(cmd="down"), settings, host, "baseline")
    calls = joined(host)
    assert calls.index(f"{BASE} {UP}") > next(
        i for i, c in enumerate(calls) if "config delete" in c
    )


def test_install_without_minimega_restores_before_anything_else(settings):
    board_built(settings, None)
    host = mm_host(settings, SHA)
    experiment.install(res(settings), settings, host)
    calls = joined(host)
    assert calls.index(f"{BASE} {UP}") < next(i for i, c in enumerate(calls) if "apps-python" in c)


def test_restore_fails_when_the_override_comes_back(settings):
    host = mm_host(settings, SHA)
    host.sticky = True
    with pytest.raises(BurgerError, match="still runs burger's build after the restore"):
        minimega.restore(host, settings)


def test_restore_drops_an_override_from_another_checkout(settings, tmp_path):
    host = mm_host(settings, SHA, work=tmp_path / "elsewhere" / "work")
    minimega.restore(host, settings)
    assert f"{BASE} {UP}" in joined(host)


def test_phenix_without_a_minimega_py_file_is_an_error(settings):
    host = mm_host(settings)
    host.responses[f"docker exec phenix python3 -c {minimega.PY_FILE}"] = cp("\n")
    with pytest.raises(BurgerError, match=r"not a minimega\.py file"):
        minimega.ensure(host, settings, SHA)


# --- burger update --only containers ---

CP_OUT = f"docker cp minimega:{minimega.LIB}/minimega.py"


def update_containers(settings, host, dry=False):
    said = []
    update.update(settings, host, ["containers"], dry, False, said.append)
    return said


def test_update_containers_puts_stock_minimega_py_back(settings, monkeypatch):
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    host = mm_host(settings, SHA)
    update_containers(settings, host)
    calls = joined(host)
    out = next(i for i, c in enumerate(calls) if c.startswith(CP_OUT))
    assert calls.index(f"{BASE} up -d --no-deps minimega") < out  # from the stock container
    assert any(c.startswith("docker cp") and c.endswith(f"phenix:{PY_AT}") for c in calls[out:])


def test_update_containers_leaves_minimega_py_alone_on_stock(settings):
    host = mm_host(settings)
    update_containers(settings, host)
    assert not any(c.startswith("docker cp") for c in joined(host))


def test_update_containers_keeps_minimega_py_while_still_swapped(settings):
    host = mm_host(settings, SHA)
    host.sticky = True  # e.g. the minimega service was not recreated
    update_containers(settings, host)
    assert not any(c.startswith("docker cp") for c in joined(host))


def test_update_containers_dry_run_only_says_it(settings):
    host = mm_host(settings, SHA)
    said = update_containers(settings, host, dry=True)
    assert any("minimega.py" in line for line in said)
    assert not any(c.startswith("docker cp") for c in joined(host))
    assert not (settings.work / "minimega").exists()


def test_asking_docker_whether_a_tag_is_built_is_quiet_and_a_miss_is_not_an_error(
    settings, tmp_path
):
    host = StrictHost({"docker image inspect": cp(rc=1)}, settings)
    loud = watch_loud(host)
    minimega.build_image(host, tmp_path / "minimega", SHA)
    assert not [c for c in loud if "image inspect" in c]
    assert any(c[:2] == ["docker", "build"] for c in host.calls)  # the miss built it


def test_install_makes_the_work_directory_and_reads_phenixs_import_path_captured(
    settings, tmp_path
):
    deep = dataclasses.replace(settings, work=tmp_path / "not" / "yet")
    host = mm_host(deep, cls=StrictMmHost)
    minimega.ensure(host, deep, SHA)
    assert any(f"phenix:{PY_AT}" in c for c in joined(host))


def test_phenix_without_a_minimega_py_names_where_it_imports_from(settings):
    host = mm_host(settings)
    host.responses[f"docker exec phenix python3 -c {minimega.PY_FILE}"] = cp("\n")
    with pytest.raises(BurgerError, match="minimega from nowhere, not"):
        minimega.ensure(host, settings, SHA)
    host.responses[f"docker exec phenix python3 -c {minimega.PY_FILE}"] = cp("/x/minimega.pyc\n")
    with pytest.raises(BurgerError, match=r"minimega from /x/minimega\.pyc, not"):
        minimega.ensure(host, settings, SHA)


def test_py_reason_survives_unreadable_hashes_and_names_what_each_side_has(settings):
    sha = "docker exec minimega sha256sum"
    theirs = "docker exec phenix python3 -c"
    gone = StrictHost({sha: cp(rc=1), theirs: cp(rc=1)}, settings)
    reason = minimega._py_reason(gone, settings, "b")
    assert "container has none" in reason and "imports no minimega" in reason
    assert "burger install b" in reason
    differ = StrictHost({sha: cp("a" * 64 + "  f\n"), theirs: cp("b" * 64 + "\n")}, settings)
    reason = minimega._py_reason(differ, settings, "b")
    assert f"imports {'b' * 12}," in reason and f"container has {'a' * 12};" in reason
    same = StrictHost({sha: cp("a" * 64 + "  f\n"), theirs: cp("a" * 64 + "\n")}, settings)
    assert minimega._py_reason(same, settings, "b") == ""
