import datetime as dt
import json
import subprocess

import pytest

from burger import update
from burger.cli import main
from burger.config import BurgerError
from tests.fakes import FakeHost, StrictHost


def cp(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


@pytest.fixture
def lines():
    return []


def host_for(settings, responses=None):
    return FakeHost(responses or {}, settings)


def run_update(settings, host, lines, only, dry=False, force=False):
    return update.update(settings, host, only, dry, force, lines.append)


def git(settings, *a):
    return " ".join(["git", "-C", str(settings.root), *a])


# --- burger layer ---


def test_burger_refuses_dirty_checkout(settings, lines):
    h = host_for(settings, {git(settings, "status"): cp(" M src/x.py\n")})
    with pytest.raises(BurgerError, match="uncommitted"):
        run_update(settings, h, lines, ["burger"])
    assert not any("pull" in c for call in h.calls for c in call)


def test_burger_refuses_other_branch(settings, lines):
    h = host_for(settings, {git(settings, "rev-parse --abbrev-ref"): cp("feat-x\n")})
    with pytest.raises(BurgerError, match="'feat-x', not main"):
        run_update(settings, h, lines, ["burger"])


def heads(settings, before, after, base=FakeHost):
    """Responses where HEAD is `before` until the pull, `after` from then on."""

    class Moving(base):
        def run(self, cmd, check=True, capture=False, **kw):
            res = super().run(cmd, check, capture, **kw)
            if "pull" in cmd:
                self.responses[git(settings, "rev-parse HEAD")] = cp(after + "\n")
            return res

    return Moving(
        {
            git(settings, "rev-parse --abbrev-ref"): cp("main\n"),
            git(settings, "rev-parse HEAD"): cp(before + "\n"),
        },
        settings,
    )


def test_burger_reexecs_with_remaining_layers_when_head_moves(settings, lines, monkeypatch):
    execs = []

    def execv(path, argv):
        execs.append((path, argv))
        raise SystemExit  # a real exec never returns

    monkeypatch.setattr(update.os, "execv", execv)
    h = heads(settings, "a" * 40, "b" * 40)
    with pytest.raises(SystemExit):
        run_update(settings, h, lines, ["burger", "containers", "bennu"], force=True)
    assert execs == [
        (
            settings.root / "burger",
            ["burger", "update", "--only", "containers,bennu", "--force"],
        )
    ]
    assert ["git", "-C", str(settings.root), "pull", "--quiet", "--ff-only"] in h.calls
    assert [
        str(settings.root / ".venv/bin/pip"),
        "install",
        "-q",
        "-e",
        str(settings.root),
    ] in h.calls


def test_burger_stays_put_when_head_is_unchanged(settings, lines, monkeypatch):
    monkeypatch.setattr(update.os, "execv", lambda *a: pytest.fail("re-exec"))
    h = heads(settings, "a" * 40, "a" * 40)
    rows = run_update(settings, h, lines, ["burger", "containers"])
    assert rows[0] == ("burger", "a" * 10, "a" * 10, "already current")


def test_burger_dry_run_does_not_claim_already_current(settings, lines):
    h = heads(settings, "a" * 40, "a" * 40)
    rows = run_update(settings, h, lines, ["burger"], dry=True)
    assert rows[0][3] == "dry-run: not pulled"


def test_burger_last_layer_does_not_reexec(settings, lines, monkeypatch):
    monkeypatch.setattr(update.os, "execv", lambda *a: pytest.fail("re-exec"))
    run_update(settings, heads(settings, "a" * 40, "b" * 40), lines, ["burger"])


def test_unknown_layer(settings, lines):
    with pytest.raises(BurgerError, match="unknown layer 'nope'"):
        run_update(settings, host_for(settings), lines, ["nope"])


def test_dry_run_runs_only_probes(settings, lines):
    h = host_for(settings, {git(settings, "rev-parse --abbrev-ref"): cp("main\n")})
    run_update(settings, h, lines, ["burger"], dry=True)
    assert not any("pull" in call or "pip" in call[0] for call in h.calls)
    assert any(line.startswith("+ git -C") and "pull" in line for line in lines)


def test_cli_update_prints_summary(settings, monkeypatch, capsys):
    monkeypatch.setattr("burger.cli.Host", lambda s, site: heads(s, "a" * 40, "a" * 40))
    monkeypatch.setattr("burger.cli.Settings.from_env", lambda: settings)
    assert main(["--quiet", "update", "--only", "burger"]) == 0
    assert "already current" in capsys.readouterr().out
    assert main(["--quiet", "update", "--only", "bogus"]) == 1


# --- containers layer ---

LABELS = (
    '{"com.docker.compose.project.working_dir":"/srv/phenix",'
    '"com.docker.compose.project.config_files":"/srv/phenix/a.yml,/srv/phenix/b.yml",'
    '"com.docker.compose.service":"phenix","com.docker.compose.project":"stack"}'
)
INSPECT = "docker inspect -f {{json .Config.Labels}} "


def compose_host(settings, labels=LABELS, **extra):
    return host_for(settings, {INSPECT + settings.ctr: cp(labels), **extra})


def test_compose_labels_parsed(settings):
    h = compose_host(settings)
    assert update.compose_labels(h, "phenix") == {
        "dir": "/srv/phenix",
        "files": ["/srv/phenix/a.yml", "/srv/phenix/b.yml"],
        "service": "phenix",
        "project": "stack",
    }


@pytest.mark.parametrize("labels", ["null", "{}", '{"com.docker.compose.service":"x"}'])
def test_not_compose_managed(settings, labels):
    assert update.compose_labels(compose_host(settings, labels), "phenix") is None


def test_containers_pull_and_up_through_compose(settings, lines, monkeypatch):
    h = compose_host(settings)
    h.responses["docker inspect -f {{json .Config.Labels}} " + settings.mm_ctr] = cp("null")
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    rows = run_update(settings, h, lines, ["containers"])
    base = (
        "docker compose -p stack --project-directory /srv/phenix"
        " -f /srv/phenix/a.yml -f /srv/phenix/b.yml"
    )
    joined = [" ".join(c) for c in h.calls]
    assert joined.index(f"{base} pull phenix") < joined.index(f"{base} up -d --no-deps phenix")
    assert "docker exec phenix phenix mm version" in joined
    assert [r[0] for r in rows] == ["phenix", "minimega"]
    assert "not managed by compose" in rows[1][3]
    assert not any(" pull " in j and "minimega" in j for j in joined)


def test_containers_report_image_change(settings, lines, monkeypatch):
    ids = iter(["sha256:aaaaaaaaaaaaaaaa\n", "sha256:bbbbbbbbbbbbbbbb\n"])

    class Host(FakeHost):
        def run(self, cmd, check=True, capture=False, **kw):
            res = super().run(cmd, check, capture, **kw)
            if cmd[:4] == ["docker", "inspect", "-f", "{{.Image}}"] and cmd[4] == "phenix":
                return cp(next(ids))
            return res

    h = Host({INSPECT + "phenix": cp(LABELS), INSPECT + "minimega": cp("null")}, settings)
    rows = run_update(settings, h, lines, ["containers"])
    assert rows[0] == ("phenix", "aaaaaaaaaaaa", "bbbbbbbbbbbb", "recreated")


def test_local_build_is_reported_not_rebuilt(settings, lines):
    cfg = '{"services":{"phenix":{"build":{"context":"."}}}}'
    key = "docker compose -p stack --project-directory /srv/phenix"
    h = compose_host(settings, **{key: cp(cfg)})
    h.responses[INSPECT + "minimega"] = cp("null")
    rows = run_update(settings, h, lines, ["containers"])
    assert "local context" in rows[0][3]
    assert not any(c[:2] == ["docker", "compose"] and "pull" in c for c in h.calls)


def test_running_experiment_refuses_then_force_proceeds(settings, lines, monkeypatch):
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    h = compose_host(settings)
    h.responses[INSPECT + "minimega"] = cp("null")
    h.api_responses["/experiments"] = {"experiments": [{"name": "a", "running": True}]}
    with pytest.raises(BurgerError, match="experiments running: a"):
        run_update(settings, h, lines, ["containers"])
    assert not any("compose" in c for call in h.calls for c in call[:2])
    run_update(settings, h, lines, ["containers"], force=True)
    assert any(c[:2] == ["docker", "compose"] and "up" in c for c in h.calls)


def test_dry_run_containers_prints_compose_and_runs_none(settings, lines):
    h = compose_host(settings)
    h.responses[INSPECT + "minimega"] = cp("null")
    rows = run_update(settings, h, lines, ["containers"], dry=True)
    assert rows[0][3] == "dry-run"
    assert any("pull phenix" in line for line in lines)
    assert any("up -d --no-deps phenix" in line for line in lines)
    assert not any(" pull " in " ".join(c) or " up " in " ".join(c) for c in h.calls)


def test_force_skips_the_guard_even_when_the_api_is_down(settings, lines, monkeypatch):
    def down(path):
        raise OSError("refused")

    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    h = compose_host(settings)
    h.responses[INSPECT + "minimega"] = cp("null")
    h.api = down
    with pytest.raises(BurgerError, match=r"cannot list.*pass --force"):
        run_update(settings, h, lines, ["containers"])
    run_update(settings, h, lines, ["containers"], force=True)
    assert any(c[:2] == ["docker", "compose"] and "up" in c for c in h.calls)


def test_api_error_status_is_not_read_as_no_experiments(settings, lines):
    h = compose_host(settings)
    h.api_responses["/experiments"] = None
    with pytest.raises(
        BurgerError, match=r"answered the experiments request with an error.*pass --force"
    ):
        run_update(settings, h, lines, ["containers"])
    assert not any("compose" in c for call in h.calls for c in call[:2])


def test_project_name_is_left_out_when_the_label_is_missing():
    c = {"dir": "/d", "files": ["/d/a.yml"], "service": "s", "project": None}
    assert update._compose(c)[:3] == ["docker", "compose", "--project-directory"]


# --- apps layer ---


def test_apps_layer_goes_through_the_main_board(settings, lines, monkeypatch):
    seen = []
    man = {"apps": {"head": "f" * 40}}
    monkeypatch.setattr(update.vcs, "sync", lambda res, s, h: seen.append(("sync", res)) or man)
    monkeypatch.setattr(update.experiment, "build", lambda res, s, h: seen.append(("build", res)))
    monkeypatch.setattr(
        update.experiment, "install", lambda res, s, h: seen.append(("install", res))
    )
    h = host_for(
        settings,
        {"docker exec phenix cat /tmp/burger/installed.json": cp('{"apps": "' + "e" * 40 + '"}')},
    )
    rows = run_update(settings, h, lines, ["apps"])
    assert [k for k, _ in seen] == ["sync", "build", "install"]
    board = seen[0][1].board
    assert (board.name, board.apps_branch, board.apps_prs, board.items) == (
        "main",
        {"branch": "main", "remote": "upstream"},
        [],
        {},
    )
    assert board.bennu is None
    assert rows == [("apps", "e" * 10, "f" * 10, "installed from upstream main")]


def test_apps_dry_run_prints_steps_and_changes_nothing(settings, lines, monkeypatch):
    monkeypatch.setattr(update.vcs, "sync", lambda *a: pytest.fail("sync"))
    sha = "9" * 40
    h = host_for(settings, {"git ls-remote": cp(f"{sha}\trefs/heads/main\n")})
    rows = run_update(settings, h, lines, ["apps"], dry=True)
    assert [line for line in lines if line.startswith("+ burger")] == [
        f"+ burger {step} main  (upstream phenix-apps main {sha[:10]})"
        for step in ("sync", "build", "install")
    ]
    assert rows[0][2] == sha[:10]


# --- bennu layer ---

REL = "https://github.com/sandialabs/sceptre-bennu/releases/latest/download"
WHL = "pybennu-6.0.0-py3-none-any.whl"
BENNU_SH = f"""\
wget {REL}/helics_3.6.1-1_amd64.deb -O /tmp/helics.deb
apt-get install -y /tmp/helics.deb
rm -f /tmp/helics.deb

# bennu (C++)
wget {REL}/bennu.deb -O /tmp/bennu.deb
apt-get install -y /tmp/bennu.deb
rm -f /tmp/bennu.deb

# pybennu (Python)
wget {REL}/{WHL} -O /tmp/{WHL}
pip install /tmp/{WHL}
rm -f /tmp/{WHL}

# Cleanup
"""
SHA = "1234567" + "0" * 33
CTR = "docker exec phenix "
IMGS = "/phenix/images"


def test_patch_bennu_sh_swaps_only_what_burger_builds():
    out = update.patch_bennu_sh(BENNU_SH)
    assert "apt-get install -y /root/burger-debs/bennu.deb\n" in out
    assert "pip install /root/burger-debs/pybennu-*.whl\nrm -rf /root/burger-debs\n" in out
    assert "download/helics_3.6.1-1_amd64.deb" in out  # not built here: still downloaded
    assert "download/bennu.deb" not in out
    assert "download/pybennu" not in out


def test_patch_bennu_sh_refuses_a_script_it_does_not_know():
    with pytest.raises(BurgerError, match="no longer downloads"):
        update.patch_bennu_sh("echo nothing to patch\n")


def point(settings, responses, cls=FakeHost):
    h = cls(responses, settings)
    ctx = update.Ctx(settings, h, [].append)
    return h, update.point_link(ctx, "bennu-main-1234567.qc2")


def test_point_link_backs_up_a_regular_file(settings, monkeypatch):
    monkeypatch.setattr(update, "_now", lambda: dt.datetime(2026, 10, 22, 9, 8, 7))
    h, rollback = point(
        settings,
        {
            f"{CTR}test -L {IMGS}/bennu.qc2": cp(rc=1),
            f"{CTR}test -e {IMGS}/bennu.qc2": cp(),
            f"{CTR}test -e {IMGS}/bennu-20261022.qc2": cp(rc=1),
        },
    )
    cmds = [" ".join(c) for c in h.calls]
    mv = cmds.index(f"{CTR}mv {IMGS}/bennu.qc2 {IMGS}/bennu-20261022.qc2")
    assert mv < cmds.index(f"{CTR}ln -sfn bennu-main-1234567.qc2 {IMGS}/bennu.qc2")
    assert rollback == f"{CTR}ln -sfn {IMGS}/bennu-20261022.qc2 {IMGS}/bennu.qc2"


def test_point_link_never_overwrites_an_existing_backup(settings, monkeypatch):
    monkeypatch.setattr(update, "_now", lambda: dt.datetime(2026, 10, 22, 9, 8, 7))
    h, _ = point(settings, {f"{CTR}test -L ": cp(rc=1)})  # all other tests: exists
    assert f"{CTR}mv {IMGS}/bennu.qc2 {IMGS}/bennu-20261022-090807.qc2" in [
        " ".join(c) for c in h.calls
    ]


def test_point_link_repoints_an_existing_symlink_without_a_backup(settings):
    h, rollback = point(
        settings,
        {f"{CTR}test -L {IMGS}/bennu.qc2": cp(), f"{CTR}readlink": cp("bennu-main-0000000.qc2\n")},
    )
    assert not any("mv" in c for c in h.calls)
    assert rollback == f"{CTR}ln -sfn bennu-main-0000000.qc2 {IMGS}/bennu.qc2"


@pytest.fixture
def images(tmp_path, monkeypatch):
    d = tmp_path / "images"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "bennu.sh").write_text(BENNU_SH)
    monkeypatch.setenv("BURGER_IMAGES_DIR", str(d))
    return d


