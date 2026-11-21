import datetime as dt
import json
import os
import shlex
import shutil
from pathlib import Path
from urllib.parse import quote

from loguru import logger

from burger.config import GIT_ID, REPOS, BurgerError, Settings
from burger.switchboard import Resolved, all_switchboards, load_switchboard, pinned_label

SLUGS = {
    "apps": "sandialabs/sceptre-phenix-apps",
    "bennu": "sandialabs/sceptre-bennu",
    "minimega": "sandia-minimega/minimega",
    "phenix": "sandialabs/sceptre-phenix",
}


def board_dir(settings: Settings, name: str) -> Path:
    return settings.work / name


def load_manifest(settings: Settings, name: str) -> dict:
    path = board_dir(settings, name) / "manifest.json"
    if not path.is_file():
        raise BurgerError(f"switchboard '{name}' not synced; run burger sync {name}")
    return json.loads(path.read_text())


def _ensure_repo(host, settings: Settings, key: str) -> Path:
    path = Path(os.environ.get(f"BURGER_{key.upper()}_REPO", settings.work / "repos" / key))
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        host.run(["git", "clone", "--quiet", "--no-checkout", REPOS[key], path])
    return path


def _fetch(host, repo: Path, url: str, refs) -> None:
    """Fetch main and PR heads into refs/burger/* so no local branch moves."""
    specs = ["+refs/heads/main:refs/burger/main"]
    specs += [f"+refs/pull/{n}/head:refs/burger/pr/{n}" for n in refs]
    host.git(repo, "fetch", "--quiet", url, *specs)


def fork_owner(host) -> str:
    """The fork's owner: BURGER_FORK_OWNER if set (a public fork needs no gh), else the
    gh user, the identity `burger prs` means by @me. Asked once per host object."""
    if env := os.environ.get("BURGER_FORK_OWNER"):
        return env
    if not hasattr(host, "_fork_owner"):
        try:
            owner = host.run(["gh", "api", "user", "--jq", ".login"], capture=True).stdout.strip()
        except BurgerError as e:
            raise BurgerError(
                f"cannot read the gh user for remote: fork ({e}); run gh auth login"
            ) from None
        if not owner:
            raise BurgerError("gh api user returned no login; run gh auth login")
        host._fork_owner = owner
    return host._fork_owner


def branch_url(host, key: str, remote: str) -> str:
    """Upstream is REPOS[key]; the fork is the gh user's repo named like the slug's."""
    if remote == "upstream":
        return REPOS[key]
    return f"https://github.com/{fork_owner(host)}/{SLUGS[key].split('/')[1]}.git"


def _branch_ref(spec: dict) -> str:
    # quoted so branches `a` and `a/b` never need to be a ref file and a ref directory at once
    return f"refs/burger/branch/{spec['remote']}/{quote(spec['branch'], safe='')}"


def _fetch_branch(host, repo: Path, key: str, spec: dict) -> str:
    """Fetch spec's branch from its remote into refs/burger/branch/...; return that ref."""
    url = branch_url(host, key, spec["remote"])
    ref = _branch_ref(spec)
    try:
        host.git(repo, "fetch", "--quiet", url, f"+refs/heads/{spec['branch']}:{ref}")
    except BurgerError as e:
        hint = " (is it pushed to the fork?)" if spec["remote"] == "fork" else ""
        raise BurgerError(f"cannot fetch branch '{spec['branch']}' of {url}{hint}: {e}") from None
    return ref


def _rev(host, repo: Path, ref: str) -> str:
    return host.git(repo, "rev-parse", ref, capture=True).stdout.strip()


def _fresh_worktree(host, repo: Path, path: Path, ref: str) -> None:
    if path.exists():
        host.git(repo, "worktree", "remove", "--force", path, check=False)
        shutil.rmtree(path, ignore_errors=True)
    host.git(repo, "worktree", "prune")
    host.git(repo, "worktree", "add", "--quiet", "--detach", path, ref)


def _merge_prs(host, wt: Path, attrs: Path, board: str, prs: list[int]) -> None:
    """Merge refs/burger/pr/<n> into the worktree in order; a conflict names the files."""
    for n in prs:
        merged = host.git(
            wt,
            "-c",
            f"core.attributesFile={attrs}",
            *GIT_ID,
            "merge",
            "--no-ff",
            "--no-edit",
            "-m",
            f"burger: merge #{n}",
            f"refs/burger/pr/{n}",
            check=False,
            capture=True,
        )
        if merged.returncode != 0:
            bad = host.git(
                wt, "diff", "--name-only", "--diff-filter=U", capture=True
            ).stdout.split()
            host.git(wt, "merge", "--abort", check=False, capture=True)
            raise BurgerError(
                f"switchboard '{board}': #{n} conflicts with the PRs before it "
                f"in {', '.join(bad) or '(none; see git output)'}; drop or reorder it in "
                f"switchboards/{board}.yaml\n{merged.stdout}{merged.stderr}".rstrip()
            )


