import json
from pathlib import Path

import yaml

from burger.checks import CheckContext, Row, run_checks
from burger.checks.status import evaluate
from burger.compose import compose, health_checks, item_locations
from burger.experiment import collect, not_running
from burger.invalid import app_rows, expected, judge
from burger.switchboard import Resolved
from burger.vcs import board_dir, load_manifest

NO_VALIDATOR = "command -v phenix-scorch-validate"


def models_yaml(res: Resolved, path: Path) -> None:
    """Every scorch item config, validate-only ones included, in the old `components:` format."""
    comps = [
        {"name": i.name, "type": i.type, "metadata": i.config}
        for i in [*res.items, *res.validate_only]
        if i.kind == "scorch"
    ]
    path.write_text(yaml.safe_dump({"components": comps}, sort_keys=False))


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


def _validate_rows(res: Resolved, settings, host, models: Path) -> list[Row]:
    check = "phenix-scorch-validate"
    found = host.ctr("sh", "-c", NO_VALIDATOR, check=False, capture=True).returncode == 0
    if not found:
        needs = any(
            i.requires.get("apps_prs") for i in [*res.items, *res.validate_only]
        )  # the models need a PR the installed apps may lack
        reason = "not installed" + (" but an item needs an apps PR" if needs else "")
        return [Row("-", check, "FAIL" if needs else "skipped", reason, [])]
    stage = f"{settings.phenix_dir}/burger/{res.board.name}"
    host.put(models, f"{stage}/models.yaml")
    got = host.ctr(
        check, f"{stage}/scenario.json", f"{stage}/models.yaml", check=False, capture=True
    )
    # items with expect_invalid must be rejected: the pass is the validator saying so
    expect = {i.name: i.expect_invalid for i in expected(res, "scorch")}
    unexpected, rejects = judge(f"{got.stdout or ''}{got.stderr or ''}", expect)
    rejects = [Row(r.item, r.check, r.verdict, r.reason, [str(models)]) for r in rejects]
    if unexpected or (got.returncode and not any(r.verdict == "ok" for r in rejects)):
        tail = (unexpected or [f"{got.stdout or ''}{got.stderr or ''}"])[0]
        tail = " ".join(tail.split())[-200:]
        return [Row("-", check, "FAIL", tail or f"exit {got.returncode}", [str(models)]), *rejects]
    return [Row("-", check, "ok", "", [str(models)]), *rejects]


def _label(host: str, args: str) -> str:
    return f"vm {host}: {' '.join(args.split())[:60]}"


def _attribute(checks, error: str, verdict: str, advisory: bool) -> list[Row]:
    """One row per vm check of a health component, from the component's start status.

    Only an `ok` start confirms the checks. A failure names the command, not the
    host: when several checks share its args, every candidate is skipped rather than
    guessing. Checks the failure says nothing about are skipped as not confirmed.
    """
    if verdict == "ok":
        return [Row(i, _label(h, c["args"]), "ok", "", []) for i, h, c in checks]
    hits = [n for n, (_, _, c) in enumerate(checks) if f"command '{c['args']}' failed" in error]
    hosts = ", ".join(dict.fromkeys(checks[n][1] for n in hits))
    rows = []
    for n, (item, host, c) in enumerate(checks):
        label = _label(host, c["args"])
        if not hits:
            rows.append(
                Row(item, label, "skipped", f"health failed, no check named: {error[:100]}", [])
            )
        elif n in hits and len(hits) > 1:
            reason = f"health failed on one of {hosts}: {error[:200]}"
            rows.append(Row(item, label, "skipped", reason, []))
        elif n in hits:
            rows.append(Row(item, label, "advisory-FAIL" if advisory else "FAIL", error[:200], []))
        elif len(hits) == 1 and host == checks[hits[0]][1]:
            ran = n < hits[0]
            rows.append(
                Row(item, label, "ok" if ran else "skipped", "" if ran else "not reached", [])
            )
        else:
            rows.append(Row(item, label, "skipped", "not confirmed", []))
    return rows