def bennu_responses(d, **extra):
    return {
        f"git ls-remote {config_repo()} refs/heads/main": cp(f"{SHA}\trefs/heads/main\n"),
        f"{CTR}test -L {IMGS}/bennu.qc2": cp(rc=1),
        f"{CTR}test -e {IMGS}/bennu.qc2": cp(rc=1),
        f"{CTR}test -e {IMGS}/bennu-main-": cp(rc=1),
        f"{CTR}phenix cfg list": cp("Image  ...  x  y  z  bennu\n"),
        f"{CTR}test -e {d}/bennu.qc2": cp(rc=1),
        f"{CTR}test -e {d}/bennu.tar": cp(rc=1),
        **extra,
    }


def config_repo():
    from burger.config import REPOS

    return REPOS["bennu"]


def test_missing_shared_images_dir_is_an_error(settings, lines, images):
    h = host_for(settings, {f"{CTR}test -d {images}": cp(rc=1)})
    with pytest.raises(BurgerError, match="not a directory in the phēnix container"):
        run_update(settings, h, lines, ["bennu"])
    assert not any("sync" in c for call in h.calls for c in call)


def test_bennu_refuses_while_an_experiment_runs(settings, lines, images):
    h = host_for(settings)
    h.api_responses["/experiments"] = {"experiments": [{"name": "x", "running": True}]}
    with pytest.raises(BurgerError, match="experiments running: x"):
        run_update(settings, h, lines, ["bennu"])


