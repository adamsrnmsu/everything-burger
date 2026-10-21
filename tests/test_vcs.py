import json
import subprocess

import pytest

from burger import config
from burger.config import BurgerError
from burger.shell import Host
from burger.switchboard import load_switchboard, resolve
from burger.vcs import prs, sync
from tests.fakes import FakeHost


def git(repo, *a):
    return subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *a],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_origin(path, prs_by_number):
    """A repo with main and refs/pull/<n>/head, each PR writing its text to a.txt."""
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("base\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    for n, text in prs_by_number.items():
        git(path, "checkout", "-q", "-b", f"pr{n}", "main")
        (path / "a.txt").write_text(text)
        git(path, "commit", "-qam", f"pr {n}")
        git(path, "update-ref", f"refs/pull/{n}/head", "HEAD")
    git(path, "checkout", "-q", "main")


@pytest.fixture
def apps_origin(tmp_path, monkeypatch):
    monkeypatch.delenv("BURGER_APPS_REPO", raising=False)
    monkeypatch.setitem(config.REPOS, "apps", str(tmp_path / "origin"))
    return tmp_path / "origin"


def board_with_prs(settings, nums):
    board = load_switchboard("baseline", settings)
    board.apps_prs = nums
    return resolve(board, settings)


def test_sync_conflict_names_files(settings, apps_origin):
    make_origin(apps_origin, {1: "one\n", 2: "two\n"})
    with pytest.raises(BurgerError, match=r"#2 conflicts.*a\.txt"):
        sync(board_with_prs(settings, [1, 2]), settings, Host(settings))


def test_sync_merges_prs_and_writes_manifest(settings, apps_origin, topologies, monkeypatch):
    make_origin(apps_origin, {1: "one\n"})
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    man = sync(board_with_prs(settings, [1]), settings, Host(settings))
    assert man["apps"]["prs"]["1"] == git(apps_origin, "rev-parse", "refs/pull/1/head")
    assert (settings.work / "baseline" / "apps" / "a.txt").read_text() == "one\n"
    assert json.loads((settings.work / "baseline" / "manifest.json").read_text()) == man


def test_sync_carrying_a_merged_pr_is_a_no_op(settings, apps_origin, topologies, monkeypatch):
    # #190 merged upstream while boards still listed it: its head is already in main
    make_origin(apps_origin, {1: "one\n"})
    git(apps_origin, "merge", "-q", "--no-ff", "-m", "merge pr 1", "pr1")
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    man = sync(board_with_prs(settings, [1]), settings, Host(settings))
    assert man["apps"]["head"] == man["apps"]["base"]  # nothing left to merge
    assert (settings.work / "baseline" / "apps" / "a.txt").read_text() == "one\n"


def test_prs_flags_stale_boards(settings, monkeypatch):
    board = load_switchboard("pybennu", settings)
    assert board.bennu
    n = board.bennu["pr"]
    (settings.work / "pybennu").mkdir()
    man = {"apps": {"prs": {}}, "bennu": {"pr": n, "sha": "old"}}
    (settings.work / "pybennu" / "manifest.json").write_text(json.dumps(man))
    monkeypatch.setattr("shutil.which", lambda _: None)  # no gh: carried PRs only
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "newsha\trefs/x\n", "")})
    row = next(r for r in prs(settings, host, "me") if r["number"] == n and "bennu" in r["repo"])
    assert "pybennu" in row["boards"] and row["stale"] == ["pybennu"]
    assert row["head"] == "newsha" and row["title"] == "(not open)"
