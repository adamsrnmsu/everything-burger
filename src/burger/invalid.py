"""Items whose config must be rejected (`expect_invalid`) or expanded (`expect_nodes`).

Some failures stop the experiment before SCORCH runs (a bad VM name, a missing
broker), so no board can express them as a pass. Such an item is validate-only
(`routes: []`) and is judged here, on the synced apps tree, without a VM:

- a scorch item: `phenix-scorch-validate` must report it `invalid`, with output
  matching the item's regex;
- an app item: the app's `configure` and `pre-start` stages run with `--dry-run`
  on the rendered topology; one must exit non-zero with output matching the regex.

An app item with `expect_nodes` (`{hostname: [injection dst, ...]}`) must be accepted: its
`configure` stage must add exactly those nodes to the topology, each with those injections.
This is how an app that creates VMs at configure time (scale) is proven where no board can
boot them.
"""

import json
import re
from pathlib import Path

from burger.checks import Row
from burger.items import Item, scenario_app
from burger.switchboard import Resolved
from burger.vcs import board_dir, load_manifest

_RECORD = re.compile(r"(?m)^(?=(?:ok|invalid|warning|skipped|deferred)\s)")
_HEAD = re.compile(r"(\w+)\s+.*?: (\S+) \(\S*\)")
UV = ["uv", "run", "--no-project", "--quiet", "--with"]
VALIDATOR = "phenix-scorch-validate"


def _indented(lines: list[str]) -> int:
    n = 0
    while n < len(lines) and lines[n][:1].isspace():
        n += 1
    return n


def expected(res: Resolved, kind: str) -> list[Item]:
    return [
        i
        for i in [*res.items, *res.validate_only]
        if (i.expect_invalid or i.expect_nodes) and i.kind == kind
    ]


def _records(output: str, names) -> tuple[list[str], dict]:
    """The `invalid` records no item expected, and {name: (status, record)} for those expected."""
    unexpected, found = [], {}
    for rec in _RECORD.split(output):
        # a record is its first line plus the indented ones; the rest is log noise
        head, *more = rec.split("\n")
        rec = "\n".join([head, *(ln for ln in more[: _indented(more)])])
        m = _HEAD.match(rec)
        if m and m[2] in names:
            if m[1] != "warning":
                found[m[2]] = (m[1], rec)
        elif rec.startswith("invalid"):
            unexpected.append(" ".join(rec.split()))
    return unexpected, found


def _expected_row(name: str, want: str, kind: str, rec: str) -> Row:
    msg = " ".join(rec.split()).split("): ", 1)[-1][:200]  # drop the path and name
    if kind == "invalid" and re.search(want, rec):
        row = ("ok", msg)
    elif kind == "invalid":
        row = ("FAIL", f"rejected, but not matching /{want}/: {msg}")
    elif kind:
        row = ("FAIL", f"{kind}, want it rejected: {msg}")
    else:
        row = ("FAIL", "the validator did not report this component")
    return Row(name, f"rejected by {VALIDATOR}", *row, [])


def judge(output: str, expect: dict[str, str]) -> tuple[list[str], list[Row]]:
    """Read phenix-scorch-validate output against {component: regex}.

    Returns the `invalid` records no item expected, and a row per expected item.
    A record can span lines (pydantic's message does), so split on the status words.
    """
    unexpected, found = _records(output, expect)
    return unexpected, [
        _expected_row(name, want, *found.get(name, ("", ""))) for name, want in expect.items()
    ]


def _skip(items, why: str) -> list[Row]:
    return [
        Row(i.name, "expect_nodes" if i.expect_nodes else "expect_invalid", "skipped", why, [])
        for i in items
    ]


def _src(res: Resolved, settings) -> Path | None:
    src = board_dir(settings, res.board.name) / "apps" / "src" / "python"
    return src if (src / "pyproject.toml").is_file() else None


def scorch_rows(res: Resolved, settings, host, models: Path) -> list[Row]:
    """The scorch items that must be rejected, judged with the synced tree's validator."""
    items = expected(res, "scorch")
    if not items:
        return []
    src = _src(res, settings)
    if not src:
        return _skip(items, f"apps not synced; burger sync {res.board.name}")
    got = host.run([*UV, src, VALIDATOR, models], check=False, capture=True)
    if got.returncode == 127:
        return _skip(items, "uv not found")
    unexpected, rows = judge(f"{got.stdout}{got.stderr}", {i.name: i.expect_invalid for i in items})
    if unexpected:
        rows.append(Row("-", f"{VALIDATOR} (offline)", "FAIL", unexpected[0][-200:], []))
    return rows