def fake_build(monkeypatch, settings):
    man = {"bennu": {"sha": SHA}}

    def build(host, wt, out, m, install, pip):
        (out / "bennu.deb").write_text("deb")
        (out / "pybennu-6.1-py3-none-any.whl").write_text("whl")

    monkeypatch.setattr(update.vcs, "sync", lambda res, s, h: man)
    monkeypatch.setattr(update, "build_bennu", build)


def test_bennu_builds_image_and_points_the_link(settings, lines, images, monkeypatch):
    fake_build(monkeypatch, settings)
    h = host_for(settings, bennu_responses(images))
    rows = run_update(settings, h, lines, ["bennu"])
    cmds = [" ".join(c) for c in h.calls]
    d = str(images)
    steps = [
        f"{CTR}phenix image delete bennu",
        f"{CTR}phenix image create -O {d}/overlays/bennu,{d}/overlays/brash,"
        f"{d}/overlays/burger-bennu -T {d}/scripts/bennu-burger.sh --compress bennu",
        f"{CTR}phenix image build -o {d} -c -x bennu",
        f"{CTR}phenix image inject-miniexe /phenix/miniccc {d}/bennu.qc2",
        f"{CTR}mv {d}/bennu.qc2 {IMGS}/bennu-main-1234567.qc2",
        f"{CTR}ln -sfn bennu-main-1234567.qc2 {IMGS}/bennu.qc2",
    ]
    assert [cmds.index(x) for x in steps] == sorted(cmds.index(x) for x in steps)
    assert rows == [
        ("bennu", "none", "bennu-main-1234567.qc2", f"built from bennu main {SHA[:10]}")
    ]
    assert (images / "overlays/burger-bennu/root/burger-debs/bennu.deb").is_file()
    assert (
        images / "overlays/burger-bennu/root/burger-debs/pybennu-6.1-py3-none-any.whl"
    ).is_file()
    assert "/root/burger-debs/bennu.deb" in (images / "scripts/bennu-burger.sh").read_text()
    assert any(line.startswith("to roll back: ") for line in lines)


