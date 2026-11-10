"""Python check for the logctl item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if every runtime log-control check passed; name each one that did not."""
    checks = result.get("checks", [])
    bad = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    problems = ([result["error"]] if result.get("error") else []) + bad
    if len(checks) < result.get("expected", 1) and not result.get("error"):
        problems.append(f"only {len(checks)} of {result['expected']} checks ran")
    who = f"pid {result.get('pid')} as {result.get('user')}"
    if problems:
        return False, f"{'; '.join(problems)} ({who}, {len(checks) - len(bad)}/{len(checks)} ok)"
    return True, f"{len(checks)} checks ok ({who}); " + "; ".join(c["detail"] for c in checks[-3:])


def judge_logctl(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-logctl.json"))
    if not found:
        return False, "no burger-logctl.json received from the VM"
    return judge(json.loads(found[0].read_text()))
