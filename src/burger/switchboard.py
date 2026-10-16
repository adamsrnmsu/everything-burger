import os
from dataclasses import dataclass, field
from pathlib import Path

from burger.config import MAX_EXP_NAME, BurgerError, Settings
from burger.items import Item, load_item, resolve_item
from burger.loader import load_yaml
from burger.routes import Route, load_route

_FIELDS = {"about", "route", "apps", "bennu", "items", "overrides", "runs", "linux_only"}
_BENNU_KEYS = {"pr", "install", "pip"}


@dataclass
class Switchboard:
    name: str
    about: str
    route: str
    apps_prs: list[int]
    bennu: dict | None
    items: dict[str, bool]
    overrides: dict[str, dict]
    runs: list[dict] | None
    linux_only: bool
    exp: str
    unset: list[str] = field(default_factory=list)


@dataclass
class Requires:
    debs_packages: list[str]
    debs_nodes: list[str]
    monitor: bool
    env: list[str]
    mgmt_vlan: str | None
    cc_nodes: list[str]
    keep_nodes: list[str]


@dataclass
class Resolved:
    board: Switchboard
    route: Route
    items: list[Item]
    validate_only: list[Item]
    requires: Requires
    linux_only: bool


def _dir(settings: Settings) -> Path:
    return settings.root / "switchboards"


def all_switchboards(settings: Settings) -> list[str]:
    return sorted(p.stem for p in _dir(settings).glob("*.yaml"))


def load_switchboard(name: str, settings: Settings) -> Switchboard:
    path = _dir(settings) / f"{name}.yaml"
    if not path.is_file():
        raise BurgerError(f"no switchboard '{name}' (known: {', '.join(all_switchboards(settings))})")
    doc, unset = load_yaml(path, _FIELDS)
    if "route" not in doc:
        raise BurgerError(f"{path}: missing field 'route'")
    try:
        load_route(doc["route"], settings)
    except BurgerError as e:
        raise BurgerError(f"{path}: route: {e}") from None
    bennu = doc.get("bennu")
    if bennu is not None:
        bad = sorted(set(bennu) - _BENNU_KEYS)
        if bad:
            raise BurgerError(
                f"{path}: unknown bennu field '{bad[0]}' (allowed: {', '.join(sorted(_BENNU_KEYS))})"
            )
        if "pr" not in bennu:
            raise BurgerError(f"{path}: bennu needs 'pr'")
    items = doc.get("items") or {}
    for ref, on in items.items():
        if not isinstance(on, bool):
            raise BurgerError(f"{path}: items.{ref} must be on or off, not {on!r}")
    return Switchboard(
        name=name,
        about=(doc.get("about") or "").strip(),
        route=doc["route"],
        apps_prs=list((doc.get("apps") or {}).get("prs") or []),
        bennu=bennu,
        items=items,
        overrides=doc.get("overrides") or {},
        runs=doc.get("runs"),
        linux_only=bool(doc.get("linux_only")),
        exp=f"ebwes-{name}",
        unset=unset,
    )


def _unique(seq) -> list:
    return list(dict.fromkeys(seq))


def resolve(board: Switchboard, settings: Settings, linux_only: bool = False) -> Resolved:
    """Check everything that can fail before a build, and raise it all in one BurgerError."""
    linux_only = linux_only or board.linux_only
    route = load_route(board.route, settings)
    problems: list[str] = []
    unset = [*board.unset, *route.unset]
    items: list[Item] = []
    validate_only: list[Item] = []
    if len(board.exp) > MAX_EXP_NAME:
        problems.append(
            f"experiment name '{board.exp}' is {len(board.exp)} characters, "
            f"over the {MAX_EXP_NAME} limit"
        )
    refs = [ref for ref, on in board.items.items() if on]
    i = 0
    while i < len(refs):  # refs grows as required apps are inserted after their requirer
        ref = refs[i]
        i += 1
        try:
            raw = load_item(ref, settings)
            if linux_only and raw.requires.get("windows"):
                continue
            item = resolve_item(raw, route, board.overrides.get(ref))
        except BurgerError as e:
            problems.append(str(e))
            continue
        unset += [*item.unset, *(e for e in item.requires.get("env", []) if e not in os.environ)]
        for need in item.requires.get("apps_prs", []):
            if need not in board.apps_prs:
                problems.append(f"item '{ref}' needs apps PR #{need}, not in the board's apps prs")
        for app in item.requires.get("apps", []):
            if app not in refs:
                refs.insert(i, app)
        if not item.routes:
            validate_only.append(item)
        elif route.name not in item.routes:
            problems.append(
                f"item '{ref}' supports {', '.join(item.routes)}, not route '{route.name}'"
            )
        else:
            items.append(item)
    if unset:
        problems.append(f"unset environment variables: {', '.join(_unique(unset))}")
    if problems:
        raise BurgerError("\n".join([f"switchboard '{board.name}':", *(f"  {p}" for p in problems)]))
    req = [i.requires for i in items]
    debs = [r.get("debs", {}) for r in req]
    requires = Requires(
        debs_packages=_unique(p for d in debs for p in d.get("packages", [])),
        debs_nodes=_unique(n for d in debs for n in d.get("nodes", [])),
        monitor=any(r.get("monitor") for r in req),
        env=_unique(e for r in req for e in r.get("env", [])),
        mgmt_vlan=next((r["mgmt_vlan"] for r in req if r.get("mgmt_vlan")), None),
        cc_nodes=_unique(n for r in req for n in r.get("cc_nodes", [])),
        keep_nodes=_unique(n for r in req for n in r.get("keep_nodes", [])),
    )
    return Resolved(board, route, items, validate_only, requires, linux_only)
