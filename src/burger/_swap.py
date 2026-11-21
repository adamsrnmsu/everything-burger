"""The one flow that swaps a compose container (minimega or phēnix) to burger's build and back."""

from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from burger import update  # update imports experiment, which imports minimega/phenix: call time
from burger.config import BurgerError, Settings


@dataclass(frozen=True)
class Swap:
    """What differs between the two swapped containers: names, messages and the few hooks."""

    name: str  # "minimega" / "phenix": log and warning text
    label: str  # "minimega" / "phēnix": the error naming the container
    env: str  # the setting that names the container
    effect: str  # what changing it does to a running experiment
    ctr: Callable[[Settings], str]
    default_image: Callable[[str], str]
    running_sha: Callable
    running_image: Callable
    recreate: Callable  # (host, s, sha, image)
    on_match: Callable | None = None  # after a no-op ensure that wants burger's build


def _short(image: str | None) -> str:
    return image.split(":", 1)[1] if image else "stock"


def ensure(w: Swap, host, s: Settings, want: str | None, image: str | None) -> None:
    ctr = w.ctr(s)
    if not ctr:
        if want:
            raise BurgerError(
                f"this switchboard runs on its own {w.label}, which burger swaps into the "
                f"{w.label} container: set {w.env} to its name"
            )
        return
    image = (image or w.default_image(want)) if want else None
    have = w.running_image(host, s)
    if have == image:
        if want and w.on_match:
            w.on_match(host, s)
        return
    running = update.running_experiments(host)
    if running:
        raise BurgerError(
            f"experiments running: {', '.join(running)}; changing {w.name} "
            f"({_short(have)} -> {_short(image)}) {w.effect}, "
            "so bring them down first (burger down <board>)"
        )
    w.recreate(host, s, want, image)
    logger.info("{} container '{}' now runs {}", w.name, ctr, image or "stock")


def restore(w: Swap, host, s: Settings) -> None:
    if w.running_sha(host, s) is None:
        return
    running = update.running_experiments(host)
    if running:
        logger.warning(
            "{} stays on {} while experiments run ({}); burger down the last of them "
            "restores stock",
            w.name,
            w.running_image(host, s),
            ", ".join(running),
        )
        return
    w.recreate(host, s, None, None)
    logger.info("{} container '{}' is back on stock", w.name, w.ctr(s))