def _sync_apps(host, settings: Settings, rd: Path, attrs: Path, board) -> dict:
    """Apps worktree: a branch, or main with the PRs merged over it; its manifest entry."""
    br = board.apps_branch  # a branch board holds its PRs already: nothing to merge
    prs = [] if br else [int(n) for n in board.apps_prs]
    repo = _ensure_repo(host, settings, "apps")
    if br:
        base = _fetch_branch(host, repo, "apps", br)
    else:
        _fetch(host, repo, REPOS["apps"], prs)
        base = "refs/burger/main"
    wt = rd / "apps"
    _fresh_worktree(host, repo, wt, base)
    sha = _rev(host, repo, base)
    apps = {**br, "sha": sha, "prs": {}} if br else {"base": sha, "prs": {}}
    for n in prs:
        apps["prs"][str(n)] = _rev(host, repo, f"refs/burger/pr/{n}")
    _merge_prs(host, wt, attrs, board.name, prs)
    apps["head"] = _rev(host, wt, "HEAD")
    return apps


def _bennu_source(host, repo: Path, spec: dict) -> tuple[str, dict]:
    """The ref to check bennu out at and the manifest's record of where it came from."""
    if "branch" in spec:
        ref = _fetch_branch(host, repo, "bennu", spec)
        return ref, {"branch": spec["branch"], "remote": spec["remote"]}
    if "prs" in spec:  # several PRs merged over main, like apps.prs
        _fetch(host, repo, REPOS["bennu"], spec["prs"])
        prs = {str(n): _rev(host, repo, f"refs/burger/pr/{n}") for n in spec["prs"]}
        return "refs/burger/main", {"prs": prs}
    _fetch(host, repo, REPOS["bennu"], [spec["pr"]])
    return f"refs/burger/pr/{spec['pr']}", {"pr": spec["pr"]}


def _sync_bennu(host, settings: Settings, rd: Path, attrs: Path, board) -> dict | None:
    spec = board.bennu
    if not spec:
        return None
    repo = _ensure_repo(host, settings, "bennu")
    ref, src = _bennu_source(host, repo, spec)
    _fresh_worktree(host, repo, rd / "bennu", ref)
    if "prs" in spec:
        _merge_prs(host, rd / "bennu", attrs, board.name, spec["prs"])
    return {**src, "sha": _rev(host, rd / "bennu", "HEAD")}


def _sync_pinned(host, settings: Settings, rd: Path, key: str, board) -> dict | None:
    """minimega or phenix at a pinned PR or branch, in its own worktree."""
    spec = getattr(board, key)
    if not spec:
        return None
    repo = _ensure_repo(host, settings, key)
    if "branch" in spec:
        ref = _fetch_branch(host, repo, key, spec)
    else:  # not _fetch: that also wants refs/heads/main, and minimega's is master
        ref = f"refs/burger/pr/{spec['pr']}"
        host.git(repo, "fetch", "--quiet", REPOS[key], f"+refs/pull/{spec['pr']}/head:{ref}")
    _fresh_worktree(host, repo, rd / key, ref)
    return {**spec, "sha": _rev(host, repo, ref), "from": getattr(board, f"{key}_from")}


def _sync_topologies(host, settings: Settings, rd: Path, topo) -> str | None:
    """Archive the route's topology directory into rd; the topologies commit it came from."""
    if not topo:  # a site route brings its own topology: nothing to archive
        return None
    repo = _ensure_repo(host, settings, "topologies")
    _fetch(host, repo, REPOS["topologies"], [])
    shutil.rmtree(rd / topo, ignore_errors=True)
    host.run(
        [
            "sh",
            "-c",
            f"git -C {shlex.quote(str(repo))} archive refs/burger/main {shlex.quote(topo)} "
            f"| tar -x -C {shlex.quote(str(rd))}",
        ]
    )
    return _rev(host, repo, "refs/burger/main")


def _log_synced(board, man: dict) -> None:
    br, bennu = board.apps_branch, man["bennu"]
    pinned = [k for k in ("minimega", "phenix") if man[k]]
    logger.info(
        "switchboard '{}' synced: apps {} ({}){}{}",
        board.name,
        man["apps"]["head"][:10],
        f"branch {br['branch']}" if br else f"main + {len(board.apps_prs)} PRs",
        f", bennu {bennu_label(bennu)} {bennu['sha'][:10]}" if bennu else "",
        "".join(f", {k} {pinned_label(man[k])} {man[k]['sha'][:10]}" for k in pinned),
    )


