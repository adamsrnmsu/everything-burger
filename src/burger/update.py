"""`burger update`: bring a stale phēnix host up to main, one layer at a time."""

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass, field

from loguru import logger
from rich.table import Table
from rich.text import Text

from burger.config import BurgerError, Settings
from burger.site import redact

LAYERS = ("burger", "containers", "apps", "bennu")
# Probes that read state; --dry-run still runs these so it can report what it would change.
_READ_CTR = {"test", "cat", "readlink", "ls", "stat"}
_READ_PHENIX = (("version",), ("mm", "version"), ("cfg", "list"), ("experiment", "list"))
_READ_GIT = {"status", "rev-parse", "ls-remote", "log", "diff"}


def _read_only(cmd: list[str]) -> bool:
    if cmd[:2] == ["docker", "inspect"]:
        return True
    if cmd[:2] == ["docker", "compose"]:
        return "config" in cmd
    if cmd[0] == "git":
        sub = cmd[3] if cmd[1:2] == ["-C"] else cmd[1]
        return sub in _READ_GIT
    rest = cmd[3:] if cmd[:2] == ["docker", "exec"] else cmd
    if rest[:1] and rest[0] in _READ_CTR:
        return True
    return rest[:1] == ["phenix"] and any(tuple(rest[1 : 1 + len(p)]) == p for p in _READ_PHENIX)


def dry_host(host, say):
    """A host that runs read-only probes and prints every other command instead."""

    class Dry(type(host)):
        def run(self, cmd, check=True, capture=False, **kw):
            cmd = [str(c) for c in cmd]
            if _read_only(cmd):
                return host.run(cmd, check=check, capture=capture, **kw)
            say(f"+ {redact(shlex.join(cmd))}")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def put(self, src, dst):
            say(f"+ put {src} -> {dst} (in {self.s.ctr or 'this host'})")

    dry = Dry.__new__(Dry)
    dry.__dict__ = host.__dict__  # shared, so caches such as the gh user carry over
    return dry


@dataclass
class Ctx:
    s: Settings
    host: object
    say: object  # prints one line
    dry: bool = False
    force: bool = False
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)

    def do(self, what: str, fn):
        """Run fn, or under --dry-run say what it would do."""
        if self.dry:
            self.say(f"+ {what}")
            return None
        return fn()

    def report(self, layer: str, before: str, after: str, note: str = "") -> None:
        self.rows.append((layer, before, after, note))
        self.say(f"{layer}: {before} -> {after}" + (f" ({note})" if note else ""))


def summary_table(rows) -> Table:
    table = Table("layer", "before", "after", "note")
    for row in rows:
        table.add_row(*(Text(c) for c in row))
    return table


def _out(host, *cmd) -> str:
    return host.run(list(cmd), check=False, capture=True).stdout.strip()