def test_bennu_skips_the_build_when_the_image_exists(settings, lines, images, monkeypatch):
    monkeypatch.setattr(update.vcs, "sync", lambda *a: pytest.fail("sync"))
    resp = bennu_responses(images)
    resp[f"{CTR}test -e {IMGS}/bennu-main-"] = cp()
    h = host_for(settings, resp)
    rows = run_update(settings, h, lines, ["bennu"])
    assert not any("image" in c and "create" in c for c in h.calls)
    assert rows[0][2] == "bennu-main-1234567.qc2"


def test_bennu_dry_run_changes_nothing(settings, lines, images):
    h = host_for(settings, bennu_responses(images))
    run_update(settings, h, lines, ["bennu"], dry=True)
    changing = ("create", "build", "delete", "inject-miniexe", "mv", "ln", "rm", "clone", "fetch")
    assert not [c for c in h.calls if any(w in c for w in changing)]
    assert not (images / "overlays").exists()
    assert any("phenix image create" in line and "bennu-burger.sh" in line for line in lines)
    assert any(
        line.startswith("+ docker exec phenix ln -sfn bennu-main-1234567.qc2") for line in lines
    )


def test_bennu_dry_run_without_images_dir_prints_the_plan(settings, lines, images):
    h = host_for(settings, bennu_responses(images, **{f"{CTR}test -d {images}": cp(rc=1)}))
    run_update(settings, h, lines, ["bennu"], dry=True)
    assert any(line.startswith("! a real run would stop here") for line in lines)
    assert any("phenix image create" in line for line in lines)


