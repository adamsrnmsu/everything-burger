import json
from pathlib import Path

import yaml

from burger import fdlogs, minimega, phenix, probes
from burger.checks import CheckContext, Row, run_checks
from burger.checks.status import evaluate
from burger.compose import compose, health_checks, item_locations
from burger.experiment import collect, not_running
from burger.invalid import app_rows, expected, judge
from burger.switchboard import Resolved, pinned_label
from burger.vcs import bennu_label, board_dir, load_manifest

NO_VALIDATOR = "command -v phenix-scorch-validate"


def models_yaml(res: Resolved, path: Path, stage_dir: str) -> None:
    """Every scorch item config, validate-only ones included, in the old `components:` format.

    `{asset_dir}` resolves as render does, so the validator sees the real paths.
    """
    comps = [
        {"name": i.name, "type": i.type, "metadata": i.config}
        for i in [*res.items, *res.validate_only]
        if i.kind == "scorch"
    ]
    text = yaml.safe_dump({"components": comps}, sort_keys=False)
    path.write_text(text.replace("{asset_dir}", f"{stage_dir}/assets"))


def _install_row(res: Resolved, man: dict, host) -> Row:
    got = host.ctr("cat", "/tmp/burger/installed.json", check=False, capture=True)
    try:
        installed = json.loads(got.stdout) if got.returncode == 0 else {}
    except ValueError:
        installed = {}
    head = man["apps"]["head"]
    ok = installed.get("apps") == head
    have = str(installed.get("apps") or "nothing")[:10]
    reason = "" if ok else f"phēnix has {have}, want {head[:10]}; burger install {res.board.name}"
    return Row("-", "phenix-apps install", "ok" if ok else "FAIL", reason, [])


def _needs_apps_pr(res: Resolved) -> bool:
    """Whether an item's models need a PR the installed apps may lack."""
    return any(i.requires.get("apps_prs") for i in [*res.items, *res.validate_only])


def _validator_row(got, unexpected: list[str], rejects: list[Row], models: Path) -> Row:
    """ok unless the validator rejected something unexpected or crashed without rejecting."""
    check = "phenix-scorch-validate"
    if unexpected or (got.returncode and not any(r.verdict == "ok" for r in rejects)):
        tail = (unexpected or [f"{got.stdout or ''}{got.stderr or ''}"])[0]
        tail = " ".join(tail.split())[-200:]
        return Row("-", check, "FAIL", tail or f"exit {got.returncode}", [str(models)])
    return Row("-", check, "ok", "", [str(models)])


def _judged_rows(res: Resolved, got, models: Path) -> list[Row]:
    """The validator's own row, then one per item that must be rejected."""
    # items with expect_invalid must be rejected: the pass is the validator saying so
    expect = {i.name: i.expect_invalid for i in expected(res, "scorch")}
    unexpected, rejects = judge(f"{got.stdout or ''}{got.stderr or ''}", expect)
    rejects = [Row(r.item, r.check, r.verdict, r.reason, [str(models)]) for r in rejects]
    return [_validator_row(got, unexpected, rejects, models), *rejects]


def _validate_rows(res: Resolved, settings, host, models: Path) -> list[Row]:
    check = "phenix-scorch-validate"
    found = host.ctr("sh", "-c", NO_VALIDATOR, check=False, capture=True).returncode == 0
    if not found:
        needs = _needs_apps_pr(res)
        reason = "not installed" + (" but an item needs an apps PR" if needs else "")
        return [Row("-", check, "FAIL" if needs else "skipped", reason, [])]
    stage = f"{settings.phenix_dir}/burger/{res.board.name}"
    host.put(models, f"{stage}/models.yaml")
    got = host.ctr(
        check, f"{stage}/scenario.json", f"{stage}/models.yaml", check=False, capture=True
    )
    return _judged_rows(res, got, models)


def _label(host: str, args: str) -> str:
    return f"vm {host}: {' '.join(args.split())[:60]}"


