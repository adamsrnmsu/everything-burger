import json
from pathlib import Path

import yaml

from burger.checks import CheckContext, Row, run_checks
from burger.checks.status import evaluate
from burger.compose import compose, health_checks, item_locations
from burger.experiment import collect, not_running
from burger.switchboard import Resolved
from burger.vcs import board_dir, load_manifest

NO_VALIDATOR = "command -v phenix-scorch-validate"


def _models_yaml(res: Resolved, path: Path) -> None:
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


def _validate_row(res: Resolved, settings, host, models: Path) -> Row:
    check = "phenix-scorch-validate"
    found = host.ctr("sh", "-c", NO_VALIDATOR, check=False, capture=True).returncode == 0
    if not found:
        needs = any(
            i.requires.get("apps_prs") for i in [*res.items, *res.validate_only]
        )  # the models need a PR the installed apps may lack
        reason = "not installed" + (" but an item needs an apps PR" if needs else "")
        return Row("-", check, "FAIL" if needs else "skipped", reason, [])
    stage = f"{settings.phenix_dir}/burger/{res.board.name}"
    host.put(models, f"{stage}/models.yaml")
    got = host.ctr(
        check, f"{stage}/scenario.json", f"{stage}/models.yaml", check=False, capture=True
    )
    if got.returncode == 0:
        return Row("-", check, "ok", "", [str(models)])
    tail = " ".join(f"{got.stdout or ''}{got.stderr or ''}".split())[-200:]
    return Row("-", check, "FAIL", tail or f"exit {got.returncode}", [str(models)])


def _label(host: str, args: str) -> str:
    return f"vm {host}: {' '.join(args.split())[:60]}"


def _attribute(checks, error: str, failed: bool, advisory: bool) -> list[Row]:
    """One row per vm check of a health component, from the component's start status."""
    if not failed:
        return [Row(i, _label(h, c["args"]), "ok", "", []) for i, h, c in checks]
    bad = next(
        (n for n, (_, _, c) in enumerate(checks) if f"command '{c['args']}' failed" in error), None
    )
    rows = []
    for n, (item, host, c) in enumerate(checks):
        label = _label(host, c["args"])
        if bad is None:
            rows.append(
                Row(item, label, "skipped", f"health failed, no check named: {error[:100]}", [])
            )
        elif n == bad:
            rows.append(Row(item, label, "advisory-FAIL" if advisory else "FAIL", error[:200], []))
        elif n > bad and host == checks[bad][1]:
            rows.append(Row(item, label, "skipped", "not reached", []))
        else:
            rows.append(Row(item, label, "ok", "", []))
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
    _models_yaml(res, rd_out / "models.yaml")
    rows.append(_validate_row(res, settings, host, rd_out / "models.yaml"))

    components, runs = compose(res, man)
    background = {c["name"] for c in components if c.get("background")}
    status, errors = {}, {}
    for n, run in enumerate(runs):
        results = collect(res, settings, host, n)
        advisory = str(run.get("name", "")).startswith("extras")
        for _, comp, stage, verdict, detail in evaluate(run, results, background):
            status[(comp, stage)] = verdict
            where = rd / "results" / f"run-{n}" / comp
            shown = "advisory-FAIL" if advisory and verdict == "FAIL" else verdict
            rows.append(
                Row(comp, f"status {stage}", shown, detail, [str(where)] if where.exists() else [])
            )
        errors.update({c["name"]: e for c in components if (e := _error(results, c["name"]))})

    for item in ["", *(i for i in res.items if i.advisory and any("vm" in v for v in i.verify))]:
        comp = f"health-{item.name}" if item else "health"
        checks = health_checks(res, man, item or None)
        if checks and (comp, "start") in status:
            failed = status[(comp, "start")] == "FAIL"
            rows += _attribute(checks, errors.get(comp, ""), failed, bool(item))

    where = item_locations(runs)
    for item in res.items:
        dirs = []
        if item.name in where:
            n, comp = where[item.name]
            dirs = sorted((rd / "results" / f"run-{n}" / comp).glob("loop-*"))
        rows += run_checks(item, CheckContext(item, dirs, res.route, man, rd))

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
        f"- topologies: `{man['topologies'][:10]}`"
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
