import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from burger.config import MAX_EXP_NAME, BurgerError, Settings
from burger.items import Item, load_item, resolve_item
from burger.loader import load_yaml
from burger.routes import Route, load_route

_FIELDS = {
    "about", "route", "apps", "bennu", "items", "overrides", "runs", "linux_only", "fdlogs",
    "services", "minimega", "phenix", "probes",
}  # fmt: skip
_BENNU_KEYS = {"pr", "prs", "branch", "remote", "install", "pip", "pip_env"}
_APPS_KEYS = {"prs", "branch", "remote", "contains"}
_PINNED_KEYS = {"pr", "branch", "remote"}  # minimega: and phenix:
_FLAG = "pr:N or branch:NAME[@fork|@upstream]"
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
    services: list[str] = field(default_factory=list)  # scripts/<name>.sh start|stop around up/down
    apps_branch: dict | None = None  # {"branch", "remote"}; then apps_prs is only the record
    fdlogs: dict | None = None  # field-device log knobs, see burger.fdlogs.DEFAULTS
    minimega: dict | None = None  # {"pr"} or {"branch", "remote"}: run on this minimega
    minimega_from: str = "board"  # "flag" when --minimega chose it
    phenix: dict | None = None  # like minimega: run on this sceptre-phenix
    phenix_from: str = "board"  # "flag" when --phenix chose it
    probes: list[str] = field(default_factory=list)  # burger.probes run by verify


@dataclass
class Requires:
    debs_packages: list[str]
    debs_nodes: list[str]
    monitor: bool
    attacker: bool
    traffic_gen: bool
    env: list[str]
    mgmt_vlan: str | None
    cc_nodes: list[str]
    keep_nodes: list[str]
    iperf_static: str | None = None  # version of the static iperf3 to inject
    iperf_nodes: list[str] = field(default_factory=list)
    caldera_server: bool = False  # inject a Caldera C2 server VM
    # inject an Ignition gateway VM; True boots ignition.qc2 (route-mapped), a str is the image
    ignition_server: bool | str = False
    goart_nodes: list[str] = field(default_factory=list)  # inject the goart binary here


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


def _one_of(path: Path, key: str, spec: dict) -> None:
    """A source is a PR (or PRs) or a branch, never both."""
    prkeys = {"apps": ["prs"], "minimega": ["pr"], "phenix": ["pr"]}.get(key, ["pr", "prs"])
    if len(given := [k for k in (*prkeys, "branch") if k in spec]) > 1:
        names = " or ".join(f"'{k}'" for k in (*prkeys, "branch"))
        raise BurgerError(f"{path}: {key} takes {names}, not both ({', '.join(given)} given)")


def _branch_source(path: Path, key: str, spec: dict, default_remote: str) -> dict:
    if not isinstance(spec["branch"], str) or not _BRANCH.fullmatch(spec["branch"]):
        raise BurgerError(f"{path}: {key}.branch must be a branch name, not {spec['branch']!r}")
    spec = {"remote": default_remote, **spec}
    if spec["remote"] not in REMOTES:
        raise BurgerError(f"{path}: {key}.remote must be fork or upstream, not {spec['remote']!r}")
    return spec


def _source(path: Path, key: str, spec: dict, allowed: set[str], default_remote: str) -> dict:
    """Check the keys common to apps and bennu; fill in the branch remote."""
    bad = sorted(set(spec) - allowed)
    if bad:
        raise BurgerError(
            f"{path}: unknown {key} field '{bad[0]}' (allowed: {', '.join(sorted(allowed))})"
        )
    _one_of(path, key, spec)
    if "branch" in spec:
        return _branch_source(path, key, spec, default_remote)
    for k in ("remote", "contains"):
        if k in spec:
            raise BurgerError(f"{path}: {key}.{k} needs 'branch'")
    return spec


def _bennu(path: Path, bennu) -> dict | None:
    if bennu is None:
        return None
    if not isinstance(bennu, dict):
        raise BurgerError(f"{path}: bennu must be a mapping, not {bennu!r}")
    bennu = _source(path, "bennu", bennu, _BENNU_KEYS, "upstream")
    if "pr" in bennu:
        if not _is_int(bennu["pr"]):
            raise BurgerError(f"{path}: bennu.pr must be an integer, not {bennu['pr']!r}")
    elif "prs" in bennu:
        bennu["prs"] = _int_list(path, "bennu.prs", bennu["prs"])
        if not bennu["prs"]:
            raise BurgerError(f"{path}: bennu.prs must not be empty")
    elif "branch" not in bennu:
        raise BurgerError(f"{path}: bennu needs 'pr', 'prs' or 'branch'")
    return bennu


def _probes(path: Path, spec) -> list[str]:
    probes = spec or []
    if not (isinstance(probes, list) and all(isinstance(n, str) for n in probes)):
        raise BurgerError(f"{path}: probes must be a list of probe names, not {probes!r}")
    from burger.probes import PROBES  # late: probes import experiment, which imports this

    if bad := [n for n in probes if n not in PROBES]:
        raise BurgerError(f"{path}: unknown probe '{bad[0]}' (known: {', '.join(PROBES)})")
    return probes


