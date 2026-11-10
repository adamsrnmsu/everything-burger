"""Python check for the modbus-badframes item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if no malformed frame was answered and the field device is unharmed."""
    problems = []
    if result.get("error"):
        problems.append(result["error"])
    answered = [c["name"] for c in result.get("cases", []) if c["response"]]
    if answered:
        problems.append(f"answered a malformed frame: {', '.join(answered)}")
    if len(result.get("cases", [])) < 4 and not result.get("error"):
        problems.append("fewer than 4 cases ran")
    if result.get("pid_before") != result.get("pid_after"):
        problems.append(f"field device pid {result.get('pid_before')} -> {result.get('pid_after')}")
    if (result.get("log_grew") or 0) > result["max_log_bytes"]:
        problems.append(f"stderr log grew over {result['max_log_bytes']} B")
    if not result.get("answers"):
        problems.append("the server no longer answers a normal Modbus read")
    seen = f"{len(result.get('cases', []))} cases; log grew {result.get('log_grew')} B"
    if problems:
        return False, f"{'; '.join(problems)} ({seen})"
    return True, f"malformed frames dropped, same pid, normal read answers ({seen})"


def judge_badframes(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-badframes.json"))
    if not found:
        return False, "no burger-badframes.json received from the VM"
    return judge(json.loads(found[0].read_text()))
