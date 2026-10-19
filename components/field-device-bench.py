"""Python check for the field-device-bench item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass or fail a result file, with the measured number in the message."""
    n, win, limit = result.get("tags"), result.get("window_s"), result.get("max_cycle_s")
    seen = f"{result.get('requests')} request(s), {result.get('tag_writes')} tag write(s)"
    if result.get("error"):
        return False, f"bench did not finish: {result['error']} ({seen})"
    cycle = result.get("seconds_per_cycle")
    if cycle is None:
        return False, (
            f"fewer than 2 cycles in {win} s at {n} tags, so a cycle takes more than "
            f"{win} s (limit {limit} s; {seen})"
        )
    ok = cycle <= limit
    verdict = "within" if ok else "over"
    return (
        ok,
        f"{cycle:.3f} s per cycle at {n} tags, {verdict} the {limit} s limit "
        f"({seen}, {win} s window)",
    )


def judge_bench(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-bench.json"))
    if not found:
        return False, "no burger-bench.json received from the VM"
    return judge(json.loads(found[0].read_text()))
