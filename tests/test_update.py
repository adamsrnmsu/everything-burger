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
    assert rows == [("burger", "a" * 10, "a" * 10, "already current")]


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
