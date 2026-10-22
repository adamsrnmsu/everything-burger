"""`burger update`: bring a stale phēnix host up to main, one layer at a time."""

import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass, field

from loguru import logger
from rich.table import Table
from rich.text import Text

from burger import experiment, vcs
from burger.bennu import build_bennu
from burger.config import REPOS, BurgerError, Settings
from burger.site import redact
from burger.switchboard import Resolved, Switchboard, resolve

IMAGES_REPO = "https://github.com/sandialabs/sceptre-phenix-images.git"
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


def main_board(s: Settings, name: str = "main", bennu: dict | None = None) -> Resolved:
    """A built-in board: upstream main apps, no PRs, no items; optionally bennu main too."""
    upstream = {"branch": "main", "remote": "upstream"}
    board = Switchboard(
        name=name,
        about="upstream main",
        route="waterway",
        apps_prs=[],
        bennu=bennu,
        items={},
        overrides={},
        runs=None,
        linux_only=False,
        exp=f"ebwes-{name}",
        apps_branch=upstream,
    )
    return resolve(board, s)


def installed_apps(host) -> str:
    """The apps sha burger last installed into the phēnix container, if its marker is there."""
    got = host.ctr("cat", "/tmp/burger/installed.json", check=False, capture=True)
    try:
        return json.loads(got.stdout)["apps"][:10]
    except (ValueError, KeyError, TypeError):
        return "unknown"


def update_apps(ctx: Ctx) -> None:
    """Install upstream phenix-apps main through the usual sync, build, install path.
    Runs after the containers layer: the phēnix entrypoint reinstalls upstream on every boot."""
    s, host = ctx.s, ctx.host
    res = main_board(s)
    before = installed_apps(host)
    if ctx.dry:
        sha = vcs._ls_remote(host, REPOS["apps"], "refs/heads/main") or "?"
        for step in ("sync", "build", "install"):
            ctx.say(f"+ burger {step} main  (upstream phenix-apps main {sha[:10]})")
        ctx.report("apps", before, sha[:10], "dry-run")
        return
    man = vcs.sync(res, s, host)
    experiment.build(res, s, host)
    experiment.install(res, s, host)
    ctx.report("apps", before, man["apps"]["head"][:10], "installed from upstream main")


# scripts/bennu.sh downloads bennu.deb and the pybennu wheel from the latest release; the
# patterns match those two blocks (wget, install, rm) so they can be swapped for local files.
_DEB_BLOCK = re.compile(r"^wget \S+/bennu\.deb .*\napt-get install -y /tmp/bennu\.deb\n.*\n", re.M)
_WHL_BLOCK = re.compile(r"^wget \S+/(pybennu-\S+\.whl) .*\npip install \S+\n.*\n", re.M)
LOCAL_DEBS = "/root/burger-debs"


def patch_bennu_sh(text: str) -> str:
    """bennu.sh with bennu.deb and pybennu installed from files the overlay put in the image.
    helics and libzmq keep their release downloads: burger builds neither."""
    text, n_deb = _DEB_BLOCK.subn(f"apt-get install -y {LOCAL_DEBS}/bennu.deb\n", text)
    text, n_whl = _WHL_BLOCK.subn(
        f"pip install {LOCAL_DEBS}/pybennu-*.whl\nrm -rf {LOCAL_DEBS}\n", text
    )
    if n_deb != 1 or n_whl != 1:
        raise BurgerError(
            "scripts/bennu.sh in sceptre-phenix-images no longer downloads bennu.deb and the "
            "pybennu wheel in the shape burger patches; update patch_bennu_sh"
        )
    return text


def _now() -> dt.datetime:
    return dt.datetime.now()


def images_dir(s: Settings) -> str:
    return os.environ.get("BURGER_IMAGES_DIR", f"{s.phenix_dir}/burger/images")


def _ctr_prefix(s: Settings) -> str:
    return f"docker exec {s.ctr} " if s.ctr else ""


def point_link(ctx: Ctx, name: str) -> str:
    """Make images/bennu.qc2 a symlink to `name`, keeping a real file it replaces under a
    dated name; the command that undoes it."""
    host, imgs = ctx.host, f"{ctx.s.phenix_dir}/images"
    link, prev = f"{imgs}/bennu.qc2", ""
    if host.ctr("test", "-L", link, check=False).returncode == 0:
        prev = host.ctr("readlink", link, capture=True).stdout.strip()
    elif host.ctr("test", "-e", link, check=False).returncode == 0:
        now = _now()
        prev = f"{imgs}/bennu-{now:%Y%m%d}.qc2"
        if host.ctr("test", "-e", prev, check=False).returncode == 0:
            prev = f"{imgs}/bennu-{now:%Y%m%d-%H%M%S}.qc2"
        host.ctr("mv", link, prev)
    host.ctr("ln", "-sfn", name, link)
    return (
        f"{_ctr_prefix(ctx.s)}ln -sfn {prev} {link}" if prev else f"{_ctr_prefix(ctx.s)}rm {link}"
    )


def bennu_current(host, s: Settings) -> str:
    link = f"{s.phenix_dir}/images/bennu.qc2"
    if host.ctr("test", "-L", link, check=False).returncode == 0:
        return os.path.basename(host.ctr("readlink", link, capture=True).stdout.strip())
    return "regular file" if host.ctr("test", "-e", link, check=False).returncode == 0 else "none"


