"""Python check for the modbus-churn item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if the server stayed healthy through the churn, with the numbers in the message."""
    n, done = result.get("conns"), result.get("done")
    before, after = result.get("sockets_before"), result.get("sockets_after")
    grew = result.get("log_grew")
    seen = f"{done}/{n} connections; socket fds {before} -> {after}; log grew {grew} B"
    problems = []
    if result.get("error"):
        problems.append(result["error"])
    if before is not None and after is not None and after - before > result["slack"]:
        problems.append(f"socket fds grew by {after - before}, over the slack of {result['slack']}")
    if (result.get("log_grew") or 0) > result["max_log_bytes"]:
        problems.append(f"stderr log grew over {result['max_log_bytes']} B")
    if not result.get("answers"):
        problems.append("the server no longer answers a Modbus read")
    if problems:
        return False, f"{'; '.join(problems)} ({seen})"
    return True, f"no fd leak, server still answers ({seen})"


def judge_churn(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-churn.json"))
    if not found:
        return False, "no burger-churn.json received from the VM"
    return judge(json.loads(found[0].read_text()))