def _attributed_row(n: int, checks, hits: list[int], error: str, advisory: bool) -> Row:
    item, host, c = checks[n]
    label = _label(host, c["args"])
    if not hits:
        return Row(item, label, "skipped", f"health failed, no check named: {error[:100]}", [])
    if n in hits:
        return _hit_row(item, label, n, checks, hits, error, advisory)
    if len(hits) == 1 and host == checks[hits[0]][1]:
        ran = n < hits[0]
        return Row(item, label, "ok" if ran else "skipped", "" if ran else "not reached", [])
    return Row(item, label, "skipped", "not confirmed", [])


def _hit_row(item, label, n, checks, hits, error: str, advisory: bool) -> Row:
    """A check the failure names: its own FAIL when alone, else skipped as one of several."""
    if len(hits) > 1:
        hosts = ", ".join(dict.fromkeys(checks[h][1] for h in hits))
        return Row(item, label, "skipped", f"health failed on one of {hosts}: {error[:200]}", [])
    return Row(item, label, "advisory-FAIL" if advisory else "FAIL", error[:200], [])


def _attribute(checks, error: str, verdict: str, advisory: bool) -> list[Row]:
    """One row per vm check of a health component, from the component's start status.

    Only an `ok` start confirms the checks. A failure names the command, not the
    host: when several checks share its args, every candidate is skipped rather than
    guessing. Checks the failure says nothing about are skipped as not confirmed.
    """
    if verdict == "ok":
        return [Row(i, _label(h, c["args"]), "ok", "", []) for i, h, c in checks]
    hits = [n for n, (_, _, c) in enumerate(checks) if f"command '{c['args']}' failed" in error]
    return [_attributed_row(n, checks, hits, error, advisory) for n in range(len(checks))]


def _error(results: dict, comp: str) -> str:
    docs = results.get((comp, "start"), [])
    return next(
        (str(d["return"]) for d in docs if str(d.get("return", "")).startswith("Error")), ""
    )


def _host_rows(res: Resolved, settings, host, man: dict, rd_out: Path) -> list[Row]:
    """Rows from the host itself: installs, running experiment, probes, offline validation."""
    name = res.board.name
    rows = [
        _install_row(res, man, host),
        *minimega.rows(host, settings, man, name),
        *phenix.rows(host, settings, man, name),
    ]
    bad = not_running(host, res.board.exp)
    rows.append(Row("-", "experiment + VMs running", "FAIL" if bad else "ok", bad, []))
    if res.board.probes:
        rows += probes.run(host, settings, res, man)
    models_yaml(res, rd_out / "models.yaml", f"{settings.phenix_dir}/burger/{name}")
    rows += _validate_rows(res, settings, host, rd_out / "models.yaml")
    topo_file = rd_out / "topology.json"
    if topo_file.is_file():
        rows += app_rows(res, settings, host, json.loads(topo_file.read_text()), rd_out / "apps")
    return rows


def _status_row(rd: Path, n: int, stage_row: tuple, advisory: bool) -> Row:
    _, comp, stage, verdict, detail = stage_row
    where = rd / "results" / f"run-{n}" / comp
    shown = "advisory-FAIL" if advisory and verdict == "FAIL" else verdict
    return Row(comp, f"status {stage}", shown, detail, [str(where)] if where.exists() else [])


def _status_rows(res: Resolved, settings, host, rd: Path, components, runs):
    """(rows, status, errors): a row per stage of every run, each (component, stage)'s
    verdict, and each component's start error."""
    background = {c["name"] for c in components if c.get("background")}
    advisory_items = {i.name for i in res.items if i.advisory}
    expect = {i.name: i.expect_error for i in res.items if i.expect_error}
    rows, status, errors = [], {}, {}
    for n, run in enumerate(runs):
        results = collect(res, settings, host, n)
        extras = str(run.get("name", "")).startswith("extras")
        for stage_row in evaluate(run, results, background, expect):
            status[stage_row[1:3]] = stage_row[3]
            rows.append(_status_row(rd, n, stage_row, extras or stage_row[1] in advisory_items))
        errors.update({c["name"]: e for c in components if (e := _error(results, c["name"]))})
    return rows, status, errors


def _health_rows(res: Resolved, man: dict, status: dict, errors: dict) -> list[Row]:
    rows = []
    for item in ["", *(i for i in res.items if i.advisory and any("vm" in v for v in i.verify))]:
        comp = f"health-{item.name}" if item else "health"
        checks = health_checks(res, man, item or None)
        if checks and (comp, "start") in status:
            rows += _attribute(checks, errors.get(comp, ""), status[(comp, "start")], bool(item))
    return rows


