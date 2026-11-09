from dataclasses import dataclass, field
from pathlib import Path

from burger.config import BurgerError, Settings
from burger.loader import load_yaml

ROUTES_DIR = Path(__file__).resolve().parents[2] / "routes"
_LISTS = ("bennu_nodes", "drop_nodes", "windows_images", "images")
_FIELDS = {
    "topology_dir",
    "files",
    "base",
    "rtus",
    "provider",
    "ics_port",
    "ics_iface",
    "image_map",
    "linux_poller",
    *_LISTS,
}


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
    images: list[str] = field(default_factory=list)  # empty: bennu.qc2, minirouter.qc2
    # image_map renames the images of nodes burger injects (kali.qc2 -> kali-arm64.qc2); a
    # route with one is an arm64 range, so those nodes also get the arm64 hardware block.
    image_map: dict[str, str] = field(default_factory=dict)
    unset: list[str] = field(default_factory=list)
    # The Modbus poller (OPC) is a Linux VM, so checks marked `windows: true` on its traffic
    # still run under --linux-only.
    linux_poller: bool = False

    def skips_windows(self, linux_only: bool) -> bool:
        return linux_only and not self.linux_poller

    @property
    def arm64(self) -> bool:
        return bool(self.image_map)


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
        values={k: doc[k] for k in ("rtus", "provider", "ics_port", "ics_iface") if k in doc},
        image_map=doc.get("image_map") or {},
        linux_poller=bool(doc.get("linux_poller")),
        unset=unset,
        **{k: doc.get(k) or [] for k in _LISTS},
    )