def _apps(path: Path, spec: dict) -> tuple[list[int], dict | None]:
    """The apps PRs the board holds, and its branch (then the PRs are `contains`)."""
    apps = _source(path, "apps", spec, _APPS_KEYS, "fork")
    prs = _int_list(path, "apps.prs", apps.get("prs") or [])
    contains = _int_list(path, "apps.contains", apps.get("contains") or [])
    branch = {k: apps[k] for k in ("branch", "remote")} if "branch" in apps else None
    return (contains if branch else prs), branch


def _items(path: Path, items: dict) -> dict[str, bool]:
    for ref, on in items.items():
        if not isinstance(on, bool):
            raise BurgerError(f"{path}: items.{ref} must be on or off, not {on!r}")
    return items


def _load_doc(name: str, settings: Settings) -> tuple[Path, dict, list[str]]:
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
    return path, doc, unset


def load_switchboard(name: str, settings: Settings) -> Switchboard:
    path, doc, unset = _load_doc(name, settings)
    bennu = _bennu(path, doc.get("bennu"))
    pinned = {k: _pinned(path, k, doc.get(k)) for k in ("minimega", "phenix")}
    probes = _probes(path, doc.get("probes"))
    for key in ("items", "apps", "overrides"):
        if not isinstance(doc.get(key) or {}, dict):
            raise BurgerError(f"{path}: {key} must be a mapping, not {doc[key]!r}")
    apps_prs, branch = _apps(path, doc.get("apps") or {})
    return Switchboard(
        name=name,
        about=(doc.get("about") or "").strip(),
        route=doc["route"],
        apps_prs=apps_prs,
        bennu=bennu,
        items=_items(path, doc.get("items") or {}),
        overrides=doc.get("overrides") or {},
        runs=doc.get("runs"),
        linux_only=bool(doc.get("linux_only")),
        exp=f"ebwes-{name}",
        unset=unset,
        services=_services(path, settings, doc.get("services")),
        apps_branch=branch,
        fdlogs=_fdlogs(path, doc.get("fdlogs")),
        **pinned,
        probes=probes,
    )


def _pinned(path: Path, key: str, spec) -> dict | None:
    """A board's `minimega:` or `phenix:` mapping: a PR or a branch of that repo."""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise BurgerError(f"{path}: {key} must be a mapping, not {spec!r}")
    spec = _source(path, key, spec, _PINNED_KEYS, "fork")
    if "pr" in spec:
        if not _is_int(spec["pr"]):
            raise BurgerError(f"{path}: {key}.pr must be an integer, not {spec['pr']!r}")
    elif "branch" not in spec:
        raise BurgerError(f"{path}: {key} needs 'pr' or 'branch'")
    return spec


def parse_flag(key: str, text: str) -> dict:
    """The --minimega or --phenix value as a board's mapping."""
    kind, _, rest = text.partition(":")
    if kind == "pr" and rest.isascii() and rest.isdigit():
        return {"pr": int(rest)}
    branch, _, remote = rest.partition("@")
    if kind == "branch" and _BRANCH.fullmatch(branch) and (remote or "fork") in REMOTES:
        return {"branch": branch, "remote": remote or "fork"}
    raise BurgerError(f"--{key} takes {_FLAG}, not {text!r}")


def pinned_label(spec: dict) -> str:
    return f"#{spec['pr']}" if "pr" in spec else f"{spec['remote']}/{spec['branch']}"


def with_flag(board: Switchboard, key: str, flag: str | None) -> Switchboard:
    """The board with --minimega or --phenix applied; the flag wins over the board's own value."""
    if flag is None:
        return board
    spec = parse_flag(key, flag)
    if old := getattr(board, key):
        was = f"pr:{old['pr']}" if "pr" in old else f"branch:{old['branch']} ({old['remote']})"
        logger.warning("--{} {} overrides switchboard '{}' {} {}", key, flag, board.name, key, was)
    return dataclasses.replace(board, **{key: spec, f"{key}_from": "flag"})


def _services(path: Path, settings: Settings, spec) -> list[str]:
    if spec is None:
        return []
    if not (isinstance(spec, list) and all(isinstance(n, str) for n in spec)):
        raise BurgerError(f"{path}: services must be a list of script names, not {spec!r}")
    for n in spec:
        if not re.fullmatch(r"[\w-]+", n) or not (settings.root / "scripts" / f"{n}.sh").is_file():
            raise BurgerError(f"{path}: services: no script scripts/{n}.sh")
    return spec


def _fdlogs(path, spec) -> dict | None:
    if spec is None:
        return None
    from burger.fdlogs import DEFAULTS, FAIL_ON  # late: fdlogs imports vcs

    if not isinstance(spec, dict) or set(spec) - set(DEFAULTS):
        raise BurgerError(f"{path}: fdlogs must be a mapping with keys {', '.join(DEFAULTS)}")
    if spec.get("fail_on", "none") not in FAIL_ON:
        raise BurgerError(f"{path}: fdlogs.fail_on must be one of {', '.join(FAIL_ON)}")
    return spec


