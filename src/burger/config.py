import os
from dataclasses import dataclass
from pathlib import Path


class BurgerError(Exception):
    """A failure the CLI reports and turns into exit code 1."""


REPOS = {
    "apps": "https://github.com/sandialabs/sceptre-phenix-apps.git",
    "bennu": "https://github.com/sandialabs/sceptre-bennu.git",
    "topologies": "https://github.com/sandialabs/sceptre-phenix-topologies.git",
}
# Components whose start polls until phēnix stops it.
# ponytail: hand-kept list; add a type here when its PR says "background".
POLLERS = {"hoststats"}
MAX_EXP_NAME = 15  # phēnix bridge-name limit (api/experiment/experiment.go)
# git -c options for every burger git command. burger creates and deletes worktrees on every
# sync, under a directory macOS keeps touching:
# - trustctime=false: macOS adds the com.apple.provenance xattr to new files about two seconds
#   after they are written, which bumps their ctime. The index then holds stale ctimes and
#   the `git stash create` that `git merge` runs first dies with a bare "fatal: stash failed"
#   if it lands in that window: the intermittent sync failure at a random PR (burger-15k).
# - fsmonitor/untrackedCache off: core.fsmonitor=true in a user's gitconfig starts a
#   daemon in every worktree burger makes, and they are never stopped.
# - gc.auto/maintenance.auto off: no detached gc after fetch or merge racing the next command.
GIT_SAFE = [
    "-c",
    "core.trustctime=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
]
GIT_ID = ["-c", "user.name=burger", "-c", "user.email=burger@localhost"]


def _checkout() -> Path:
    """The checkout an editable install points at, else the current directory."""
    here = Path(__file__).resolve().parents[2]
    return here if (here / "switchboards").is_dir() else Path.cwd()


@dataclass(frozen=True)
class Settings:
    root: Path
    work: Path
    ctr: str  # "" = phenix installed natively
    mm_ctr: str  # miniccc comes from here, and router fixes go through it; "" disables both
    api: str
    token: str
    phenix_dir: str

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        root = (root or _checkout()).resolve()
        env = os.environ.get
        return cls(
            root=root,
            work=Path(env("BURGER_WORK", root / "work")).resolve(),
            ctr=env("BURGER_PHENIX_CONTAINER", "phenix"),
            mm_ctr=env("BURGER_MINIMEGA_CONTAINER", "minimega"),
            api=env("BURGER_API", "http://localhost:3000/api/v1"),
            token=env("BURGER_TOKEN", ""),
            phenix_dir=env("BURGER_PHENIX_DIR", "/phenix"),
        )