def _error(results: dict, comp: str) -> str:
    docs = results.get((comp, "start"), [])
    return next(
        (str(d["return"]) for d in docs if str(d.get("return", "")).startswith("Error")), ""
    )


def verify(res: Resolved, settings, host) -> list[Row]:
    name = res.board.name
    rd, man = board_dir(settings, name), load_manifest(settings, name)
    rd_out = rd / "out"
    rd_out.mkdir(parents=True, exist_ok=True)
    rows = [_install_row(res, man, host)]
    bad = not_running(host, res.board.exp)
    rows.append(Row("-", "experiment + VMs running", "FAIL" if bad else "ok", bad, []))
    models_yaml(res, rd_out / "models.yaml")
    rows += _validate_rows(res, settings, host, rd_out / "models.yaml")
    topo_file = rd_out / "topology.json"
    if topo_file.is_file():
        rows += app_rows(res, settings, host, json.loads(topo_file.read_text()), rd_out / "apps")

    components, runs = compose(res, man)
    background = {c["name"] for c in components if c.get("background")}
    advisory_items = {i.name for i in res.items if i.advisory}
    expect = {i.name: i.expect_error for i in res.items if i.expect_error}
    status, errors = {}, {}
    for n, run in enumerate(runs):
        results = collect(res, settings, host, n)
        extras = str(run.get("name", "")).startswith("extras")
        for _, comp, stage, verdict, detail in evaluate(run, results, background, expect):
            status[(comp, stage)] = verdict
            where = rd / "results" / f"run-{n}" / comp
            advisory = extras or comp in advisory_items
            shown = "advisory-FAIL" if advisory and verdict == "FAIL" else verdict
            rows.append(
                Row(comp, f"status {stage}", shown, detail, [str(where)] if where.exists() else [])
            )
        errors.update({c["name"]: e for c in components if (e := _error(results, c["name"]))})

    for item in ["", *(i for i in res.items if i.advisory and any("vm" in v for v in i.verify))]:
        comp = f"health-{item.name}" if item else "health"
        checks = health_checks(res, man, item or None)
        if checks and (comp, "start") in status:
            rows += _attribute(checks, errors.get(comp, ""), status[(comp, "start")], bool(item))

    where = item_locations(runs)
    for item in res.items:
        dirs = []
        if item.name in where:
            n, comp = where[item.name]
            dirs = sorted((rd / "results" / f"run-{n}" / comp).glob("loop-*"))
        rows += run_checks(item, CheckContext(item, dirs, res.route, man, rd, res.linux_only))

    (rd / "report.md").write_text(render_report(res, man, {"linux_only": res.linux_only}, rows))
    return rows


def render_report(res: Resolved, man: dict, state: dict, rows: list[Row]) -> str:
    b = man.get("bennu")
    prs = ", ".join(f"#{n} `{s[:8]}`" for n, s in man["apps"]["prs"].items()) or "no PRs"
    bennu = (
        f"#{b['pr']} `{b['sha'][:10]}` deb `{b.get('deb_version')}` wheel `{b.get('wheel')}`"
        if b
        else "stock bennu.qc2"
    )
    lines = [
        f"# everything-burger switchboard `{res.board.name}` ({res.board.exp})",
        "",
        f"- apps: `{man['apps']['head'][:10]}` = main `{man['apps']['base'][:10]}` + {prs}",
        f"- bennu: {bennu}",
        f"- topologies: `{(man.get('topologies') or 'site route')[:10]}`"
        + (" (linux-only)" if state["linux_only"] else ""),
        f"- synced {man.get('synced')}",
        "",
        "| item | check | verdict | reason | paths |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cells = [r.item, r.check, r.verdict, r.reason, ", ".join(f"`{p}`" for p in r.paths)]
        lines.append(
            "| " + " | ".join(" ".join(str(c).split()).replace("|", "/") for c in cells) + " |"
        )
    fails = sum(r.verdict == "FAIL" for r in rows)
    lines += [
        "",
        f"**{'FAIL' if fails else 'PASS'}**" + (f": {fails} failing check(s)" if fails else ""),
    ]
    return "\n".join(lines) + "\n"
