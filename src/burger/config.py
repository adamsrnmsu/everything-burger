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
GIT_ID = ["-c", "user.name=burger", "-c", "user.email=burger@localhost"]


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
        root = (root or Path.cwd()).resolve()
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
