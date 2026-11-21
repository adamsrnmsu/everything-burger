import json
import re
from pathlib import Path


def collect_status(dst: Path, since: str = "") -> dict:
    """Status files under dst (a results/run-N dir), keyed by (component, stage);
    `since` drops files older than this attempt (status `start` is UTC, same format)."""
    results = {}
    for f in dst.glob("*/loop-*/*.json"):
        try:
            doc = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(doc, dict):  # components save other JSON (erspan) beside status files
            continue
        if str(doc.get("start", "")) < since:
            continue
        results.setdefault((doc.get("component"), doc.get("stage")), []).append(doc)
    return results


_STAGES = ("configure", "start", "stop", "cleanup")


def _status_verdict(docs: list, comp: str, expect: dict) -> tuple[str, str]:
    """(verdict, detail) from the status files a stage left."""
    errs = [
        str(d.get("return")) for d in docs if str(d.get("return", "")).startswith("Error occurred")
    ]
    logged = [ln for d in docs for ln in d.get("logs", []) if " ERROR " in ln or " CRITICAL " in ln]
    if errs and comp in expect:
        want = re.compile(expect[comp])
        if all(want.search(e) for e in errs):
            return "ok", f"failed as expected: {errs[0][:160]}"
        return "FAIL", errs[0][:200]
    if errs:
        return "FAIL", errs[0][:200]
    if logged:
        return "WARN", logged[0][:200]
    return "ok", ""


def _stage_verdict(docs: list, comp: str, stage: str, background, expect: dict):
    if not docs and stage == "start" and comp in background:
        return "ok", "no start status: background component, ended by SCORCH at stop"
    if not docs:
        return "FAIL", "no status file (component crashed or never ran)"
    return _status_verdict(docs, comp, expect)


def evaluate(run_spec, results, background=frozenset(), expect=None):
    """(run, component, stage, verdict, detail) per stage a run lists.

    The status file's `return` is the stage's return value, `Error occurred: …`
    on an exception; a component that exits without one (sys.exit) leaves no
    file, which is a failure too. Except a background component's start:
    phēnix's shell runner SIGKILLs it on stop (exec.CommandContext races its
    own SIGTERM), so it never writes one; its stop stage carries the verdict.

    `expect` maps a component to a regex its failure must match (the item's
    `expect_error`): a matching error is `ok`, any other outcome fails. A
    sys.exit instead of the error leaves no status file, so it fails too.
    """
    expect = expect or {}
    name = run_spec.get("name")
    rows = [
        (
            name,
            comp,
            stage,
            *_stage_verdict(results.get((comp, stage), []), comp, stage, background, expect),
        )
        for stage in _STAGES
        for comp in run_spec.get(stage, [])
    ]
    for comp, pattern in expect.items():
        listed = [st for st in _STAGES if comp in run_spec.get(st, [])]
        if listed and not any(r[1] == comp and r[4].startswith("failed as expected") for r in rows):
            rows.append((name, comp, "expect_error", "FAIL", f"no stage failed with /{pattern}/"))
    return rows
