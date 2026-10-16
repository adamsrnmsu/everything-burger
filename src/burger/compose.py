import copy
import shlex

from burger.config import POLLERS, BurgerError
from burger.items import STAGES, Item
from burger.switchboard import Resolved


def _check(args: str, validator: str | None = None) -> dict:
    c = {"type": "exec", "args": args, "wait": True}
    if validator:
        c["validator"] = validator
    return c


def _install_checks(man: dict) -> list[dict]:
    b = man.get("bennu") or {}
    # pybennu keeps its version across PRs, so pip's log is the evidence.
    wants = ([("Setting up bennu", "dpkg -i")] if b.get("deb_version") else []) + (
        [("Successfully installed.* pybennu-", "pip install")] if b.get("wheel") else []
    )
    return [
        _check(
            "cat /var/log/burger-bennu.log 2>&1",
            f'log=$(cat); grep -Eq {shlex.quote(want)} <<<"$log" || '
            f'{{ echo "$1: {what} of the PR build failed: $(tail -3 <<<"$log")" >&2; exit 1; }}',
        )
        for want, what in wants
    ]


def _version_check(deb: str) -> dict:
    # No ${...} in any cc string: SCORCH templating eats it. And a command with
    # a validator must print something: cc.py crashes on empty stdout.
    return _check(
        "dpkg-query -W bennu 2>/dev/null | cut -f2 | grep . || echo none",
        f'v=$(cat); [ "$v" = {shlex.quote(deb)} ] || '
        f'{{ echo "$1 runs bennu $v, want {deb}" >&2; exit 1; }}',
    )


def health_checks(
    res: Resolved, man: dict, only: Item | None = None
) -> list[tuple[str, str, dict]]:
    """(item, host, cc check) in the order a health component runs them.

    Burger's own install and version checks come first, under the item name 'bennu'.
    Advisory items are left out (they get their own `only=` component in an extras run).
    """
    out: list[tuple[str, str, dict]] = []
    install = [] if only else _install_checks(man)
    if install:
        deb = man["bennu"].get("deb_version")
        for host in res.route.bennu_nodes:
            out += [("bennu", host, c) for c in [*install, *([_version_check(deb)] if deb else [])]]
    for item in [only] if only else [i for i in res.items if not i.advisory]:
        for v in item.verify:
            vm = v.get("vm") if isinstance(v, dict) else None
            if not vm:
                continue
            run = str(vm.get("run", ""))
            if vm.get("expect"):
                run += f" | grep -Eq {shlex.quote(str(vm['expect']))}"
            hosts = vm["host"] if isinstance(vm["host"], list) else [vm["host"]]
            out += [(item.name, host, _check(run)) for host in hosts]
    return out


def health_component(res: Resolved, man: dict, only: Item | None = None) -> dict | None:
    """A SCORCH cc component running health_checks, grouped per host."""
    checks: dict[str, list[dict]] = {}
    for _, host, c in health_checks(res, man, only):
        checks.setdefault(host, []).append(c)
    if not checks:
        return None
    return {
        "name": f"health-{only.name}" if only else "health",
        "type": "cc",
        "metadata": {"vms": [{"hostname": h, "start": c} for h, c in checks.items()]},
    }


def _run(name: str, items: list[Item]) -> dict:
    run: dict = {"name": name}
    for stage in STAGES:
        names = [i for i in items if stage in i.stages]
        if stage == "start":
            names.sort(key=lambda i: i.holds)  # stable: holds items last
        if names:
            run[stage] = [i.name for i in names]
    return run


def compose(res: Resolved, man: dict) -> tuple[list[dict], list[dict]]:
    scorch = [i for i in res.items if i.kind == "scorch"]
    components = []
    for i in scorch:
        c = {"name": i.name, "type": i.type, "metadata": copy.deepcopy(i.config)}
        if i.background:
            c["background"] = True
        components.append(c)
    if res.board.runs is not None:
        runs = copy.deepcopy(res.board.runs)
    else:
        main = _run(res.board.name, [i for i in scorch if not i.advisory])
        runs = [main] if len(main) > 1 else []
        runs += [_run(f"extras-{i.name}", [i]) for i in scorch if i.advisory]
        for i in res.items:
            extra = health_component(res, man, only=i) if i.advisory else None
            if not extra:
                continue
            components.append(extra)
            run = next((r for r in runs if r["name"] == f"extras-{i.name}"), None)
            if run:
                run["start"] = [*run.get("start", []), extra["name"]]
            else:
                runs.append({"name": f"extras-{i.name}", "start": [extra["name"]]})
    health = health_component(res, man)
    if health:
        components.insert(0, health)
        runs.insert(0, {"name": "health", "start": ["health"]})
    known = {c["name"] for c in components}
    where = f"switchboard '{res.board.name}'"
    for r in runs:
        for stage in STAGES:
            missing = [c for c in r.get(stage, []) if c not in known]
            if missing:
                raise BurgerError(
                    f"{where} run '{r.get('name')}' {stage} names unknown components {missing}"
                )
        for c in components:
            if (
                c["type"] in POLLERS
                and c["name"] in r.get("start", [])
                and not (c.get("background") and c["name"] in r.get("stop", []))
            ):
                raise BurgerError(
                    f"{where} run '{r.get('name')}': {c['name']} polls until "
                    "stopped; give it `background: true` and list it in stop, or the run hangs"
                )
    return components, runs


def item_locations(runs: list[dict]) -> dict[str, tuple[int, str]]:
    """Item name -> (index of the first run that lists it, its component name)."""
    out: dict[str, tuple[int, str]] = {}
    for n, r in enumerate(runs):
        for stage in STAGES:
            for name in r.get(stage, []):
                out.setdefault(name, (n, name))
    return out