def test_bennu_never_deletes_a_leftover_image(settings, lines, images, monkeypatch):
    fake_build(monkeypatch, settings)
    resp = bennu_responses(images, **{f"{CTR}test -e {images}/bennu.tar": cp()})
    h = host_for(settings, resp)
    with pytest.raises(BurgerError, match=r"bennu\.tar exists"):
        run_update(settings, h, lines, ["bennu"])
    assert not any("rm" in c for c in h.calls)


def test_compose_labels_drop_burgers_override(settings):
    labels = LABELS.replace("/srv/phenix/b.yml", str(update.burger_override(settings)))
    c = update.compose_labels(compose_host(settings, labels), "phenix")
    assert c["files"] == ["/srv/phenix/a.yml"]


# --- pins: behaviour the real Host contract (StrictHost) makes visible ---


def strict_for(settings, responses=None, **kw):
    return StrictHost(responses or {}, settings, **kw)


def test_dry_host_follows_the_host_contract_for_probes():
    h = StrictHost({"test -e x": cp(rc=1)})
    dry = update.dry_host(h, [].append)
    with pytest.raises(BurgerError):  # check defaults to True, as on the real host
        dry.run(["test", "-e", "x"])
    assert dry.run(["test", "-e", "x"], check=False).returncode == 1
    assert dry.run(["test", "-e", "y"]).stdout is None  # capture defaults to False


def test_dry_host_prints_what_it_skips_and_reports_success():
    said = []
    h = StrictHost({})
    res = update.dry_host(h, said.append).run(["rm", "-f", "x"])
    assert res.returncode == 0
    assert said == ["+ rm -f x"]
    assert h.calls == []


@pytest.mark.parametrize(
    ("cmd", "probe"),
    [
        (["phenix", "version"], True),
        (["docker", "exec", "c", "phenix", "version"], True),
        (["docker", "exec", "c", "phenix", "mm", "version"], True),
        (["docker", "exec", "c", "phenix", "cfg", "list"], True),
        (["docker", "exec", "c", "phenix", "experiment", "list"], True),
        (["docker", "exec", "c", "phenix", "image", "delete", "bennu"], False),
        (["docker", "exec", "c", "phenix", "cfg", "delete", "x"], False),
        (["docker", "exec", "c", "phenix", "experiment", "stop", "x"], False),
        (["docker", "exec", "c", "phenix", "mm", "kill"], False),
        (["docker", "exec", "c", "version"], False),
        (["docker", "exec", "c", "cat", "/x"], True),
        (["docker", "exec", "c", "rm", "/x"], False),
        (["docker", "inspect", "c"], True),
        (["docker", "compose", "config"], True),
        (["docker", "compose", "up"], False),
        (["git", "-C", "/r", "status"], True),
        (["git", "-C", "/r", "pull"], False),
    ],
)
def test_dry_host_runs_only_probes(cmd, probe):
    h = StrictHost({})
    update.dry_host(h, [].append).run(cmd)
    assert (h.calls == [cmd]) is probe


def test_burger_layer_under_the_host_contract(settings, lines, monkeypatch):
    execs = []

    def execv(path, argv):
        execs.append(argv)
        raise SystemExit

    monkeypatch.setattr(update.os, "execv", execv)
    h = heads(settings, "a" * 40, "b" * 40, StrictHost)
    with pytest.raises(SystemExit):
        run_update(settings, h, lines, ["burger", "containers", "bennu"], force=True)
    assert "burger moved: continuing with the new code: update --only containers,bennu --force" in (
        lines
    )
    assert execs == [["burger", "update", "--only", "containers,bennu", "--force"]]


def test_burger_dry_run_continues_with_dry_run_flag(settings, lines, monkeypatch):
    h = heads(settings, "a" * 40, "a" * 40, StrictHost)
    rows = run_update(settings, h, lines, ["burger"], dry=True)
    assert rows[0][1:] == ("a" * 10, "a" * 10, "dry-run: not pulled")


def test_wait_phenix_keeps_polling_until_the_deadline():
    class Clock:
        now = 0

        def time(self):
            return self.now

        def sleep(self, s):
            self.now += s

    class Slow(StrictHost):
        def run(self, cmd, check=True, capture=False, **kw):
            res = cp(rc=1 if len(self.calls) < 6 else 0)
            self.calls.append(cmd)
            return enforce_(res, cmd, check, capture)

    h = Slow()
    update.wait_phenix(h, 100, Clock())  # fails three polls, then answers
    assert len(h.calls) == 8
    with pytest.raises(BurgerError, match="not answering 20s"):
        update.wait_phenix(StrictHost({"docker exec phenix phenix": cp(rc=1)}), 20, Clock())


def enforce_(res, cmd, check, capture):
    from tests.fakes import enforce

    return enforce(res, cmd, check, capture)


