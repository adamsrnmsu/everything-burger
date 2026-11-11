"""Python check for the groundtruth item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if every groundtruth_monitor check passed; name each one that did not."""
    checks = result.get("checks", [])
    bad = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    problems = ([result["error"]] if result.get("error") else []) + bad
    if len(checks) < result.get("expected", 1) and not result.get("error"):
        problems.append(f"only {len(checks)} of {result['expected']} checks ran")
    who = f"elasticsearch-py {result.get('client')}, pybennu {result.get('pybennu')}"
    if problems:
        return False, f"{'; '.join(problems)} ({who}, {len(checks) - len(bad)}/{len(checks)} ok)"
    return True, f"{len(checks)} checks ok ({who}); " + "; ".join(c["detail"] for c in checks[-4:])


def judge_groundtruth(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-groundtruth.json"))
    if not found:
        return False, "no burger-groundtruth.json received from the VM"
    return judge(json.loads(found[0].read_text()))
