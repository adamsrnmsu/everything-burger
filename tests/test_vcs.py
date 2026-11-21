import dataclasses
import json
import shutil
import subprocess

import pytest

from burger import config, vcs
from burger.config import BurgerError
from burger.shell import Host
from burger.switchboard import load_switchboard, resolve
from burger.vcs import _branch_ref, _fetch_branch, bennu_label, branch_url, fork_owner, prs, sync
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
    board = load_switchboard("fdleak", settings)
    assert board.bennu
    n = board.bennu["pr"]
    (settings.work / "fdleak").mkdir()
    man = {"apps": {"prs": {}}, "bennu": {"pr": n, "sha": "old"}}
    (settings.work / "fdleak" / "manifest.json").write_text(json.dumps(man))
    monkeypatch.setattr("shutil.which", lambda _: None)  # no gh: carried PRs only
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "newsha\trefs/x\n", "")})
    row = next(r for r in prs(settings, host, "me") if r["number"] == n and "bennu" in r["repo"])
    assert "fdleak" in row["boards"] and row["stale"] == ["fdleak"]
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


def test_sync_bennu_prs_merges_them_in_order(
    settings, apps_origin, topologies, monkeypatch, tmp_path
):
    make_origin(apps_origin, {})
    bennu_origin = tmp_path / "bennu-origin"
    make_origin(bennu_origin, {})
    for n, name in ((1, "one"), (2, "two")):  # two PRs on different files: both merge
        git(bennu_origin, "checkout", "-q", "-b", f"x{n}", "main")
        (bennu_origin / f"{name}.txt").write_text(f"{name}\n")
        git(bennu_origin, "add", ".")
        git(bennu_origin, "commit", "-qm", name)
        git(bennu_origin, "update-ref", f"refs/pull/{n}/head", "HEAD")
    git(bennu_origin, "checkout", "-q", "main")
    monkeypatch.delenv("BURGER_BENNU_REPO", raising=False)
    monkeypatch.setitem(config.REPOS, "bennu", str(bennu_origin))
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    b = load_switchboard("baseline", settings)
    spec = {"prs": [1, 2], "install": ["deb"]}
    man = sync(resolve(dataclasses.replace(b, bennu=spec), settings), settings, Host(settings))
    wt = settings.work / "baseline" / "bennu"
    assert (wt / "one.txt").exists() and (wt / "two.txt").exists()
    assert man["bennu"]["prs"] == {
        "1": git(bennu_origin, "rev-parse", "refs/pull/1/head"),
        "2": git(bennu_origin, "rev-parse", "refs/pull/2/head"),
    }
    assert man["bennu"]["sha"] == git(wt, "rev-parse", "HEAD") != man["bennu"]["prs"]["2"]
    assert bennu_label(man["bennu"]) == "#1+#2"


def test_prs_lists_every_bennu_pr_of_a_board_and_its_stale_head(settings, monkeypatch):
    b = load_switchboard("baseline", settings)
    b = dataclasses.replace(b, bennu={"prs": [84, 85], "install": ["deb"]})
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: ["gt"])
    monkeypatch.setattr("burger.vcs.load_switchboard", lambda n, s: b)
    (settings.work / "gt").mkdir()
    man = {"apps": {"prs": {}}, "bennu": {"prs": {"84": "old", "85": "new"}, "sha": "m"}}
    (settings.work / "gt" / "manifest.json").write_text(json.dumps(man))
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "new\trefs/x\n", "")})
    rows = {r["number"]: r for r in prs(settings, host, "me") if "bennu" in r["repo"]}
    assert rows[84]["boards"] == ["gt"] and rows[84]["stale"] == ["gt"]
    assert rows[85]["boards"] == ["gt"] and rows[85]["stale"] == []


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