def checkout_images(ctx: Ctx, d: str) -> None:
    """The sceptre-phenix-images checkout, current with origin main."""
    host = ctx.host
    if host.run(["test", "-d", f"{d}/.git"], check=False).returncode:
        host.run(["git", "clone", "--quiet", IMAGES_REPO, d])
    else:
        host.git(d, "fetch", "--quiet", "origin", "main")
        host.git(d, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if not ctx.dry and host.ctr("test", "-f", f"{d}/scripts/bennu.sh", check=False).returncode:
        raise BurgerError(
            f"the phēnix container cannot see {d}/scripts/bennu.sh: {d} must be the same "
            "directory on the host and in the container (set BURGER_IMAGES_DIR)"
        )


def build_image(ctx: Ctx, d: str, out) -> None:
    """The `bennu` target of sceptre-phenix-images' Makefile, with the patched script and
    one more overlay. Not `make`: its paths and `docker exec -t` are for a person at a tty."""
    host, s = ctx.host, ctx.s
    # burger moves its own result away, so a leftover is a failed build or somebody's file
    for name in ("bennu.qc2", "bennu.tar"):
        if host.ctr("test", "-e", f"{d}/{name}", check=False).returncode == 0:
            raise BurgerError(f"{d}/{name} exists; move or remove it, burger will not delete it")

    def stage():
        shutil.rmtree(f"{d}/overlays/burger-bennu", ignore_errors=True)
        debs = f"{d}/overlays/burger-bennu{LOCAL_DEBS}"
        os.makedirs(debs)
        for f in (out / "bennu.deb", *out.glob("pybennu-*.whl")):
            shutil.copy2(f, debs)
        with open(f"{d}/scripts/bennu.sh") as f:
            script = patch_bennu_sh(f.read())
        with open(f"{d}/scripts/bennu-burger.sh", "w") as f:
            f.write(script)
        os.chmod(f"{d}/scripts/bennu-burger.sh", 0o755)

    ctx.do(f"stage overlays/burger-bennu and scripts/bennu-burger.sh in {d}", stage)
    # The Makefile refuses while the image config exists.
    listing = host.phenix("cfg", "list", check=False, capture=True).stdout.splitlines()
    cells = (line.split() for line in listing if "Image" in line)
    if any(len(c) > 5 and c[5] == "bennu" for c in cells):
        host.phenix("image", "delete", "bennu")
    mirror = ((host.site.mirrors if host.site else {}).get("apt")) or ""
    host.phenix(
        "image", "create",
        "-O", f"{d}/overlays/bennu,{d}/overlays/brash,{d}/overlays/burger-bennu",
        "-T", f"{d}/scripts/bennu-burger.sh",
        *([f"--mirror={mirror}"] if mirror else []),
        "--compress", "bennu",
    )  # fmt: skip
    host.phenix("image", "build", "-o", d, "-c", "-x", "bennu")
    for miniccc in (f"{s.phenix_dir}/miniccc", f"{d}/miniccc"):
        if host.ctr("test", "-f", miniccc, check=False).returncode == 0:
            host.phenix("image", "inject-miniexe", miniccc, f"{d}/bennu.qc2")
            return
    ctx.say("no miniccc in the phenix directory or images checkout: not injected into bennu.qc2")


def update_bennu(ctx: Ctx) -> None:
    """bennu.qc2 from bennu main: build, image build in the phēnix container, symlink."""
    s, host = ctx.s, ctx.host
    guard_experiments(ctx, "bennu.qc2")
    d = images_dir(s)
    if host.ctr("test", "-d", d, check=False).returncode:
        msg = (
            f"{d} is not a directory in the phēnix container. The images Makefile passes paths "
            "through `docker exec`, so burger needs a sceptre-phenix-images checkout at one "
            f"path the host and the container both see: mkdir it on a bind mount "
            "(default /phenix/burger/images) or set BURGER_IMAGES_DIR"
        )
        if not ctx.dry:
            raise BurgerError(msg)
        ctx.say(f"! a real run would stop here: {msg}")
    before = bennu_current(host, s)
    sha = vcs._ls_remote(host, REPOS["bennu"], "refs/heads/main") or ""
    imgs = f"{s.phenix_dir}/images"
    if (
        sha
        and host.ctr("test", "-e", f"{imgs}/bennu-main-{sha[:7]}.qc2", check=False).returncode == 0
    ):
        name = f"bennu-main-{sha[:7]}.qc2"
        note = "image already built"
    else:
        name, note = _build_bennu_image(ctx, d, sha)
    if before == name:
        ctx.report("bennu", before, name, "already current")
        return
    rollback = point_link(ctx, name)
    ctx.report("bennu", before, name, note)
    ctx.say(f"to roll back: {rollback}")
    ctx.say("helics and libzmq in the image still come from the latest sceptre-bennu release")


def _build_bennu_image(ctx: Ctx, d: str, sha: str) -> tuple[str, str]:
    s, host = ctx.s, ctx.host
    spec = {"branch": "main", "remote": "upstream", "install": ["deb", "pybennu"]}
    res = main_board(s, "upd-bennu", spec)
    checkout_images(ctx, d)
    out = vcs.board_dir(s, res.board.name) / "out"
    if ctx.dry:
        ctx.say(f"+ burger sync and build bennu main (deb, pybennu wheel) into {out}")
        sha = sha or "<sha7>"
    else:
        man = vcs.sync(res, s, host)
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        build_bennu(host, vcs.board_dir(s, res.board.name) / "bennu", out, man, spec["install"], [])
        sha = man["bennu"]["sha"]
    name = f"bennu-main-{sha[:7]}.qc2"
    target = f"{s.phenix_dir}/images/{name}"
    if host.ctr("test", "-e", target, check=False).returncode == 0:
        raise BurgerError(f"{target} exists; remove it to rebuild")
    build_image(ctx, d, out)
    host.ctr("mv", f"{d}/bennu.qc2", target)
    return name, f"built from bennu main {sha[:10]}"


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
        elif name == "apps":
            update_apps(ctx)
        elif name == "bennu":
            update_bennu(ctx)
    return ctx.rows