def _unique(seq) -> list:
    return list(dict.fromkeys(seq))


def _name_problems(board: Switchboard, settings: Settings, refs: list[str]) -> list[str]:
    problems = []
    if len(board.exp) > MAX_EXP_NAME:
        problems.append(
            f"experiment name '{board.exp}' is {len(board.exp)} characters, "
            f"over the {MAX_EXP_NAME} limit"
        )
    for key in board.overrides:
        if key not in refs:
            problems.append(
                f"{_dir(settings) / f'{board.name}.yaml'}: overrides.{key} names an item "
                "that is not enabled in items"
            )
    return problems


def _item_problems(board: Switchboard, route: Route, ref: str, item: Item) -> list[str]:
    problems = []
    for need in item.requires.get("apps_prs", []):
        if need not in board.apps_prs:
            where = "contains" if board.apps_branch else "prs"
            problems.append(f"item '{ref}' needs apps PR #{need}, not in the board's apps {where}")
    if item.routes and route.name not in item.routes:
        problems.append(f"item '{ref}' supports {', '.join(item.routes)}, not route '{route.name}'")
    return problems


def _load_one(
    board: Switchboard, settings: Settings, route: Route, linux_only: bool, ref: str
) -> Item | None:
    """The item resolved for the route, or None when --linux-only skips it."""
    raw = load_item(ref, settings)
    if linux_only and raw.requires.get("windows"):
        return None
    return resolve_item(raw, route, board.overrides.get(ref))


def _resolve_items(
    board: Switchboard, settings: Settings, route: Route, linux_only: bool, refs: list[str]
) -> tuple[list[Item], list[Item], list[str], list[str]]:
    """(runnable items, validate-only items, unset env vars, problems) for the enabled refs."""
    items: list[Item] = []
    validate_only: list[Item] = []
    unset: list[str] = []
    problems: list[str] = []
    i = 0
    while i < len(refs):  # refs grows as required apps are inserted after their requirer
        ref = refs[i]
        i += 1
        try:
            item = _load_one(board, settings, route, linux_only, ref)
        except BurgerError as e:
            problems.append(str(e))
            continue
        if item is None:
            continue
        unset += [*item.unset, *(e for e in item.requires.get("env", []) if e not in os.environ)]
        for app in item.requires.get("apps", []):
            if app not in refs:
                refs.insert(i, app)
        problems += _item_problems(board, route, ref, item)
        if not item.routes:
            validate_only.append(item)
        elif route.name in item.routes:
            items.append(item)
    return items, validate_only, unset, problems


def _requires(items: list[Item]) -> Requires:
    req = [i.requires for i in items]
    debs = [r.get("debs", {}) for r in req]
    return Requires(
        debs_packages=_unique(p for d in debs for p in d.get("packages", [])),
        debs_nodes=_unique(n for d in debs for n in d.get("nodes", [])),
        monitor=any(r.get("monitor") for r in req),
        attacker=any(r.get("attacker") for r in req),
        traffic_gen=any(r.get("traffic_gen") for r in req),
        env=_unique(e for r in req for e in r.get("env", [])),
        mgmt_vlan=next((r["mgmt_vlan"] for r in req if r.get("mgmt_vlan")), None),
        cc_nodes=_unique(n for r in req for n in r.get("cc_nodes", [])),
        keep_nodes=_unique(n for r in req for n in r.get("keep_nodes", [])),
        iperf_static=next((s["version"] for r in req if (s := r.get("iperf_static"))), None),
        iperf_nodes=_unique(n for r in req for n in r.get("iperf_static", {}).get("nodes", [])),
        caldera_server=any(r.get("caldera_server") for r in req),
        ignition_server=next((v for r in req if (v := r.get("ignition_server"))), False),
        goart_nodes=_unique(n for r in req for n in r.get("goart", {}).get("nodes", [])),
    )


def resolve(
    board: Switchboard, settings: Settings, linux_only: bool = False, route_name: str | None = None
) -> Resolved:
    """Check everything that can fail before a build, and raise it all in one BurgerError."""
    linux_only = linux_only or board.linux_only
    route = load_route(route_name or board.route, settings)
    refs = [ref for ref, on in board.items.items() if on]
    problems = _name_problems(board, settings, refs)
    items, validate_only, unset, more = _resolve_items(board, settings, route, linux_only, refs)
    unset = [*board.unset, *route.unset, *unset]
    problems += more
    if unset:
        problems.append(f"unset environment variables: {', '.join(_unique(unset))}")
    if problems:
        raise BurgerError(
            "\n".join([f"switchboard '{board.name}':", *(f"  {p}" for p in problems)])
        )
    return Resolved(board, route, items, validate_only, _requires(items), linux_only)
