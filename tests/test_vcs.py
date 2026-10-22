import dataclasses
import json
import subprocess

import pytest

from burger import config
from burger.config import BurgerError
from burger.shell import Host
from burger.switchboard import load_switchboard, resolve
from burger.vcs import _branch_ref, _fetch_branch, fork_owner, prs, sync
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


def test_sync_merge_failure_without_conflict_shows_git_output(settings, apps_origin, monkeypatch):
    # burger-15k: a merge that dies before it starts (no conflict files, no MERGE_HEAD)
    # used to report only "conflicts ... (see git output)" with the output thrown away
    make_origin(apps_origin, {1: "one\n"})

    class Boom(Host):
        def run(self, cmd, **kw):
            if "merge" in cmd and "--no-ff" in cmd:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: stash failed\n")
            return super().run(cmd, **kw)

    with pytest.raises(
        BurgerError, match=r"(?s)#1 conflicts.*in \(none; see git output\).*stash failed"
    ):
        sync(board_with_prs(settings, [1]), settings, Boom(settings))


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


def branch_board(settings, name="baseline", **kw):
    b = load_switchboard(name, settings)
    b.apps_branch = {"branch": "integration", "remote": "upstream"}
    b.apps_prs = [1]
    for k, v in kw.items():
        setattr(b, k, v)
    return resolve(b, settings)


def test_sync_branch_checks_out_the_branch_without_merging(
    settings, apps_origin, topologies, monkeypatch
):
    make_origin(apps_origin, {})
    git(apps_origin, "checkout", "-q", "-b", "integration")
    (apps_origin / "a.txt").write_text("integrated\n")
    git(apps_origin, "commit", "-qam", "integration")
    git(apps_origin, "checkout", "-q", "main")
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    man = sync(branch_board(settings), settings, Host(settings))
    sha = git(apps_origin, "rev-parse", "integration")
    assert man["apps"] == {
        "branch": "integration",
        "remote": "upstream",
        "sha": sha,
        "head": sha,
        "prs": {},
    }
    assert (settings.work / "baseline" / "apps" / "a.txt").read_text() == "integrated\n"


def test_sync_missing_branch_is_a_burger_error(settings, apps_origin):
    make_origin(apps_origin, {})
    with pytest.raises(BurgerError, match="integration"):
        sync(branch_board(settings), settings, Host(settings))


def test_fork_branch_fetches_from_the_gh_users_fork(settings, tmp_path):
    host = FakeHost({"gh api user": subprocess.CompletedProcess([], 0, "adamsrnmsu\n", "")})
    spec = {"branch": "integration", "remote": "fork"}
    ref = _fetch_branch(host, tmp_path, "apps", spec)
    _fetch_branch(host, tmp_path, "bennu", spec)
    assert ref == "refs/burger/branch/fork/integration"
    fetches = [c for c in host.calls if "fetch" in c]
    assert fetches[0][-2:] == [
        "https://github.com/adamsrnmsu/sceptre-phenix-apps.git",
        "+refs/heads/integration:refs/burger/branch/fork/integration",
    ]
    assert fetches[1][-2].endswith("/adamsrnmsu/sceptre-bennu.git")
    assert sum(c[:3] == ["gh", "api", "user"] for c in host.calls) == 1  # cached per host


def test_fork_owner_unavailable_is_a_burger_error():
    host = FakeHost({"gh api user": subprocess.CompletedProcess([], 0, "\n", "")})
    with pytest.raises(BurgerError, match="no login"):
        fork_owner(host)


def test_sync_bennu_branch_records_branch_remote_and_sha(
    settings, apps_origin, topologies, monkeypatch, tmp_path
):
    make_origin(apps_origin, {})
    bennu_origin = tmp_path / "bennu-origin"
    make_origin(bennu_origin, {})
    monkeypatch.delenv("BURGER_BENNU_REPO", raising=False)
    monkeypatch.setitem(config.REPOS, "bennu", str(bennu_origin))
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    spec = {"branch": "main", "remote": "upstream", "install": ["deb"]}
    b = load_switchboard("baseline", settings)
    man = sync(resolve(dataclasses.replace(b, bennu=spec), settings), settings, Host(settings))
    sha = git(bennu_origin, "rev-parse", "main")
    assert man["bennu"] == {"branch": "main", "remote": "upstream", "sha": sha}
    assert (settings.work / "baseline" / "bennu" / "a.txt").exists()


