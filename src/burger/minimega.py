"""Run a board on a minimega PR or branch: build its image, swap the compose service."""

import shutil
from pathlib import Path

import yaml
from loguru import logger

from burger import update  # update imports experiment, which imports this: use it at call time
from burger.checks import Row
from burger.config import BurgerError, Settings
from burger.site import _FROM, build_dockerfile

LABEL = "burger.minimega"  # on the minimega container burger swapped in: the commit it runs
LIB = "/opt/minimega/lib"  # minimega.py in the minimega image; the phēnix image installs it
# Run where phēnix runs: the sha256 of the minimega.py its Python imports.
PY_FILE = "import minimega; print(minimega.__file__)"
PY_HASH = (
    "import hashlib, minimega; "
    "print(hashlib.sha256(open(minimega.__file__, 'rb').read()).hexdigest())"
)


def image_tag(sha: str) -> str:
    return f"burger/minimega:{sha[:12]}"


def _built(host, tag: str) -> bool:
    return host.run(["docker", "image", "inspect", tag], check=False, capture=True).returncode == 0


def build_image(host, wt: Path, sha: str, layer: str | None = None) -> str:
    """Build minimega's own docker/Dockerfile from the worktree; the image tag.
    The Dockerfile runs minimega's check, build and test scripts, so a broken change fails here.
    The worktree's .git file stays in the context: the doc stage copies /minimega/.git, and
    Go treats a .git file as a repository only when the gitdir it names exists, which it
    does not inside the build, so there is no VCS stamping to fail.

    `layer` is the route's minimega_layer: a Dockerfile the range puts on top of upstream
    minimega (the arm64 rig adds firmware and a kvm wrapper). It is built from a copy of
    its directory with its first FROM pointed at the PR build."""
    tag = image_tag(sha)
    # ponytail: cached by the PR sha only; after editing the layer, docker rmi the tag
    if _built(host, tag):
        logger.info("minimega image {} already built", tag)
        return tag
    src = Path(layer) if layer else None
    if src and not src.is_file():
        raise BurgerError(f"the route's minimega_layer {src} is not a file")
    base = f"burger/minimega-base:{sha[:12]}" if src else tag
    if not (src and _built(host, base)):
        build_dockerfile(host, wt, base, dockerfile="docker/Dockerfile")
    if src:
        ctx = host.s.work / "minimega" / "layer"
        shutil.rmtree(ctx, ignore_errors=True)
        shutil.copytree(src.parent, ctx, ignore=shutil.ignore_patterns("tmp", ".git"))
        lines, done = [], False
        for line in src.read_text().splitlines():
            m = None if done else _FROM.match(line)
            lines.append(f"{m[1]}{m[2]}{base}{m[4]}" if m else line)
            done = done or bool(m)
        (ctx / "Dockerfile").write_text("\n".join(lines) + "\n")
        build_dockerfile(host, ctx, tag)
    return tag


def running_sha(host, s: Settings) -> str | None:
    """The commit of burger's minimega in the running container, None on stock minimega."""
    if not s.mm_ctr:
        return None
    return update.container_labels(host, s.mm_ctr).get(LABEL)


def _install_py(host, s: Settings) -> None:
    """Copy the running minimega container's minimega.py over the one phēnix imports.
    A file copy, not pip: minimega's lib needs setuptools>=77 to build, the phēnix image
    (ubuntu 24.04) has 68, and it has no index to fetch a newer one."""
    local = s.work / "minimega" / "minimega.py"
    local.parent.mkdir(parents=True, exist_ok=True)
    host.run(["docker", "cp", f"{s.mm_ctr}:{LIB}/minimega.py", local])
    where = host.ctr("python3", "-c", PY_FILE, capture=True).stdout.strip()
    if not where.endswith(".py"):
        raise BurgerError(
            f"phēnix imports minimega from {where or 'nowhere'}, not a minimega.py file "
            "burger can replace"
        )
    host.put(local, where)