def _item_rows(res: Resolved, man: dict, rd: Path, runs) -> list[Row]:
    rows = []
    where = item_locations(runs)
    for item in res.items:
        dirs = []
        if item.name in where:
            n, comp = where[item.name]
            dirs = sorted((rd / "results" / f"run-{n}" / comp).glob("loop-*"))
        rows += run_checks(item, CheckContext(item, dirs, res.route, man, rd, res.linux_only))
    return rows


def verify(res: Resolved, settings, host) -> list[Row]:
    name = res.board.name
    rd, man = board_dir(settings, name), load_manifest(settings, name)
    rd_out = rd / "out"
    rd_out.mkdir(exist_ok=True)
    rows = _host_rows(res, settings, host, man, rd_out)
    components, runs = compose(res, man)
    status_rows, status, errors = _status_rows(res, settings, host, rd, components, runs)
    rows += status_rows
    rows += _health_rows(res, man, status, errors)
    rows += _item_rows(res, man, rd, runs)
    fd, cfg = fdlogs_rows(res, settings, host, rows)
    man = {**man, "fdlogs": fd}
    (rd / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    (rd / "report.md").write_text(
        render_report(res, man, {"linux_only": res.linux_only}, rows, cfg)
    )
    return rows


def fdlogs_rows(res: Resolved, settings, host, rows: list[Row]) -> tuple[dict, dict]:
    """Collect and scan the RTUs' field-device logs; add one row to `rows`."""
    cfg = fdlogs.settings_for(res.board)
    summary = fdlogs.collect(res, settings, host, cfg)
    if summary:
        verdict, reason = fdlogs.verdict(summary, cfg["fail_on"])
        where = board_dir(settings, res.board.name) / "results" / "fdlogs"
        rows.append(Row("-", "field-device logs", verdict, reason, [str(where)]))
    return summary, cfg


def _pinned_line(pin: dict | None) -> str:
    return f"{pinned_label(pin)} `{pin['sha'][:10]}` ({pin['from']})" if pin else "stock"


def _apps_line(a: dict) -> str:
    if "branch" in a:
        return f"`{a['head'][:10]}` = branch {a['remote']}/{a['branch']}"
    prs = ", ".join(f"#{n} `{s[:8]}`" for n, s in a["prs"].items()) or "no PRs"
    return f"`{a['head'][:10]}` = main `{a['base'][:10]}` + {prs}"


def _bennu_line(b: dict | None) -> str:
    if not b:
        return "stock bennu.qc2"
    return (
        f"{bennu_label(b)} `{b['sha'][:10]}` deb `{b.get('deb_version')}` wheel `{b.get('wheel')}`"
    )


def _row_line(r: Row) -> str:
    cells = [r.item, r.check, r.verdict, r.reason, ", ".join(f"`{p}`" for p in r.paths)]
    return "| " + " | ".join(" ".join(str(c).split()).replace("|", "/") for c in cells) + " |"


def render_report(
    res: Resolved, man: dict, state: dict, rows: list[Row], fd_cfg: dict | None = None
) -> str:
    lines = [
        f"# everything-burger switchboard `{res.board.name}` ({res.board.exp})",
        "",
        f"- apps: {_apps_line(man['apps'])}",
        f"- bennu: {_bennu_line(man.get('bennu'))}",
        f"- minimega: {_pinned_line(man.get('minimega'))}",
        f"- phenix: {_pinned_line(man.get('phenix'))}",
        f"- topologies: `{(man.get('topologies') or 'site route')[:10]}`"
        + (" (linux-only)" if state["linux_only"] else ""),
        f"- synced {man.get('synced')}",
        "",
        "| item | check | verdict | reason | paths |",
        "|---|---|---|---|---|",
        *map(_row_line, rows),
    ]
    lines += fdlogs.render_section(man.get("fdlogs") or {}, fd_cfg or fdlogs.DEFAULTS)
    fails = sum(r.verdict == "FAIL" for r in rows)
    lines += [
        "",
        f"**{'FAIL' if fails else 'PASS'}**" + (f": {fails} failing check(s)" if fails else ""),
    ]
    return "\n".join(lines) + "\n"