def test_prs_lists_branch_board_contains_as_carried(settings, monkeypatch):
    b = load_switchboard("baseline", settings)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: ["integration"])
    b = dataclasses.replace(b, apps_prs=[159], apps_branch={"branch": "i", "remote": "fork"})
    monkeypatch.setattr("burger.vcs.load_switchboard", lambda n, s: b)
    (settings.work / "integration").mkdir()
    man = {"apps": {"branch": "i", "remote": "fork", "sha": "x", "head": "x", "prs": {}}}
    (settings.work / "integration" / "manifest.json").write_text(json.dumps(man))
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "head1\trefs/x\n", "")})
    row = next(r for r in prs(settings, host, "me") if r["number"] == 159)
    assert row["boards"] == ["integration"] and row["stale"] == []


def test_fork_url_ignores_a_local_upstream_override(tmp_path, monkeypatch):
    monkeypatch.setitem(config.REPOS, "bennu", str(tmp_path / "bennu-origin"))
    host = FakeHost({"gh api user": subprocess.CompletedProcess([], 0, "me\n", "")})
    _fetch_branch(host, tmp_path, "bennu", {"branch": "b", "remote": "fork"})
    assert host.calls[-1][-2] == "https://github.com/me/sceptre-bennu.git"


def test_gh_failing_is_a_burger_error_with_a_hint():
    class NoGh(FakeHost):
        def run(self, cmd, **kw):
            raise BurgerError("gh not found on PATH")

    with pytest.raises(BurgerError, match="gh auth login"):
        fork_owner(NoGh())


def test_fork_owner_is_asked_once_per_host_and_not_shared():
    ans = subprocess.CompletedProcess([], 0, "me\n", "")
    a, b = FakeHost({"gh api user": ans}), FakeHost({"gh api user": ans})
    assert fork_owner(a) == fork_owner(a) == fork_owner(b) == "me"
    assert [len(h.calls) for h in (a, b)] == [1, 1]


def test_sync_fork_branch_end_to_end(settings, apps_origin, topologies, monkeypatch):
    make_origin(apps_origin, {})
    git(apps_origin, "checkout", "-q", "-b", "integration")
    (apps_origin / "a.txt").write_text("forked\n")
    git(apps_origin, "commit", "-qam", "fork work")
    git(apps_origin, "checkout", "-q", "main")
    fork = "https://github.com/me/sceptre-phenix-apps.git"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{apps_origin}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", fork)
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    host = Host(settings)
    host._fork_owner = "me"
    board = branch_board(settings, apps_branch={"branch": "integration", "remote": "fork"})
    man = sync(board, settings, host)
    assert man["apps"]["remote"] == "fork"
    assert man["apps"]["sha"] == git(apps_origin, "rev-parse", "integration")
    assert (settings.work / "baseline" / "apps" / "a.txt").read_text() == "forked\n"


def test_fork_fetch_failure_is_a_burger_error_with_a_hint(settings, tmp_path):
    class Fails(FakeHost):
        def git(self, *a, **kw):
            raise BurgerError("command failed (128): git fetch")

    host = Fails({"gh api user": subprocess.CompletedProcess([], 0, "me\n", "")})
    with pytest.raises(BurgerError, match="pushed to the fork"):
        _fetch_branch(host, tmp_path, "apps", {"branch": "nope", "remote": "fork"})


def test_branches_a_and_a_slash_b_get_distinct_refs():
    refs = [_branch_ref({"remote": "fork", "branch": b}) for b in ("a", "a/b")]
    assert refs[0] != refs[1] and "/" not in refs[1].removeprefix("refs/burger/branch/fork/")


def test_prs_flags_a_branch_board_whose_branch_moved(settings, monkeypatch):
    b = dataclasses.replace(
        load_switchboard("baseline", settings),
        apps_prs=[159],
        apps_branch={"branch": "i", "remote": "upstream"},
    )
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: ["integration"])
    monkeypatch.setattr("burger.vcs.load_switchboard", lambda n, s: b)
    (settings.work / "integration").mkdir()
    man = {"apps": {"branch": "i", "remote": "upstream", "sha": "old", "head": "old", "prs": {}}}
    (settings.work / "integration" / "manifest.json").write_text(json.dumps(man))
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "new\trefs/x\n", "")})
    row = next(r for r in prs(settings, host, "me") if r["number"] == 159)
    assert row["stale"] == ["integration"]
