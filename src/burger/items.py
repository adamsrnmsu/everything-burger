import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path

from burger.config import POLLERS, BurgerError, Settings
from burger.loader import load_yaml
from burger.routes import Route

ITEM_FIELDS = {
    *"name kind type about routes stages holds background advisory config hosts requires".split(),
    *"verify waterway soap site".split(),
}
KINDS = ("scorch", "app", "bennu")
STAGES = ("configure", "start", "stop", "cleanup")
_ROUTE_BLOCKS = ("waterway", "soap", "site")
_TOKEN = re.compile(r"\{route\.(\w+)(?:\[(\d+)\])?\}")
# Each verify entry is one check type -> the spec keys it needs (checks.CHECKS + vm).
CHECK_KEYS = {
    "vm": ("host", "run"),
    "process": ("host", "name"),  # shorthands for vm, expanded by resolve_item
    "port": ("host", "port"),
    "files": ("glob",),
    "pcap": ("glob",),
    "csv": ("glob",),
    "log": ("glob",),
    "json": ("glob", "field"),
    "python": ("function",),
}
# Keys a check accepts besides CHECK_KEYS; any check also takes `windows`.
OPTIONAL_KEYS = {
    "vm": ("expect",),
    "process": ("expect",),
    "port": ("state", "expect"),
    "files": ("min", "min_bytes"),
    "pcap": ("tcp_port", "udp_port", "ip_proto", "min_packets"),
    "csv": ("min_rows", "columns"),
    "log": ("contains", "lacks"),
    "json": ("equals", "matches", "exists"),
}
_NAME = re.compile(r"[A-Za-z0-9._-]+")
_SS = {"listening": "-Hltn", "established": "-Htn state established"}
REQUIRES = set("debs apps apps_prs env monitor mgmt_vlan cc_nodes keep_nodes windows".split())


@dataclass
class Item:
    name: str
    kind: str
    type: str
    about: str
    routes: list[str]
    stages: list[str]
    holds: bool
    background: bool
    advisory: bool
    config: dict
    hosts: list
    requires: dict
    verify: list
    path: Path
    unset: list[str] = field(default_factory=list)  # env vars the file uses that are unset
    overrides: dict = field(default_factory=dict)  # per-route blocks: waterway, soap, site


def _check_fields(path: Path, doc: dict, where: str = "") -> None:
    """Raise on a verify entry or requires key no code would read."""
    for n, entry in enumerate(doc.get("verify") or []):
        field = f"{where}verify[{n}]"
        if not isinstance(entry, dict) or len(entry) != 1:
            raise BurgerError(f"{path}: {field} must be one check, like `- files: {{glob: ...}}`")
        ((name, spec),) = entry.items()
        if name == "status":
            raise BurgerError(
                f"{path}: {field}: 'status' is checked for every component; delete it"
            )
        if name not in CHECK_KEYS:
            raise BurgerError(
                f"{path}: {field}: unknown check '{name}' (known: {', '.join(CHECK_KEYS)})"
            )
        need = CHECK_KEYS[name]
        if not need or (name == "python" and isinstance(spec, str)):
            continue
        lacking = [k for k in need if not isinstance(spec, dict) or k not in spec]
        if lacking:
            raise BurgerError(f"{path}: {field}.{name} needs '{lacking[0]}'")
        known = {*need, *OPTIONAL_KEYS.get(name, ()), "windows"}
        if unknown := sorted(set(spec) - known):
            raise BurgerError(
                f"{path}: {field}.{name}: unknown key '{unknown[0]}' "
                f"(known: {', '.join(sorted(known))})"
            )
    requires = doc.get("requires") or {}
    bad = sorted(requires if not isinstance(requires, dict) else set(requires) - REQUIRES)
    if bad:
        raise BurgerError(
            f"{path}: {where}requires.{bad[0]} is not a requirement "
            f"(allowed: {', '.join(sorted(REQUIRES))})"
        )


