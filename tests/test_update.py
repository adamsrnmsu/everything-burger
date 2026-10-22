import subprocess

import pytest

from burger import update
from burger.cli import main
from burger.config import BurgerError
from tests.fakes import FakeHost


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


def heads(settings, before, after):
    """Responses where HEAD is `before` until the pull, `after` from then on."""

    class Moving(FakeHost):
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
    monkeypatch.setattr(update.os, "execv", lambda path, argv: execs.append((path, argv)))
    h = heads(settings, "a" * 40, "b" * 40)
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
    with pytest.raises(BurgerError, match="cannot list"):
        run_update(settings, h, lines, ["containers"])
    run_update(settings, h, lines, ["containers"], force=True)
    assert any(c[:2] == ["docker", "compose"] and "up" in c for c in h.calls)


def test_api_error_status_is_not_read_as_no_experiments(settings, lines):
    h = compose_host(settings)
    h.api_responses["/experiments"] = None
    with pytest.raises(BurgerError, match="answered the experiments request with an error"):
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
