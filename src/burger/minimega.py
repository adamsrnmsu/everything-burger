"""Run a board on a minimega PR or branch: build its image, swap the compose service."""

import hashlib
import shutil
from pathlib import Path

from loguru import logger

from burger import _swap as _swap_mod
from burger import update  # update imports experiment, which imports this: use it at call time
from burger.checks import Row
from burger.config import BurgerError, Settings
from burger.site import build_dockerfile, retarget_first_from

LABEL = "burger.minimega"  # on the minimega container burger swapped in: the commit it runs
IMAGE_LABEL = "burger.minimega.image"  # and the image tag, which also says whether it is layered
LIB = "/opt/minimega/lib"  # minimega.py in the minimega image; the phēnix image installs it
# Run where phēnix runs: the sha256 of the minimega.py its Python imports.
PY_FILE = "import minimega; print(minimega.__file__)"
PY_HASH = (
    "import hashlib, minimega; "
    "print(hashlib.sha256(open(minimega.__file__, 'rb').read()).hexdigest())"
)


def image_tag(sha: str, layer_hash: str | None = None) -> str:
    """burger/minimega:<sha12>, or <sha12>-<layer hash> for a build with the route's layer, so
    the two never share a tag (burger-fr8)."""
    return f"burger/minimega:{sha[:12]}" + (f"-{layer_hash}" if layer_hash else "")


def _layer_hash(src: Path) -> str:
    """8 hex digits over the files of the layer's directory that the build copies (no tmp/, .git):
    editing the Dockerfile or a file it copies in gives a new tag, and so a rebuild."""
    h = hashlib.sha256()
    for p in sorted(src.parent.rglob("*")):
        rel = p.relative_to(src.parent)
        if p.is_file() and not {"tmp", ".git"} & set(rel.parts):
            h.update(f"{rel}\0".encode() + p.read_bytes() + b"\0")
    return h.hexdigest()[:8]


def _built(host, tag: str) -> bool:
    return host.run(["docker", "image", "inspect", tag], check=False, capture=True).returncode == 0


def _build_layer(host, src: Path, base: str, tag: str) -> None:
    """Build the layer Dockerfile `src` from a copy of its directory, first FROM on `base`."""
    ctx = host.s.work / "minimega" / "layer"
    shutil.rmtree(ctx, ignore_errors=True)
    shutil.copytree(src.parent, ctx, ignore=shutil.ignore_patterns("tmp", ".git"))
    (ctx / "Dockerfile").write_text(retarget_first_from(src.read_text(), base))
    build_dockerfile(host, ctx, tag)


def _layer_file(layer: str | None) -> Path | None:
    src = Path(layer) if layer else None
    if src and not src.is_file():
        raise BurgerError(f"the route's minimega_layer {src} is not a file")
    return src


def build_image(host, wt: Path, sha: str, layer: str | None = None) -> str:
    """Build minimega's own docker/Dockerfile from the worktree; the image tag.
    The Dockerfile runs minimega's check, build and test scripts, so a broken change fails here.
    The worktree's .git file stays in the context: the doc stage copies /minimega/.git, and
    Go treats a .git file as a repository only when the gitdir it names exists, which it
    does not inside the build, so there is no VCS stamping to fail.

    `layer` is the route's minimega_layer: a Dockerfile the range puts on top of upstream
    minimega (the arm64 rig adds firmware and a kvm wrapper). It is built from a copy of
    its directory with its first FROM pointed at the PR build."""
    src = _layer_file(layer)
    tag = image_tag(sha, _layer_hash(src) if src else None)
    if _built(host, tag):
        logger.info("minimega image {} already built", tag)
        return tag
    base = f"burger/minimega-base:{sha[:12]}" if src else tag
    if not (src and _built(host, base)):
        build_dockerfile(host, wt, base, dockerfile="docker/Dockerfile")
    if src:
        _build_layer(host, src, base, tag)
    return tag


def running_sha(host, s: Settings) -> str | None:
    """The commit of burger's minimega in the running container, None on stock minimega."""
    if not s.mm_ctr:
        return None
    return update.container_labels(host, s.mm_ctr).get(LABEL)


