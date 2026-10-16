from dataclasses import dataclass, field
from pathlib import Path

from burger.config import BurgerError, Settings
from burger.loader import load_yaml

ROUTES_DIR = Path(__file__).resolve().parents[2] / "routes"
_LISTS = ("bennu_nodes", "drop_nodes", "windows_images")
_FIELDS = {"topology_dir", "files", "base", "rtus", "provider", "ics_port", *_LISTS}


@dataclass
class Route:
    name: str
    topology_dir: str | None
    files: tuple[str, str] | None
    base: dict | None
    values: dict  # rtus, provider, ics_port: what items refer to as {route.<key>}
    bennu_nodes: list[str] = field(default_factory=list)
    drop_nodes: list[str] = field(default_factory=list)
    windows_images: list[str] = field(default_factory=list)
    unset: list[str] = field(default_factory=list)


def load_route(name: str, settings: Settings) -> Route:
    path = ROUTES_DIR / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in ROUTES_DIR.glob("*.yaml")))
        raise BurgerError(f"no route '{name}' (known: {known})")
    doc, unset = load_yaml(path, _FIELDS)
    files = doc.get("files")
    return Route(
        name=name,
        topology_dir=doc.get("topology_dir"),
        files=tuple(files) if files else None,
        base=doc.get("base"),
        values={k: doc[k] for k in ("rtus", "provider", "ics_port") if k in doc},
        unset=unset,
        **{k: doc.get(k) or [] for k in _LISTS},
    )