def test_fork_owner_env_skips_gh(monkeypatch):
    monkeypatch.setenv("BURGER_FORK_OWNER", "someone")
    host = FakeHost()
    assert fork_owner(host) == "someone"
    assert branch_url(host, "apps", "fork") == "https://github.com/someone/sceptre-phenix-apps.git"
    assert host.calls == []  # gh never ran


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


def minimega_board(settings, spec, **kw):
    b = load_switchboard("baseline", settings)
    return resolve(dataclasses.replace(b, minimega=spec, **kw), settings)


def test_sync_minimega_pr(settings, apps_origin, topologies, monkeypatch, tmp_path):
    make_origin(apps_origin, {})
    mm_origin = tmp_path / "mm-origin"
    make_origin(mm_origin, {1782: "pr\n"})
    git(mm_origin, "branch", "-m", "main", "master")  # minimega has no main branch
    monkeypatch.delenv("BURGER_MINIMEGA_REPO", raising=False)
    monkeypatch.setitem(config.REPOS, "minimega", str(mm_origin))
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    man = sync(minimega_board(settings, {"pr": 1782}), settings, Host(settings))
    sha = git(mm_origin, "rev-parse", "refs/pull/1782/head")
    assert man["minimega"] == {"pr": 1782, "sha": sha, "from": "board"}
    assert (settings.work / "baseline" / "minimega" / "a.txt").read_text() == "pr\n"
    assert json.loads((settings.work / "baseline" / "manifest.json").read_text()) == man


def test_sync_minimega_branch_fork(settings, monkeypatch):
    monkeypatch.setenv("BURGER_FORK_OWNER", "me")
    monkeypatch.delenv("BURGER_MINIMEGA_REPO", raising=False)
    host = FakeHost({"git -C": subprocess.CompletedProcess([], 0, "f" * 40 + "\n", "")}, settings)
    host.run = lambda cmd, **kw: FakeHost.run(host, [c for c in cmd if c != "--quiet"], **kw)
    res = minimega_board(settings, {"branch": "foo", "remote": "fork"}, minimega_from="flag")
    man = sync(res, settings, host)
    repo, wt = settings.work / "repos" / "minimega", settings.work / "baseline" / "minimega"
    ref = "refs/burger/branch/fork/foo"
    assert [
        "git", "-C", str(repo), "fetch", "https://github.com/me/minimega.git",
        f"+refs/heads/foo:{ref}",
    ] in host.calls  # fmt: skip
    assert ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), ref] in host.calls
    assert man["minimega"] == {"branch": "foo", "remote": "fork", "sha": "f" * 40, "from": "flag"}


def test_sync_minimega_pr_fetches_only_the_pr_head(settings, monkeypatch):
    monkeypatch.delenv("BURGER_MINIMEGA_REPO", raising=False)
    host = FakeHost(settings=settings)
    sync(minimega_board(settings, {"pr": 1782}), settings, host)
    fetch = next(c for c in host.calls if "fetch" in c and config.REPOS["minimega"] in c)
    assert fetch[-1] == "+refs/pull/1782/head:refs/burger/pr/1782"
    assert not any("refs/heads/main" in a for a in fetch)


def test_sync_without_minimega_records_none(settings):
    host = FakeHost(settings=settings)
    man = sync(minimega_board(settings, None), settings, host)
    assert man["minimega"] is None
    assert not any("repos/minimega" in " ".join(c) for c in host.calls)


def test_prs_lists_minimega_prs_the_boards_carry(settings, monkeypatch):
    b = dataclasses.replace(load_switchboard("baseline", settings), minimega={"pr": 1782})
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: ["mm"])
    monkeypatch.setattr("burger.vcs.load_switchboard", lambda n, s: b)
    (settings.work / "mm").mkdir()
    man = {"apps": {"prs": {}}, "minimega": {"pr": 1782, "sha": "old", "from": "board"}}
    (settings.work / "mm" / "manifest.json").write_text(json.dumps(man))
    host = FakeHost({"git ls-remote": subprocess.CompletedProcess([], 0, "new\trefs/x\n", "")})
    (row,) = [r for r in prs(settings, host, "me") if r["repo"] == "sandia-minimega/minimega"]
    assert (row["number"], row["boards"], row["stale"]) == (1782, ["mm"], ["mm"])