def update_burger(ctx: Ctx, remaining: list[str]) -> None:
    """Fast-forward the checkout, reinstall it, and hand the rest to the new code."""
    root, host = ctx.s.root, ctx.host
    if host.git(root, "status", "--porcelain", "--untracked-files=no", capture=True).stdout.strip():
        raise BurgerError(f"{root} has uncommitted changes; commit or stash them first")
    branch = host.git(root, "rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip()
    if branch != "main":
        raise BurgerError(f"{root} is on '{branch}', not main; check out main first")
    before = host.git(root, "rev-parse", "HEAD", capture=True).stdout.strip()
    host.git(root, "pull", "--quiet", "--ff-only")
    host.run([root / ".venv" / "bin" / "pip", "install", "-q", "-e", root])
    after = host.git(root, "rev-parse", "HEAD", capture=True).stdout.strip()
    note = "dry-run: not pulled" if ctx.dry else "already current" if after == before else ""
    ctx.report("burger", before[:10], after[:10], note)
    if after != before and remaining:
        argv = ["burger", "update", "--only", ",".join(remaining)]
        argv += [f for f, on in (("--dry-run", ctx.dry), ("--force", ctx.force)) if on]
        ctx.say(f"burger moved: continuing with the new code: {' '.join(argv[1:])}")
        sys.stdout.flush()
        os.execv(root / "burger", argv)


def running_experiments(host) -> list[str]:
    try:
        data = host.api("/experiments")
    except (OSError, urllib.error.URLError) as e:
        raise BurgerError(
            f"cannot list phēnix experiments ({e}); fix BURGER_API or pass --force"
        ) from None
    if data is None:  # Host.api turns HTTP 400/404/500 into None
        raise BurgerError("phēnix answered the experiments request with an error; pass --force")
    exps = data.get("experiments") if isinstance(data, dict) else data
    return [e["name"] for e in exps or [] if e.get("running")]


def guard_experiments(ctx: Ctx, what: str) -> None:
    if ctx.force:  # also when the API is unreachable, which is how a stale host looks
        return
    running = running_experiments(ctx.host)
    if running:
        raise BurgerError(
            f"experiments running: {', '.join(running)}; stop them before updating {what} "
            "or pass --force"
        )


def wait_phenix(host, timeout: int, clock=time) -> None:
    """Poll `phenix version` and `phenix mm version` until both answer."""
    deadline = clock.time() + timeout
    while True:
        cmds = (("version",), ("mm", "version"))
        if all(host.phenix(*c, check=False, capture=True).returncode == 0 for c in cmds):
            return
        if clock.time() > deadline:
            raise BurgerError(f"phenix and minimega not answering {timeout}s after the update")
        clock.sleep(5)


def compose_labels(host, name: str) -> dict | None:
    """The compose project facts of a container, None when compose does not manage it."""
    res = host.run(
        ["docker", "inspect", "-f", "{{json .Config.Labels}}", name], check=False, capture=True
    )
    labels = json.loads(res.stdout.strip() or "null") if res.returncode == 0 else None
    wd = (labels or {}).get("com.docker.compose.project.working_dir")
    files = (labels or {}).get("com.docker.compose.project.config_files")
    svc = (labels or {}).get("com.docker.compose.service")
    if not (wd and files and svc):
        return None
    project = labels.get("com.docker.compose.project")
    return {"dir": wd, "files": files.split(","), "service": svc, "project": project}


def _compose(c: dict) -> list[str]:
    """`docker compose` aimed at the project the container belongs to, whatever it was
    started with (-p, COMPOSE_PROJECT_NAME): else compose derives a name and duplicates it."""
    cmd = ["docker", "compose", *(["-p", c["project"]] if c["project"] else [])]
    cmd += ["--project-directory", c["dir"]]
    return cmd + [a for f in c["files"] for a in ("-f", f)]


def _image_id(host, name: str) -> str:
    out = _out(host, "docker", "inspect", "-f", "{{.Image}}", name)
    return out.removeprefix("sha256:")[:12] or "?"


def _local_build(host, c: dict) -> bool:
    """A service with a build context and no image name is built here, not pulled."""
    res = host.run([*_compose(c), "config", "--format", "json"], check=False, capture=True)
    try:
        svc = json.loads(res.stdout)["services"][c["service"]]
    except (ValueError, KeyError, TypeError):
        return False
    return "build" in svc and "image" not in svc


def update_container(ctx: Ctx, name: str) -> bool:
    """Pull and recreate one container through compose; whether it was touched."""
    host = ctx.host
    c = compose_labels(host, name)
    if c is None:
        image = _out(host, "docker", "inspect", "-f", "{{.Config.Image}}", name) or "<image>"
        ctx.report(
            name,
            _image_id(host, name),
            "-",
            f"not managed by compose: run `docker pull {image}` and recreate '{name}' with its "
            "original flags",
        )
        return False
    if _local_build(host, c):
        ctx.report(name, _image_id(host, name), "-", "built from a local context; not rebuilt")
        return False
    base = _compose(c)
    before = _image_id(host, name)
    host.run([*base, "pull", c["service"]])
    # --no-deps: minimega is handled as its own container, not as phenix's dependency
    host.run([*base, "up", "-d", "--no-deps", c["service"]])
    after = _image_id(host, name)
    note = "dry-run" if ctx.dry else "recreated" if after != before else "image unchanged"
    ctx.report(name, before, after, note)
    return True


def update_containers(ctx: Ctx, timeout: int = 180) -> None:
    guard_experiments(ctx, "the containers")
    names = [n for n in (ctx.s.ctr, ctx.s.mm_ctr) if n]
    if not names:
        ctx.report("containers", "-", "-", "no containers configured; nothing to do")
        return
    touched = [update_container(ctx, n) for n in names]
    if any(touched) and not ctx.dry:
        wait_phenix(ctx.host, timeout)
        version = ctx.host.phenix("version", check=False, capture=True).stdout.strip()
        ctx.say(f"phenix version: {version.splitlines()[0] if version else '?'}")


def update(s: Settings, host, only: list[str], dry_run: bool, force: bool, say) -> list:
    """Run the chosen layers in order; the summary rows."""
    unknown = [n for n in only if n not in LAYERS]
    if unknown:
        raise BurgerError(f"unknown layer '{unknown[0]}' (layers: {', '.join(LAYERS)})")
    names = [n for n in LAYERS if n in only]
    ctx = Ctx(s, dry_host(host, say) if dry_run else host, say, dry_run, force)
    for i, name in enumerate(names):
        logger.info("update layer: {}", name)
        if name == "burger":
            update_burger(ctx, names[i + 1 :])
        elif name == "containers":
            update_containers(ctx)
    return ctx.rows