def load_item(ref: str, settings: Settings) -> Item:
    path = settings.root / (ref if "/" in ref else f"components/{ref}")
    path = path.with_suffix(".yaml")
    if not path.is_file():
        raise BurgerError(f"no item '{ref}' (looked for {path})")
    doc, unset = load_yaml(path, ITEM_FIELDS)
    for key in ("name", "kind", "type"):
        if key not in doc:
            raise BurgerError(f"{path}: missing field '{key}'")
    if doc["kind"] not in KINDS:
        raise BurgerError(f"{path}: kind '{doc['kind']}' must be one of {', '.join(KINDS)}")
    stages = doc.get("stages") or []
    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise BurgerError(f"{path}: unknown stage '{bad[0]}' (allowed: {', '.join(STAGES)})")
    _check_fields(path, doc)
    for r in _ROUTE_BLOCKS:
        if isinstance(doc.get(r), dict):
            _check_fields(path, doc[r], f"{r}.")
    return Item(
        name=doc["name"],
        kind=doc["kind"],
        type=doc["type"],
        about=(doc.get("about") or "").strip(),
        routes=doc.get("routes") or [],
        stages=stages,
        holds=bool(doc.get("holds")),
        background=bool(doc.get("background")),
        advisory=bool(doc.get("advisory")),
        config=doc.get("config") or {},
        hosts=doc.get("hosts") or [],
        requires=doc.get("requires") or {},
        verify=doc.get("verify") or [],
        path=path,
        unset=unset,
        overrides={r: doc[r] for r in _ROUTE_BLOCKS if doc.get(r)},
    )


def all_items(settings: Settings) -> list[Item]:
    refs = [p.stem for p in sorted((settings.root / "components").glob("*.yaml"))]
    for d in ("apps", "bennu"):
        refs += [f"{d}/{p.stem}" for p in sorted((settings.root / d).glob("*.yaml"))]
    return [load_item(r, settings) for r in refs]


def _merge(base, over):
    """Dicts merge key by key; anything else, lists included, is replaced."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base else deepcopy(v)
        return out
    return deepcopy(over)


def _subst(node, route: Route):
    if isinstance(node, dict):
        return {k: _subst(v, route) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst(v, route) for v in node]
    if not isinstance(node, str):
        return node

    def value(m):
        if m[1] not in route.values:
            raise BurgerError(f"route '{route.name}' has no value '{m[1]}' (used as {m[0]})")
        v = route.values[m[1]]
        if m[2] is None:
            return v
        try:
            return v[int(m[2])]
        except (IndexError, TypeError):
            raise BurgerError(f"route '{route.name}': {m[0]} is out of range") from None

    whole = _TOKEN.fullmatch(node)
    if whole:
        return value(whole)  # a lone token keeps the value's type
    return _TOKEN.sub(lambda m: str(value(m)), node)


def _vm_runs(item: Item):
    for check in item.verify:
        if isinstance(check, dict) and isinstance(check.get("vm"), dict):
            yield str(check["vm"].get("run", ""))


def _expand(entry: dict, path: Path) -> dict:
    """process and port become the vm check they stand for; other checks pass through."""
    ((name, spec),) = entry.items()
    if name not in ("process", "port"):
        return entry
    if name == "process":
        if not _NAME.fullmatch(str(spec["name"])):
            raise BurgerError(f"{path}: process name {spec['name']!r} must match [A-Za-z0-9._-]+")
        run = f"pgrep -f '(^|/){spec['name']}( |$)'"
    else:
        state = spec.get("state", "listening")
        if state not in _SS:
            raise BurgerError(f"{path}: port state {state!r} must be {' or '.join(_SS)}")
        if not re.fullmatch(r"\d+", str(spec["port"])):
            raise BurgerError(f"{path}: port {spec['port']!r} must be an integer")
        run = f"ss {_SS[state]} '( sport = :{spec['port']} )' | grep -q ."
    rest = {k: v for k, v in spec.items() if k not in ("host", "name", "port", "state")}
    return {"vm": {"host": spec["host"], "run": run, **rest}}


def resolve_item(item: Item, route: Route, override: dict | None) -> Item:
    d = asdict(item)
    overrides = d.pop("overrides")
    d = _merge(d, overrides.get(route.name, {}))
    d = _subst(_merge(d, override or {}), route)
    out = Item(**d, overrides=overrides)
    _check_fields(out.path, d)  # the switchboard's override may bring its own verify
    out.verify = [_expand(e, out.path) for e in out.verify]
    for run in _vm_runs(out):
        if "${" in run:
            raise BurgerError(f"{out.path}: vm check must not contain '${{': {run}")
    if out.background and "stop" not in out.stages:
        raise BurgerError(f"{out.path}: background item '{out.name}' needs a stop stage")
    if out.background and out.type not in POLLERS:
        raise BurgerError(
            f"{out.path}: background item '{out.name}' has type '{out.type}', "
            f"which is not a poller ({', '.join(sorted(POLLERS))})"
        )
    return out
