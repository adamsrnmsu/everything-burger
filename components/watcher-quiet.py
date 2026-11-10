"""Python check for the watcher-quiet item."""

import json


def judge(results: list[dict]) -> tuple[bool, str]:
    """Pass only if every RTU's watcher-stderr.log grew by at most the bound, per minute."""
    problems, seen = [], []
    for r in results:
        if r.get("error"):
            problems.append(f"{r.get('host')}: {r['error']}")
            continue
        rate = int(r["grew"] * 60 / max(r["seconds"], 1))
        seen.append(f"{r['host']} {rate} B/min")
        if rate > r["max_per_min"]:
            problems.append(
                f"{r['host']}: watcher-stderr.log grew {rate} B/min, over {r['max_per_min']}"
            )
    if not results:
        problems.append("no RTU reported")
    if problems:
        return False, f"{'; '.join(problems)} ({', '.join(seen)})"
    return True, f"watcher-stderr.log quiet ({', '.join(seen)})"


def judge_watcher(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-watcher-*.json"))
    if not found:
        return False, "no burger-watcher-<rtu>.json received from the VMs"
    return judge([json.loads(p.read_text()) for p in found])