def _gh_host(open_by_slug, rc=0):
    def reply(cmd, **kw):
        slug = cmd[cmd.index("-R") + 1]
        return json.dumps(open_by_slug.get(slug, []))

    class Gh(FakeHost):
        def run(self, cmd, check=True, capture=False, **kw):
            res = super().run(cmd, check, capture, **kw)
            if cmd[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(cmd, rc, reply([str(c) for c in cmd]), "")
            return res

    return Gh()


def test_prs_lists_the_authors_open_prs_without_any_board(settings, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: [])
    long = "t" * 70
    host = _gh_host(
        {
            "sandialabs/sceptre-phenix-apps": [
                {"number": 9, "title": long, "headRefOid": "h9", "isDraft": True},
                {"number": 3, "title": "short", "headRefOid": "h3", "isDraft": False},
            ],
            "sandialabs/sceptre-bennu": [
                {"number": 5, "title": "b", "headRefOid": "h5", "isDraft": True}
            ],
        }
    )
    rows = prs(settings, host, "me")
    assert [(r["repo"], r["number"]) for r in rows] == [
        ("sandialabs/sceptre-phenix-apps", 3),
        ("sandialabs/sceptre-phenix-apps", 9),
        ("sandialabs/sceptre-bennu", 5),
    ]  # per repo in SLUGS order, PRs ascending
    by_n = {r["number"]: r for r in rows}
    assert by_n[3] == {
        "repo": "sandialabs/sceptre-phenix-apps",
        "number": 3,
        "head": "h3",
        "boards": [],
        "title": "short",
        "stale": [],
    }
    assert by_n[9]["title"] == "t" * 60 + " [draft]" and by_n[9]["head"] == "h9"
    assert by_n[5]["title"] == "b [draft]"
    cmd = next(c for c in host.calls if c[:3] == ["gh", "pr", "list"])
    assert cmd[cmd.index("--author") + 1] == "me" and "open" in cmd


def test_prs_survives_gh_failing(settings, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: [])
    assert prs(settings, _gh_host({"sandialabs/sceptre-bennu": [{"number": 1}]}, rc=1), "me") == []


def test_prs_a_carried_pr_that_is_open_takes_its_title_and_flags_a_moved_head(
    settings, monkeypatch
):
    b = dataclasses.replace(load_switchboard("baseline", settings), apps_prs=[7])
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr("burger.vcs.all_switchboards", lambda s: ["x", "y"])
    monkeypatch.setattr("burger.vcs.load_switchboard", lambda n, s: b)
    (settings.work / "x").mkdir()
    (settings.work / "x" / "manifest.json").write_text(json.dumps({"apps": {"prs": {"7": "old"}}}))
    host = _gh_host(
        {
            "sandialabs/sceptre-phenix-apps": [
                {"number": 7, "title": "seven", "headRefOid": "new", "isDraft": False}
            ]
        }
    )
    (row,) = prs(settings, host, "me")
    assert row["boards"] == ["x", "y"] and row["title"] == "seven"
    assert row["stale"] == ["x"]  # y was never synced: nothing to be behind


def test_ensure_repo_makes_missing_parents_and_clones_once_per_call(
    settings, monkeypatch, tmp_path
):
    monkeypatch.delenv("BURGER_APPS_REPO", raising=False)
    deep = dataclasses.replace(settings, work=tmp_path / "a" / "b")
    host = FakeHost(settings=deep)
    path = vcs._ensure_repo(host, deep, "apps")
    assert path == deep.work / "repos" / "apps" and path.parent.is_dir()
    vcs._ensure_repo(host, deep, "apps")  # the parent already exists: still fine
    assert [c[:3] for c in host.calls] == [["git", "clone", "--quiet"]] * 2
    (path / ".git").mkdir(parents=True)
    vcs._ensure_repo(host, deep, "apps")
    assert len(host.calls) == 2  # a clone is not repeated


def test_ensure_repo_honours_the_env_override(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("BURGER_APPS_REPO", str(tmp_path / "mine"))
    (tmp_path / "mine" / ".git").mkdir(parents=True)
    host = FakeHost(settings=settings)
    assert vcs._ensure_repo(host, settings, "apps") == tmp_path / "mine"
    assert host.calls == []


@pytest.mark.parametrize("remove_deletes", [True, False])
def test_fresh_worktree_replaces_a_stale_one_whatever_git_says(tmp_path, remove_deletes):
    """`worktree remove` may fail (not a worktree) or already delete the dir: neither stops
    the new worktree."""
    stale = tmp_path / "wt"
    (stale / "sub").mkdir(parents=True)

    class Host2(FakeHost):
        def run(self, cmd, check=True, capture=False, **kw):
            super().run(cmd)
            if "remove" in cmd:
                if check:
                    raise BurgerError("remove must not be checked")
                if remove_deletes:
                    shutil.rmtree(stale)
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

    host = Host2()
    vcs._fresh_worktree(host, tmp_path, stale, "refs/x")
    assert not stale.exists()
    verbs = [c[3:5] for c in host.calls]
    assert verbs == [["worktree", "remove"], ["worktree", "prune"], ["worktree", "add"]]
    assert host.calls[-1][-3:] == ["--detach", str(stale), "refs/x"]


def test_fresh_worktree_without_a_stale_one_just_adds(tmp_path):
    host = FakeHost()
    vcs._fresh_worktree(host, tmp_path, tmp_path / "wt", "refs/x")
    assert [c[3:5] for c in host.calls] == [["worktree", "prune"], ["worktree", "add"]]


def test_ls_remote_reads_the_head_and_tolerates_a_failing_remote(tmp_path, settings):
    origin = tmp_path / "o"
    make_origin(origin, {4: "x\n"})
    host = Host(settings)
    assert vcs._ls_remote(host, str(origin), "refs/pull/4/head") == git(
        origin, "rev-parse", "refs/pull/4/head"
    )
    assert vcs._ls_remote(host, str(origin), "refs/pull/5/head") is None
    assert vcs._ls_remote(host, str(tmp_path / "nowhere"), "refs/heads/main") is None


def test_merge_conflict_message_carries_gits_own_output(settings, apps_origin):
    make_origin(apps_origin, {1: "one\n", 2: "two\n"})
    with pytest.raises(BurgerError) as e:
        sync(board_with_prs(settings, [1, 2]), settings, Host(settings))
    assert "CONFLICT" in str(e.value) and "None" not in str(e.value)
    wt = settings.work / "baseline" / "apps"
    assert git(wt, "diff", "--name-only", "--diff-filter=U") == ""  # the merge was aborted


def test_fork_owner_reads_the_login_gh_prints(monkeypatch, tmp_path, settings):
    monkeypatch.delenv("BURGER_FORK_OWNER", raising=False)
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text("#!/bin/sh\necho bob\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{gh.parent}:/usr/bin:/bin")
    assert fork_owner(Host(settings)) == "bob"


def test_sync_makes_the_work_dir_when_it_is_missing(
    settings, apps_origin, topologies, monkeypatch, tmp_path
):
    make_origin(apps_origin, {})
    monkeypatch.setitem(config.REPOS, "topologies", str(topologies))
    monkeypatch.setenv("BURGER_TOPOLOGIES_REPO", str(topologies))
    fresh = dataclasses.replace(settings, work=tmp_path / "new" / "work")
    sync(board_with_prs(fresh, []), fresh, Host(fresh))
    assert (fresh.work / "baseline" / "manifest.json").is_file()
