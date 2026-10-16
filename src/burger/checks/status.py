import json
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


def evaluate(run_spec, results, background=frozenset()):
    """(run, component, stage, verdict, detail) per stage a run lists.

    The status file's `return` is the stage's return value, `Error occurred: …`
    on an exception; a component that exits without one (sys.exit) leaves no
    file, which is a failure too. Except a background component's start:
    phēnix's shell runner SIGKILLs it on stop (exec.CommandContext races its
    own SIGTERM), so it never writes one; its stop stage carries the verdict.
    """
    rows = []
    name = run_spec.get("name")
    for stage in ("configure", "start", "stop", "cleanup"):
        for comp in run_spec.get(stage, []):
            docs = results.get((comp, stage), [])
            if not docs and stage == "start" and comp in background:
                msg = "no start status: background component, ended by SCORCH at stop"
                rows.append((name, comp, stage, "ok", msg))
                continue
            if not docs:
                msg = "no status file (component crashed or never ran)"
                rows.append((name, comp, stage, "FAIL", msg))
                continue
            errs = [str(d.get("return")) for d in docs if str(d.get("return", "")).startswith("Error occurred")]
            logged = [ln for d in docs for ln in d.get("logs", []) if " ERROR " in ln or " CRITICAL " in ln]
            if errs:
                rows.append((name, comp, stage, "FAIL", errs[0][:200]))
            elif logged:
                rows.append((name, comp, stage, "WARN", logged[0][:200]))
            else:
                rows.append((name, comp, stage, "ok", ""))
    return rows