@pytest.mark.parametrize(
    "missing",
    [
        '"com.docker.compose.project.working_dir"',
        '"com.docker.compose.project.config_files"',
        '"com.docker.compose.service"',
    ],
)
def test_compose_labels_need_all_three_facts(settings, missing):
    labels = json.loads(LABELS)
    del labels[missing.strip('"')]
    assert update.compose_labels(compose_host(settings, json.dumps(labels)), "phenix") is None


@pytest.mark.parametrize("parent", ["fresh/nested", "."])
def test_recreate_writes_the_override_even_when_its_directory_exists(settings, tmp_path, parent):
    h = compose_host(settings)
    override = tmp_path / parent / "compose.override.yml"
    (tmp_path / parent).mkdir(parents=True, exist_ok=True)
    update.recreate(h, "phenix", override, {"image": "x"})
    assert "image: x" in override.read_text()
    up = next(c for c in h.calls if "up" in c)
    assert up[up.index(str(override)) - 1] == "-f"


def test_recreate_makes_the_missing_override_directory(settings, tmp_path):
    override = tmp_path / "a" / "b" / "compose.override.yml"
    update.recreate(compose_host(settings), "phenix", override, {"image": "x"})
    assert override.is_file()


def test_unmanaged_container_names_its_image_and_is_not_touched(settings, lines):
    ctx = update.Ctx(
        settings,
        strict_for(
            settings,
            {
                INSPECT: cp(rc=1),
                "docker inspect -f {{.Config.Image}} web": cp("nginx:1\n"),
                "docker inspect -f {{.Image}} web": cp("sha256:abcdefabcdefabcdef\n"),
            },
        ),
        lines.append,
    )
    assert update.update_container(ctx, "web") is False
    assert ctx.rows == [
        (
            "web",
            "abcdefabcdef",
            "-",
            "not managed by compose: run `docker pull nginx:1` and "
            "recreate 'web' with its original flags",
        )
    ]


def test_unmanaged_container_without_an_image_says_placeholder(settings, lines):
    probes = {INSPECT: cp(rc=1), "docker inspect -f {{.": cp(rc=1)}
    ctx = update.Ctx(settings, strict_for(settings, probes), lines.append)
    assert update.update_container(ctx, "web") is False
    assert "docker pull <image>" in ctx.rows[0][3]
    assert ctx.rows[0][1] == "?"


CFG_KEY = "docker compose -p stack --project-directory /srv/phenix"


@pytest.mark.parametrize(
    ("cfg", "local"),
    [
        ('{"services":{"phenix":{"build":{"context":"."}}}}', True),
        ('{"services":{"phenix":{"build":{"context":"."},"image":"x"}}}', False),
        ('{"services":{"phenix":{"image":"x"}}}', False),
        ('{"services":{"phenix":{}}}', False),
    ],
)
def test_only_a_service_without_an_image_name_is_built_locally(settings, lines, cfg, local):
    h = strict_for(settings, {INSPECT + "phenix": cp(LABELS), CFG_KEY: cp(cfg)})
    h.responses[CFG_KEY + " -f /srv/phenix/a.yml -f /srv/phenix/b.yml pull"] = cp()
    ctx = update.Ctx(settings, h, lines.append)
    assert update.update_container(ctx, "phenix") is not local
    assert any("pull" in c for c in h.calls) is not local


def test_unreadable_compose_config_is_not_a_local_build(settings, lines):
    h = strict_for(settings, {INSPECT + "phenix": cp(LABELS)})
    h.responses[CFG_KEY + " -f /srv/phenix/a.yml -f /srv/phenix/b.yml config"] = cp(rc=1)
    ctx = update.Ctx(settings, h, lines.append)
    assert update.update_container(ctx, "phenix") is True


def local_build_containers(settings):
    cfg = cp('{"services":{"phenix":{"build":{"context":"."}}}}')
    return strict_for(
        settings,
        {INSPECT + settings.ctr: cp(LABELS), INSPECT + settings.mm_ctr: cp(LABELS), CFG_KEY: cfg},
    )


def test_containers_skip_the_wait_when_nothing_was_touched(settings, lines):
    h = local_build_containers(settings)
    run_update(settings, h, lines, ["containers"])
    assert not any("version" in c for c in h.calls)


def test_dry_containers_do_not_wait_for_phenix(settings, lines):
    h = compose_host(settings)
    h.responses[INSPECT + settings.mm_ctr] = cp("null")
    run_update(settings, h, lines, ["containers"], dry=True)
    assert not any("version" in c for c in h.calls)


def test_containers_report_the_first_line_of_the_phenix_version(settings, lines):
    h = strict_for(
        settings,
        {
            INSPECT + settings.ctr: cp(LABELS),
            INSPECT + settings.mm_ctr: cp("null"),
            f"{CTR}phenix version": cp("v1.2\nbuilt x\n"),
        },
    )
    run_update(settings, h, lines, ["containers"])
    assert "phenix version: v1.2" in lines


