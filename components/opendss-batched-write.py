"""Python check for the opendss-batched-write item."""

import json


def judge(result: dict) -> tuple[bool | None, str]:
    """True pass, False fail, None skipped (shown as WARN)."""
    if result.get("skipped"):
        return None, f"skipped: {result['skipped']}"
    if result.get("error"):
        return False, f"check did not finish: {result['error']}"
    devices = result.get("devices") or {}
    if not devices:
        return False, f"no device state reported (write said {result.get('write')})"
    stuck = [n for n, d in devices.items() if d["before"] == d["after"]]
    if stuck:
        return False, (
            f"OpenDSS did not apply the batched write to {', '.join(stuck)} "
            f"(write said {result.get('write')}); bennu without #64 applies only the last device"
        )
    return True, f"OpenDSS applied the batched write to all {len(devices)} devices"


def judge_opendss(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-opendss.json"))
    if not found:
        return False, "no burger-opendss.json received from the VM"
    return judge(json.loads(found[0].read_text()))
