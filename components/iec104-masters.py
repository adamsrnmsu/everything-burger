"""Python check for the iec104-masters item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if every step of the on-RTU check ran, with the steps in the message."""
    seen = "; ".join(result.get("steps", [])) or "no steps ran"
    if result.get("error") or not result.get("ok"):
        return False, f"{result.get('error') or 'did not finish'} (after: {seen})"
    return True, seen


def judge_iec104(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-iec104.json"))
    if not found:
        return False, "no burger-iec104.json received from the VM"
    return judge(json.loads(found[0].read_text()))