def running_image(host, s: Settings) -> str | None:
    """The tag of burger's minimega in the running container, None on stock minimega.
    A swap from before IMAGE_LABEL ran the plain build of its commit."""
    if not s.mm_ctr:
        return None
    got = update.container_labels(host, s.mm_ctr)
    return got.get(IMAGE_LABEL) or (image_tag(got[LABEL]) if got.get(LABEL) else None)


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


def _recreate(
    host, s: Settings, sha: str | None, image: str | None = None, timeout: int = 180
) -> None:
    """Recreate the compose minimega service: on burger's `image` of `sha`, or stock for None.
    This kills every VM, so the callers check that no experiment runs."""
    service = None
    if sha:
        image = image or image_tag(sha)
        service = {
            "image": image,
            "pull_policy": "never",
            "labels": {LABEL: sha, IMAGE_LABEL: image},
        }
    update.recreate(host, s.mm_ctr, update.burger_override(s), service, timeout)
    if not sha and running_sha(host, s):  # the override came back: its path did not match
        raise BurgerError(
            f"minimega container '{s.mm_ctr}' still runs burger's build after the restore; "
            "recreate it from the project's own compose files by hand"
        )
    _install_py(host, s)


def _swap() -> _swap_mod.Swap:
    return _swap_mod.Swap(
        "minimega",
        "minimega",
        "BURGER_MINIMEGA_CONTAINER",
        "would kill their VMs",
        lambda s: s.mm_ctr,
        image_tag,
        running_sha,
        running_image,
        _recreate,
        _install_py,  # a recreated phēnix container has the stock minimega.py
    )


# Make the running minimega the one the board wants: burger's build `image` of commit
# `want` (default its plain build), or stock for None (which also undoes a swap a killed run
# left behind). The image, not only the commit, must match: a plain and a layered build of
# one commit are different minimegas (burger-fr8).
def ensure(host, s: Settings, want: str | None, image: str | None = None) -> None:
    _swap_mod.ensure(_swap(), host, s, want, image)


# Put stock minimega back after a board that swapped it, unless VMs still need it.
def restore(host, s: Settings) -> None:
    _swap_mod.restore(_swap(), host, s)


def _container_reason(host, s: Settings, mm: dict, board: str) -> str:
    """Why the container is not the board's minimega, "" when it is."""
    want, have = mm.get("sha"), running_sha(host, s)
    if have != want:
        return (
            f"the container runs {(have or 'stock')[:12]}, want {(want or 'stock')[:12]}; "
            f"burger install {board}"
        )
    if mm.get("image") and running_image(host, s) != mm["image"]:  # layered or not
        return (
            f"the container runs {_swap_mod._short(running_image(host, s))}, "
            f"want {_swap_mod._short(mm['image'])}; burger install {board}"
        )
    return ""


def _py_reason(host, s: Settings, board: str) -> str:
    """Why phēnix does not import the container's minimega.py, "" when it does."""
    ours = host.run(
        ["docker", "exec", s.mm_ctr, "sha256sum", f"{LIB}/minimega.py"],
        check=False,
        capture=True,
    ).stdout.split()[:1]
    theirs = host.ctr("python3", "-c", PY_HASH, check=False, capture=True).stdout.split()[:1]
    if ours and ours == theirs:  # an unreadable file is not a match
        return ""
    return (
        f"phēnix imports {theirs[0][:12] if theirs else 'no minimega.py'}, the minimega "
        f"container has {ours[0][:12] if ours else 'none'}; burger install {board}"
    )


def _row(check: str, reason: str) -> Row:
    return Row("-", check, "FAIL" if reason else "ok", reason, [])


def rows(host, s: Settings, man: dict, board: str) -> list[Row]:
    """verify's proof of which minimega ran: the container's commit, and for a board with
    its own minimega, that phēnix imports that container's minimega.py."""
    mm = man.get("minimega") or {}
    checks = ["minimega container", *(["minimega.py in phēnix"] if mm.get("sha") else [])]
    if not s.mm_ctr:
        return [Row("-", c, "skipped", "BURGER_MINIMEGA_CONTAINER is empty", []) for c in checks]
    out = [_row(checks[0], _container_reason(host, s, mm, board))]
    if mm.get("sha"):
        out.append(_row(checks[1], _py_reason(host, s, board)))
    return out