def sync(res: Resolved, settings: Settings, host) -> dict:
    """Fetch the PRs, merge them over main in fresh worktrees, and write the manifest."""
    board = res.board
    rd = board_dir(settings, board.name)
    rd.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(rd / "out", ignore_errors=True)  # a new sync invalidates every build
    attrs = settings.work / "union-changelog.attributes"
    attrs.write_text("CHANGELOG.md merge=union\n")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    man: dict = {"switchboard": board.name, "synced": now}
    man["apps"] = _sync_apps(host, settings, rd, attrs, board)
    man["bennu"] = _sync_bennu(host, settings, rd, attrs, board)
    for key in ("minimega", "phenix"):
        man[key] = _sync_pinned(host, settings, rd, key, board)
    man["topologies"] = _sync_topologies(host, settings, rd, res.route.topology_dir)
    (rd / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    _log_synced(board, man)
    return man


def bennu_label(b: dict) -> str:
    if "prs" in b:
        return "+".join(f"#{n}" for n in b["prs"])
    return f"#{b['pr']}" if "pr" in b else f"{b['remote']}/{b['branch']}"


def _ls_remote(host, url: str, ref: str) -> str | None:
    out = host.run(["git", "ls-remote", url, ref], check=False, capture=True).stdout
    return out.split()[0] if out.strip() else None


def _ls_remote_pr(host, url: str, n: int) -> str | None:
    return _ls_remote(host, url, f"refs/pull/{n}/head")


def _moved_branches(host, synced: dict) -> set[str]:
    """Branch boards whose branch has a new head since sync. That is all `prs` can see:
    whether the branch still holds its PRs' current heads is for whoever rebuilds it."""
    moved = set()
    for name, man in synced.items():
        a = man["apps"]
        if "branch" not in a:
            continue
        try:
            url = branch_url(host, "apps", a["remote"])
        except BurgerError:
            continue  # no gh user: staleness unknown, as for PRs without gh
        head = _ls_remote(host, url, f"refs/heads/{a['branch']}")
        if head not in (None, a["sha"]):
            moved.add(name)
    return moved


def _pr_sha(man: dict, key: str, n: int) -> str | None:
    if key == "apps":
        return man["apps"]["prs"].get(str(n))
    b = man.get(key) or {}  # bennu, minimega or phenix
    if "prs" in b:
        return b["prs"].get(str(n))
    return b["sha"] if b.get("pr") == n else None


def _board_prs(board) -> list[tuple[str, int]]:
    """(repo key, PR number) for every PR the board carries; a branch carries none."""
    b = board.bennu or {}
    bennu = [b["pr"]] if "pr" in b else b.get("prs", [])
    found = [("apps", int(n)) for n in board.apps_prs] + [("bennu", int(n)) for n in bennu]
    for key in ("minimega", "phenix"):
        if "pr" in (getattr(board, key) or {}):
            found.append((key, getattr(board, key)["pr"]))
    return found


def _carried_and_synced(settings: Settings):
    """Which boards carry each PR (per repo key), and the manifest of each synced board."""
    carried: dict[str, dict[int, list[str]]] = {k: {} for k in SLUGS}
    synced = {}
    for name in all_switchboards(settings):
        for key, n in _board_prs(load_switchboard(name, settings)):
            carried[key].setdefault(n, []).append(name)
        path = board_dir(settings, name) / "manifest.json"
        if path.is_file():
            synced[name] = json.loads(path.read_text())
    return carried, synced


def _open_prs(host, slug: str, author: str) -> dict[int, dict]:
    """`author`'s open PRs of slug by number; none without gh or when it fails."""
    if not shutil.which("gh"):
        return {}
    res = host.run(
        [
            *("gh", "pr", "list", "-R", slug, "--author", author, "--state", "open"),
            *("--json", "number,title,headRefOid,isDraft", "--limit", "100"),
        ],
        check=False,
        capture=True,
    )
    return {p["number"]: p for p in json.loads(res.stdout)} if res.returncode == 0 else {}


def _pr_title(pr: dict | None) -> str:
    if not pr:
        return "(not open)"
    return pr["title"][:60] + (" [draft]" if pr["isDraft"] else "")


def _stale_boards(boards: list[str], moved: set[str], synced: dict, key: str, n, head) -> list:
    return [
        b
        for b in boards
        if b in moved or (b in synced and _pr_sha(synced[b], key, n) not in (None, head))
    ]


def prs(settings: Settings, host, author: str) -> list[dict]:
    """Open PRs by `author` plus the PRs the switchboards carry: which boards carry
    each, and the boards whose synced copy is behind the PR's current head."""
    carried, synced = _carried_and_synced(settings)
    moved = _moved_branches(host, synced)
    rows = []
    for key, slug in SLUGS.items():
        open_prs = _open_prs(host, slug, author)
        for n in sorted(set(open_prs) | set(carried[key])):
            pr = open_prs.get(n)
            head = pr["headRefOid"] if pr else _ls_remote_pr(host, REPOS[key], n)
            boards = carried[key].get(n, [])
            stale = _stale_boards(boards, moved, synced, key, n, head)
            rows.append(
                {
                    "repo": slug,
                    "number": n,
                    "head": head,
                    "boards": boards,
                    "title": _pr_title(pr),
                    "stale": stale,
                }
            )
    return rows