def _recreate(host, s: Settings, sha: str | None, timeout: int = 180) -> None:
    """Recreate the compose minimega service: on burger's image for `sha`, or stock for None.
    This kills every VM, so the callers check that no experiment runs."""
    c = update.compose_labels(host, s.mm_ctr)
    if c is None:
        raise BurgerError(
            f"container '{s.mm_ctr}' is not managed by docker compose; minimega must run "
            "under docker compose for burger to swap its image"
        )
    extra = []
    if sha:
        service = {"image": image_tag(sha), "pull_policy": "never", "labels": {LABEL: sha}}
        path = update.burger_override(s)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"services": {c["service"]: service}}))
        extra = ["-f", path]
    # c's files never include burger's override, so without `extra` this is the stock service
    host.run(
        [*update._compose(c), *extra, "up", "-d", "--no-deps", "--force-recreate", c["service"]]
    )
    update.wait_phenix(host, timeout)
    if not sha and running_sha(host, s):  # the override came back: its path did not match
        raise BurgerError(
            f"minimega container '{s.mm_ctr}' still runs burger's build after the restore; "
            "recreate it from the project's own compose files by hand"
        )
    _install_py(host, s)


def ensure(host, s: Settings, want: str | None) -> None:
    """Make the running minimega the one the board wants: burger's build of commit `want`,
    or stock for None (which also undoes a swap a killed run left behind)."""
    if not s.mm_ctr:
        if want:
            raise BurgerError(
                "this switchboard runs on its own minimega, which burger swaps into the "
                "minimega container: set BURGER_MINIMEGA_CONTAINER to its name"
            )
        return
    have = running_sha(host, s)
    if have == want:
        if want:  # no recreate; but a recreated phēnix container has the stock minimega.py
            _install_py(host, s)
        return
    running = update.running_experiments(host)
    if running:
        raise BurgerError(
            f"experiments running: {', '.join(running)}; changing minimega "
            f"({(have or 'stock')[:12]} -> {(want or 'stock')[:12]}) would kill their VMs, "
            "so bring them down first (burger down <board>)"
        )
    _recreate(host, s, want)
    logger.info(
        "minimega container '{}' now runs {}", s.mm_ctr, image_tag(want) if want else "stock"
    )


def restore(host, s: Settings) -> None:
    """Put stock minimega back after a board that swapped it, unless VMs still need it."""
    have = running_sha(host, s)
    if have is None:
        return
    running = update.running_experiments(host)
    if running:
        logger.warning(
            "minimega stays on {} while experiments run ({}); burger down the last of them "
            "restores stock",
            image_tag(have),
            ", ".join(running),
        )
        return
    _recreate(host, s, None)
    logger.info("minimega container '{}' is back on stock", s.mm_ctr)


def rows(host, s: Settings, man: dict, board: str) -> list[Row]:
    """verify's proof of which minimega ran: the container's commit, and for a board with
    its own minimega, that phēnix imports that container's minimega.py."""
    want = (man.get("minimega") or {}).get("sha")
    checks = ["minimega container", *(["minimega.py in phēnix"] if want else [])]
    if not s.mm_ctr:
        return [Row("-", c, "skipped", "BURGER_MINIMEGA_CONTAINER is empty", []) for c in checks]
    have = running_sha(host, s)
    reason = ""
    if have != want:
        reason = (
            f"the container runs {(have or 'stock')[:12]}, want {(want or 'stock')[:12]}; "
            f"burger install {board}"
        )
    out = [Row("-", checks[0], "FAIL" if reason else "ok", reason, [])]
    if want:
        ours = host.run(
            ["docker", "exec", s.mm_ctr, "sha256sum", f"{LIB}/minimega.py"],
            check=False,
            capture=True,
        ).stdout.split()[:1]
        theirs = host.ctr("python3", "-c", PY_HASH, check=False, capture=True).stdout.split()[:1]
        reason = ""
        if not ours or ours != theirs:  # an unreadable file is not a match
            reason = (
                f"phēnix imports {(theirs or ['no minimega.py'])[0][:12]}, the minimega "
                f"container has {(ours or ['none'])[0][:12]}; burger install {board}"
            )
        out.append(Row("-", checks[1], "FAIL" if reason else "ok", reason, []))
    return out
