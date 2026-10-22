"""`burger update`: bring a stale phēnix host up to main, one layer at a time."""

import os
import shlex
import subprocess
import sys
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
    return ctx.rows