def _installed(res: Resolved, settings, host) -> bool:
    """Whether phēnix has the apps this board synced (what `burger install` records)."""
    got = host.ctr("cat", "/tmp/burger/installed.json", check=False, capture=True)
    try:
        have = json.loads(got.stdout).get("apps") if got.returncode == 0 else None
    except ValueError:
        return False
    return have == load_manifest(settings, res.board.name)["apps"]["head"]


def _stage(host, entry: str, stage: str, stdin: str):
    """Run an installed app stage where phēnix runs (docker exec needs -i for stdin)."""
    pre = ["docker", "exec", "-i", host.s.ctr] if host.s.ctr else []
    return host.run([*pre, entry, stage, "--dry-run"], check=False, capture=True, input=stdin)


def _app_experiment(res: Resolved, item: Item, topo: dict, workdir: Path) -> dict:
    return {
        "metadata": {"name": res.board.exp, "annotations": {}},
        "spec": {
            "experimentName": res.board.exp,
            "baseDir": str(workdir / item.name),
            "topology": topo["spec"],
            "scenario": {"apps": [scenario_app(item)]},
        },
    }


def _dry_run_stages(res: Resolved, settings, host, src: Path, item: Item, stdin: str):
    """(stage, result) of the first stage that exits non-zero, else of the last stage. An
    expect_nodes item runs only configure: its verdict is configure's output."""
    entry = f"phenix-app-{item.type}"
    for stage in ("configure",) if item.expect_nodes else ("configure", "pre-start"):
        got = host.run(
            [*UV, src, entry, stage, "--dry-run"], check=False, capture=True, input=stdin
        )
        if got.returncode == 127:  # no uv: use the apps installed in phēnix, if they are
            if not _installed(res, settings, host):  # this board's; needs no index
                break
            got = _stage(host, entry, stage, stdin)
        if got.returncode:
            break
    return stage, got


def _added(topo: dict, out: str) -> dict[str, set[str]]:
    """{hostname: injection destinations} of the nodes in configure's output `out` that `topo`
    lacks. Raises ValueError (or a subclass) when `out` is not an experiment."""
    old = {n["general"]["hostname"] for n in topo["spec"]["nodes"]}
    nodes = json.loads(out)["spec"]["topology"]["nodes"]
    new = [n for n in nodes if n["general"]["hostname"] not in old]
    names = [n["general"]["hostname"] for n in new]
    if dup := sorted({h for h in names if names.count(h) > 1}):
        raise ValueError(f"duplicate added hostnames {dup}")
    return {n["general"]["hostname"]: {i["dst"] for i in n.get("injections") or []} for n in new}


def _nodes_problem(want: dict, topo: dict, out: str) -> str:
    """Why configure's output `out` is not `want` added to `topo`; "" when it is."""
    try:
        added = _added(topo, out)
    except (ValueError, KeyError, TypeError) as e:
        return f"configure output is not an experiment: {type(e).__name__}: {e}"
    if set(added) != set(want):
        return f"added nodes {sorted(added)}, want {sorted(want)}"
    for host, dsts in want.items():
        if missing := sorted(set(dsts) - added[host]):
            return f"{host} lacks injections {missing}"
    return ""


def _nodes_row(item: Item, stage: str, got, topo: dict) -> Row:
    label = f"{item.type} configure adds nodes"
    if got.returncode:
        tail = " ".join(f"{got.stdout}{got.stderr}".split())[-200:]
        return Row(item.name, label, "FAIL", f"{stage} rejected the config: {tail}", [])
    problem = _nodes_problem(item.expect_nodes, topo, got.stdout)
    return Row(item.name, label, "FAIL" if problem else "ok", problem, [])


def _app_row(item: Item, stage: str, got, topo: dict) -> list[Row]:
    out = f"{got.stdout}{got.stderr}"
    label = f"{item.type} {stage} rejects"
    if got.returncode == 127:
        return _skip([item], "uv not found, and this board's apps are not installed in phēnix")
    if item.expect_nodes:
        return [_nodes_row(item, stage, got, topo)]
    if not got.returncode:
        return [Row(item.name, label, "FAIL", "every stage accepted the config", [])]
    if re.search(item.expect_invalid, out):
        return [Row(item.name, label, "ok", "", [])]
    tail = " ".join(out.split())[-200:]
    return [Row(item.name, label, "FAIL", f"not matching /{item.expect_invalid}/: {tail}", [])]


def app_rows(res: Resolved, settings, host, topo: dict, workdir: Path) -> list[Row]:
    """The app items that must be rejected or expanded: run the app's early stages on `topo`."""
    items = expected(res, "app")
    if not items:
        return []
    src = _src(res, settings)
    if not src:
        return _skip(items, f"apps not synced; burger sync {res.board.name}")
    rows = []
    for item in items:
        stdin = json.dumps(_app_experiment(res, item, topo, workdir))
        rows += _app_row(item, *_dry_run_stages(res, settings, host, src, item, stdin), topo)
    return rows