def test_containers_say_unknown_when_the_version_cannot_be_read(settings, lines):
    class Flaky(StrictHost):
        def run(self, cmd, check=True, capture=False, **kw):
            seen = sum(c[-2:] == ["phenix", "version"] for c in self.calls)
            res = super().run(cmd, check, capture, **kw)
            if cmd[-2:] == ["phenix", "version"] and seen >= 1:
                return enforce_(cp(rc=1), cmd, check, capture)
            return res

    h = Flaky({INSPECT + settings.ctr: cp(LABELS), INSPECT + settings.mm_ctr: cp("null")}, settings)
    run_update(settings, h, lines, ["containers"])
    assert "phenix version: ?" in lines


@pytest.mark.parametrize("drop", ["bennu\\.deb", r"pybennu-\S+\.whl"])
def test_patch_bennu_sh_needs_both_blocks(drop):
    kept = "".join(ln for ln in BENNU_SH.splitlines(True) if not __import__("re").search(drop, ln))
    with pytest.raises(BurgerError, match="no longer downloads"):
        update.patch_bennu_sh(kept)


def test_point_link_under_the_host_contract(settings, monkeypatch):
    monkeypatch.setattr(update, "_now", lambda: dt.datetime(2026, 10, 22, 9, 8, 7))
    h, rollback = point(
        settings,
        {f"{CTR}test -L ": cp(rc=1), f"{CTR}test -e {IMGS}/bennu-20261022.qc2": cp(rc=1)},
        StrictHost,
    )
    assert rollback == f"{CTR}ln -sfn {IMGS}/bennu-20261022.qc2 {IMGS}/bennu.qc2"
    h, rollback = point(
        settings,
        {f"{CTR}test -L {IMGS}/bennu.qc2": cp(), f"{CTR}readlink": cp("old.qc2\n")},
        StrictHost,
    )
    assert rollback == f"{CTR}ln -sfn old.qc2 {IMGS}/bennu.qc2"
    h, rollback = point(
        settings,
        {f"{CTR}test -L ": cp(rc=1), f"{CTR}test -e {IMGS}/bennu.qc2": cp(rc=1)},
        StrictHost,
    )
    assert rollback == f"{CTR}rm {IMGS}/bennu.qc2"
    assert not any("mv" in c for c in h.calls)


def test_bennu_current_names_the_link_target_a_file_or_none(settings):
    link = f"{IMGS}/bennu.qc2"
    t = strict_for(settings, {f"{CTR}test -L {link}": cp(), f"{CTR}readlink": cp("/x/old.qc2\n")})
    assert update.bennu_current(t, settings) == "old.qc2"
    f = strict_for(settings, {f"{CTR}test -L {link}": cp(rc=1)})
    assert update.bennu_current(f, settings) == "regular file"
    n = strict_for(settings, {f"{CTR}test -L ": cp(rc=1), f"{CTR}test -e ": cp(rc=1)})
    assert update.bennu_current(n, settings) == "none"


def test_installed_apps_reads_the_marker_or_says_unknown(settings):
    ok = strict_for(settings, {f"{CTR}cat": cp('{"apps": "' + "a" * 40 + '"}')})
    assert update.installed_apps(ok) == "a" * 10
    gone = strict_for(settings, {f"{CTR}cat": cp(rc=1)})
    assert update.installed_apps(gone) == "unknown"


def test_checkout_images_clones_or_fetches(settings, lines):
    d = "/imgs"
    fresh = strict_for(settings, {"test -d /imgs/.git": cp(rc=1)})
    update.checkout_images(update.Ctx(settings, fresh, lines.append, dry=True), d)
    assert ["git", "clone", "--quiet", update.IMAGES_REPO, d] in fresh.calls
    there = strict_for(settings)
    update.checkout_images(update.Ctx(settings, there, lines.append, dry=True), d)
    assert ["git", "-C", d, "fetch", "--quiet", "origin", "main"] in there.calls
    assert not any("clone" in c for c in there.calls)


def test_checkout_images_checks_the_container_sees_the_script_unless_dry(settings, lines):
    h = strict_for(settings, {f"{CTR}test -f /imgs/scripts/bennu.sh": cp(rc=1)})
    with pytest.raises(BurgerError, match=r"cannot see /imgs/scripts/bennu\.sh"):
        update.checkout_images(update.Ctx(settings, h, lines.append), "/imgs")
    update.checkout_images(update.Ctx(settings, h, lines.append, dry=True), "/imgs")
    seen = strict_for(settings)
    update.checkout_images(update.Ctx(settings, seen, lines.append), "/imgs")


def run_bennu(settings, lines, images, monkeypatch, dry=False, site=None, **extra):
    fake_build(monkeypatch, settings)
    h = StrictHost(bennu_responses(images, **extra), settings, site)
    return h, run_update(settings, h, lines, ["bennu"], dry=dry)


def test_bennu_build_under_the_host_contract(settings, lines, images, monkeypatch):
    h, rows = run_bennu(settings, lines, images, monkeypatch)
    assert rows == [
        ("bennu", "none", "bennu-main-1234567.qc2", f"built from bennu main {SHA[:10]}")
    ]
    assert f"{CTR}ln -sfn bennu-main-1234567.qc2 {IMGS}/bennu.qc2" in [" ".join(c) for c in h.calls]


