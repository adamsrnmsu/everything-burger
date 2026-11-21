"""Run a board on a sceptre-phenix PR or branch: build its image, swap the compose service.
The same shape as minimega.py: its own override file, labels, ensure/restore and verify row."""

import hashlib
from pathlib import Path

from loguru import logger

from burger import _swap as _swap_mod
from burger import update  # update imports experiment, which imports this: use it at call time
from burger._swap import _short
from burger.checks import Row
from burger.config import BurgerError, Settings
from burger.minimega import _built
from burger.site import build_dockerfile, retarget_first_from

LABEL = "burger.phenix"  # on the phēnix container burger swapped in: the commit it runs
IMAGE_LABEL = "burger.phenix.image"  # and the image tag, which says which minimega it carries
DOCKERFILE = "docker/Dockerfile.burger"


def image_tag(sha: str, mm_image: str | None) -> str:
    """burger/phenix:<sha12>-<12 hex of the minimega image's tag>, or -stock: one commit built
    over two minimegas is two phēnixes."""
    return f"burger/phenix:{sha[:12]}-" + (
        hashlib.sha256(mm_image.encode()).hexdigest()[:12] if mm_image else "stock"
    )


def build_image(host, wt: Path, sha: str, mm_image: str | None, sed: str | None) -> str:
    """Build the worktree's docker/Dockerfile into an image; the tag. The route's `sed` script
    (arm64: x86 downloads and checksums) is applied first, then the first FROM, the minimega
    stage, is pointed at the board's minimega so the image installs that minimega's bindings."""
    tag = image_tag(sha, mm_image)
    if _built(host, tag):
        logger.info("phenix image {} already built", tag)
        return tag
    src = wt / "docker" / "Dockerfile"
    text = host.run(["sed", "-f", sed, src], capture=True).stdout if sed else src.read_text()
    if mm_image:
        text = retarget_first_from(text, mm_image)
    (wt / DOCKERFILE).write_text(text)
    build_dockerfile(host, wt, tag, dockerfile=DOCKERFILE, build_args=(f"PHENIX_COMMIT={sha}",))
    return tag


def _labels(host, s: Settings) -> dict:
    return update.container_labels(host, s.ctr) if s.ctr else {}


def running_sha(host, s: Settings) -> str | None:
    """The commit of burger's phēnix in the running container, None on stock phēnix."""
    return _labels(host, s).get(LABEL)


def running_image(host, s: Settings) -> str | None:
    return _labels(host, s).get(IMAGE_LABEL)


def _recreate(host, s: Settings, sha: str | None, image: str | None, timeout: int = 180) -> None:
    """Recreate the compose phēnix service on `image` of `sha`, or stock for None."""
    service = None
    if sha:
        service = {
            "image": image,
            "pull_policy": "never",
            "labels": {LABEL: sha, IMAGE_LABEL: image},
        }
    update.recreate(host, s.ctr, update.burger_override(s, "phenix"), service, timeout)
    if not sha and running_sha(host, s):  # the override came back: its path did not match
        raise BurgerError(
            f"phenix container '{s.ctr}' still runs burger's build after the restore; "
            "recreate it from the project's own compose files by hand"
        )


def _swap() -> _swap_mod.Swap:
    return _swap_mod.Swap(
        "phenix",
        "phēnix",
        "BURGER_PHENIX_CONTAINER",
        "would restart it under them",
        lambda s: s.ctr,
        lambda sha: image_tag(sha, None),
        running_sha,
        running_image,
        _recreate,
    )


# Make the running phēnix the one the board wants: burger's build `image` of commit
# `want`, or stock for None (which also undoes a swap a killed run left behind). A recreated
# phēnix has neither burger's apps nor its minimega.py: install swaps it first.
def ensure(host, s: Settings, want: str | None, image: str | None = None) -> None:
    _swap_mod.ensure(_swap(), host, s, want, image)


# Put stock phēnix back after a board that swapped it, unless VMs still need it.
def restore(host, s: Settings) -> None:
    _swap_mod.restore(_swap(), host, s)


def rows(host, s: Settings, man: dict, board: str) -> list[Row]:
    """verify's proof of which phēnix ran: the container's commit and image."""
    check = "phenix container"
    if not s.ctr:
        return [Row("-", check, "skipped", "BURGER_PHENIX_CONTAINER is empty", [])]
    px = man.get("phenix") or {}
    have, want = running_sha(host, s), px.get("sha")
    reason = ""
    if have != want or (px.get("image") and running_image(host, s) != px["image"]):
        reason = (
            f"the container runs {_short(running_image(host, s))}, want "
            f"{_short(px.get('image') or want)}; burger install {board}"
        )
    return [Row("-", check, "FAIL" if reason else "ok", reason, [])]
