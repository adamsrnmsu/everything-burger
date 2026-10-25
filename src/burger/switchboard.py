import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from burger.config import MAX_EXP_NAME, BurgerError, Settings
from burger.items import Item, load_item, resolve_item
from burger.loader import load_yaml
from burger.routes import Route, load_route

_FIELDS = {"about", "route", "apps", "bennu", "items", "overrides", "runs", "linux_only"}
_BENNU_KEYS = {"pr", "branch", "remote", "install", "pip"}
_APPS_KEYS = {"prs", "branch", "remote", "contains"}
REMOTES = ("fork", "upstream")
# A conservative subset of git ref names: no spaces, `:`, `..`, `//`, a leading `-` or `.`,
# a trailing `/`, `.` or `.lock`.
_BRANCH = re.compile(r"(?!.*(\.\.|//|\.lock(/|$)|/\.|\.$|/$))\w[\w./-]*")


@dataclass
class Switchboard:
    name: str
    about: str
    route: str
    apps_prs: list[
        int
    ]  # PRs the apps tree holds: merged from `prs`, or `contains` on a branch board
    bennu: dict | None
    items: dict[str, bool]
    overrides: dict[str, dict]
    runs: list[dict] | None
    linux_only: bool
    exp: str
    unset: list[str] = field(default_factory=list)
    apps_branch: dict | None = None  # {"branch", "remote"}; then apps_prs is only the record


@dataclass
class Requires:
    debs_packages: list[str]
    debs_nodes: list[str]
    monitor: bool
    traffic_gen: bool
    env: list[str]
    mgmt_vlan: str | None
    cc_nodes: list[str]
    keep_nodes: list[str]
    iperf_static: str | None = None  # version of the static iperf3 to inject
    iperf_nodes: list[str] = field(default_factory=list)


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


def _is_int(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def _int_list(path: Path, key: str, val) -> list[int]:
    if not (isinstance(val, list) and all(_is_int(n) for n in val)):
        raise BurgerError(f"{path}: {key} must be a list of integers, not {val!r}")
    return val


def _source(path: Path, key: str, spec: dict, allowed: set[str], default_remote: str) -> dict:
    """Check the keys common to apps and bennu; fill in the branch remote."""
    bad = sorted(set(spec) - allowed)
    if bad:
        raise BurgerError(
            f"{path}: unknown {key} field '{bad[0]}' (allowed: {', '.join(sorted(allowed))})"
        )
    pr = "prs" if key == "apps" else "pr"
    if pr in spec and "branch" in spec:
        raise BurgerError(f"{path}: {key} takes '{pr}' or 'branch', not both")
    if "branch" not in spec:
        for k in ("remote", "contains"):
            if k in spec:
                raise BurgerError(f"{path}: {key}.{k} needs 'branch'")
        return spec
    if not isinstance(spec["branch"], str) or not _BRANCH.fullmatch(spec["branch"]):
        raise BurgerError(f"{path}: {key}.branch must be a branch name, not {spec['branch']!r}")
    spec = {"remote": default_remote, **spec}
    if spec["remote"] not in REMOTES:
        raise BurgerError(f"{path}: {key}.remote must be fork or upstream, not {spec['remote']!r}")
    return spec


def load_switchboard(name: str, settings: Settings) -> Switchboard:
    path = _dir(settings) / f"{name}.yaml"
    if not path.is_file():
        raise BurgerError(
            f"no switchboard '{name}' (known: {', '.join(all_switchboards(settings))})"
        )
    doc, unset = load_yaml(path, _FIELDS)
    if "route" not in doc:
        raise BurgerError(f"{path}: missing field 'route'")
    try:
        load_route(doc["route"], settings)
    except BurgerError as e:
        raise BurgerError(f"{path}: route: {e}") from None
    bennu = doc.get("bennu")
    if bennu is not None:
        if not isinstance(bennu, dict):
            raise BurgerError(f"{path}: bennu must be a mapping, not {bennu!r}")
        bennu = _source(path, "bennu", bennu, _BENNU_KEYS, "upstream")
        if "pr" in bennu:
            if not _is_int(bennu["pr"]):
                raise BurgerError(f"{path}: bennu.pr must be an integer, not {bennu['pr']!r}")
        elif "branch" not in bennu:
            raise BurgerError(f"{path}: bennu needs 'pr' or 'branch'")
    for key in ("items", "apps", "overrides"):
        if not isinstance(doc.get(key) or {}, dict):
            raise BurgerError(f"{path}: {key} must be a mapping, not {doc[key]!r}")
    apps = _source(path, "apps", doc.get("apps") or {}, _APPS_KEYS, "fork")
    prs = _int_list(path, "apps.prs", apps.get("prs") or [])
    contains = _int_list(path, "apps.contains", apps.get("contains") or [])
    branch = {k: apps[k] for k in ("branch", "remote")} if "branch" in apps else None
    items = doc.get("items") or {}
    for ref, on in items.items():
        if not isinstance(on, bool):
            raise BurgerError(f"{path}: items.{ref} must be on or off, not {on!r}")
    return Switchboard(
        name=name,
        about=(doc.get("about") or "").strip(),
        route=doc["route"],
        apps_prs=contains if branch else prs,
        bennu=bennu,
        items=items,
        overrides=doc.get("overrides") or {},
        runs=doc.get("runs"),
        linux_only=bool(doc.get("linux_only")),
        exp=f"ebwes-{name}",
        unset=unset,
        apps_branch=branch,
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
    for key in board.overrides:
        if key not in refs:
            problems.append(
                f"{_dir(settings) / f'{board.name}.yaml'}: overrides.{key} names an item "
                "that is not enabled in items"
            )
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
                where = "contains" if board.apps_branch else "prs"
                problems.append(
                    f"item '{ref}' needs apps PR #{need}, not in the board's apps {where}"
                )
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
        raise BurgerError(
            "\n".join([f"switchboard '{board.name}':", *(f"  {p}" for p in problems)])
        )
    req = [i.requires for i in items]
    debs = [r.get("debs", {}) for r in req]
    requires = Requires(
        debs_packages=_unique(p for d in debs for p in d.get("packages", [])),
        debs_nodes=_unique(n for d in debs for n in d.get("nodes", [])),
        monitor=any(r.get("monitor") for r in req),
        traffic_gen=any(r.get("traffic_gen") for r in req),
        env=_unique(e for r in req for e in r.get("env", [])),
        mgmt_vlan=next((r["mgmt_vlan"] for r in req if r.get("mgmt_vlan")), None),
        cc_nodes=_unique(n for r in req for n in r.get("cc_nodes", [])),
        keep_nodes=_unique(n for r in req for n in r.get("keep_nodes", [])),
        iperf_static=next((s["version"] for r in req if (s := r.get("iperf_static"))), None),
        iperf_nodes=_unique(n for r in req for n in r.get("iperf_static", {}).get("nodes", [])),
    )
    return Resolved(board, route, items, validate_only, requires, linux_only)