def test_bennu_dry_run_under_the_host_contract(settings, lines, images, monkeypatch):
    run_bennu(settings, lines, images, monkeypatch, dry=True)
    assert any("phenix image create" in line for line in lines)


def test_bennu_missing_images_dir_under_the_host_contract(settings, lines, images, monkeypatch):
    with pytest.raises(BurgerError, match="not a directory in the phēnix container"):
        run_bennu(settings, lines, images, monkeypatch, **{f"{CTR}test -d {images}": cp(rc=1)})
    run_bennu(
        settings, lines, images, monkeypatch, dry=True, **{f"{CTR}test -d {images}": cp(rc=1)}
    )
    assert any(line.startswith("! a real run") for line in lines)


def test_bennu_existing_image_is_pointed_to_not_rebuilt(settings, lines, images, monkeypatch):
    monkeypatch.setattr(update.vcs, "sync", lambda *a: pytest.fail("sync"))
    h = StrictHost(bennu_responses(images, **{f"{CTR}test -e {IMGS}/bennu-main-": cp()}), settings)
    rows = run_update(settings, h, lines, ["bennu"])
    assert rows[0][3] == "image already built"


def test_bennu_refuses_to_rebuild_over_a_target_that_appeared_during_the_build(
    settings, lines, images, monkeypatch
):
    fake_build(monkeypatch, settings)
    probe = f"{CTR}test -e {IMGS}/bennu-main-1234567.qc2"

    class Appears(StrictHost):
        def run(self, cmd, check=True, capture=False, **kw):
            if " ".join(cmd) == probe and self.calls.count(cmd):
                return enforce_(cp(), cmd, check, capture)
            return super().run(cmd, check, capture, **kw)

    h = Appears(bennu_responses(images), settings)
    with pytest.raises(BurgerError, match="exists; remove it to rebuild"):
        run_update(settings, h, lines, ["bennu"])


def test_bennu_drop_config_only_for_an_image_named_bennu(settings, lines, images, monkeypatch):
    short = "Image a b c d\nImage a b c d other\nImage a b c d bennu\n"
    h, _ = run_bennu(settings, lines, images, monkeypatch, **{f"{CTR}phenix cfg list": cp(short)})
    assert sum(c[-3:] == ["image", "delete", "bennu"] for c in h.calls) == 1
    short = "Image a b c d\nImage a b c d other\nTopology x\n"
    h, _ = run_bennu(settings, lines, images, monkeypatch, **{f"{CTR}phenix cfg list": cp(short)})
    assert not any("delete" in c for c in h.calls)
    h, _ = run_bennu(settings, lines, images, monkeypatch, **{f"{CTR}phenix cfg list": cp(rc=1)})
    assert not any("delete" in c for c in h.calls)


def test_bennu_injects_miniccc_from_the_checkout_or_says_it_is_missing(
    settings, lines, images, monkeypatch
):
    d = str(images)
    h, _ = run_bennu(
        settings, lines, images, monkeypatch, **{f"{CTR}test -f /phenix/miniccc": cp(rc=1)}
    )
    assert f"{CTR}phenix image inject-miniexe {d}/miniccc {d}/bennu.qc2" in [
        " ".join(c) for c in h.calls
    ]
    gone = {f"{CTR}test -f /phenix/miniccc": cp(rc=1), f"{CTR}test -f {d}/miniccc": cp(rc=1)}
    h, _ = run_bennu(settings, lines, images, monkeypatch, **gone)
    assert not any("inject-miniexe" in c for c in h.calls)
    assert any(line.startswith("no miniccc in the phenix directory") for line in lines)


def test_bennu_passes_the_apt_mirror_to_image_create(settings, lines, images, monkeypatch):
    from types import SimpleNamespace

    site = SimpleNamespace(mirrors={"apt": "http://mirror/apt"})
    h, _ = run_bennu(settings, lines, images, monkeypatch, site=site)
    assert any("--mirror=http://mirror/apt" in c for c in h.calls[0:] for c in c)
    site = SimpleNamespace(mirrors={})
    h, _ = run_bennu(settings, lines, images, monkeypatch, site=site)
    assert not any(a.startswith("--mirror") for c in h.calls for a in c)


def test_wait_phenix_polls_again_at_the_deadline_itself():
    class Clock:
        now = 0

        def time(self):
            return self.now

        def sleep(self, s):
            self.now += s

    class Late(StrictHost):
        def run(self, cmd, check=True, capture=False, **kw):
            res = cp(rc=1 if len(self.calls) < 5 else 0)  # polls at 0,5,10,15,20 fail
            self.calls.append(cmd)
            return enforce_(res, cmd, check, capture)

    update.wait_phenix(Late(), 20, Clock())


def test_dry_put_says_where_it_would_copy(settings):
    said = []
    update.dry_host(StrictHost({}, settings), said.append).put("a", "/b")
    assert said == ["+ put a -> /b (in phenix)"]
    import dataclasses

    bare = dataclasses.replace(settings, ctr="")
    update.dry_host(StrictHost({}, bare), said.append).put("a", "/b")
    assert said[1] == "+ put a -> /b (in this host)"


def test_main_board_is_not_linux_only(settings):
    assert update.main_board(settings).board.linux_only is False
