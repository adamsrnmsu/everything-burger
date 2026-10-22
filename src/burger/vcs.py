import datetime as dt
import json
import os
import shlex
import shutil
from pathlib import Path
from urllib.parse import quote

from loguru import logger

from burger.config import GIT_ID, REPOS, BurgerError, Settings
from burger.switchboard import Resolved, all_switchboards, load_switchboard

SLUGS = {"apps": "sandialabs/sceptre-phenix-apps", "bennu": "sandialabs/sceptre-bennu"}


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
    """The gh user: the identity `burger prs` means by @me. Asked once per host object."""
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
    man["apps"] = {**br, "sha": sha, "prs": {}} if br else {"base": sha, "prs": {}}
    for n in prs:
        man["apps"]["prs"][str(n)] = _rev(host, repo, f"refs/burger/pr/{n}")
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
                f"switchboard '{board.name}': #{n} conflicts with the PRs before it "
                f"in {', '.join(bad) or '(none; see git output)'}; drop or reorder it in "
                f"switchboards/{board.name}.yaml\n{merged.stdout}{merged.stderr}".rstrip()
            )
    man["apps"]["head"] = _rev(host, wt, "HEAD")

    man["bennu"] = None
    spec = board.bennu
    if spec:
        brepo = _ensure_repo(host, settings, "bennu")
        if "branch" in spec:
            ref = _fetch_branch(host, brepo, "bennu", spec)
            src = {"branch": spec["branch"], "remote": spec["remote"]}
        else:
            _fetch(host, brepo, REPOS["bennu"], [spec["pr"]])
            ref, src = f"refs/burger/pr/{spec['pr']}", {"pr": spec["pr"]}
        _fresh_worktree(host, brepo, rd / "bennu", ref)
        man["bennu"] = {**src, "sha": _rev(host, brepo, ref)}

    topo = res.route.topology_dir  # a site route brings its own topology: nothing to archive
    man["topologies"] = None
    if topo:
        trepo = _ensure_repo(host, settings, "topologies")
        _fetch(host, trepo, REPOS["topologies"], [])
        man["topologies"] = _rev(host, trepo, "refs/burger/main")
        shutil.rmtree(rd / topo, ignore_errors=True)
        host.run(
            [
                "sh",
                "-c",
                f"git -C {shlex.quote(str(trepo))} archive refs/burger/main {shlex.quote(topo)} "
                f"| tar -x -C {shlex.quote(str(rd))}",
            ]
        )

    (rd / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    logger.info(
        "switchboard '{}' synced: apps {} ({}){}",
        board.name,
        man["apps"]["head"][:10],
        f"branch {br['branch']}" if br else f"main + {len(prs)} PRs",
        f", bennu {bennu_label(man['bennu'])} {man['bennu']['sha'][:10]}" if spec else "",
    )
    return man


def bennu_label(b: dict) -> str:
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
    return man["bennu"]["sha"] if man.get("bennu") and man["bennu"].get("pr") == n else None


def prs(settings: Settings, host, author: str) -> list[dict]:
    """Open PRs by `author` plus the PRs the switchboards carry: which boards carry
    each, and the boards whose synced copy is behind the PR's current head."""
    carried: dict[str, dict[int, list[str]]] = {"apps": {}, "bennu": {}}
    synced = {}
    for name in all_switchboards(settings):
        board = load_switchboard(name, settings)
        for n in board.apps_prs:
            carried["apps"].setdefault(int(n), []).append(name)
        if (board.bennu or {}).get("pr"):  # a bennu branch carries no PR
            carried["bennu"].setdefault(int(board.bennu["pr"]), []).append(name)
        path = board_dir(settings, name) / "manifest.json"
        if path.is_file():
            synced[name] = json.loads(path.read_text())

    moved = _moved_branches(host, synced)
    rows = []
    for key, slug in SLUGS.items():
        open_prs = {}
        if shutil.which("gh"):
            res = host.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "-R",
                    slug,
                    "--author",
                    author,
                    "--state",
                    "open",
                    "--json",
                    "number,title,headRefOid,isDraft",
                    "--limit",
                    "100",
                ],
                check=False,
                capture=True,
            )
            if res.returncode == 0:
                open_prs = {p["number"]: p for p in json.loads(res.stdout)}
        for n in sorted(set(open_prs) | set(carried[key])):
            pr = open_prs.get(n)
            head = pr["headRefOid"] if pr else _ls_remote_pr(host, REPOS[key], n)
            boards = carried[key].get(n, [])
            title = "(not open)"
            if pr:
                title = pr["title"][:60] + (" [draft]" if pr["isDraft"] else "")
            rows.append(
                {
                    "repo": slug,
                    "number": n,
                    "head": head,
                    "boards": boards,
                    "title": title,
                    "stale": [
                        b
                        for b in boards
                        if b in moved
                        or (b in synced and _pr_sha(synced[b], key, n) not in (None, head))
                    ],
                }
            )
    return rows
