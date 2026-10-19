"""Python check for the fifo item."""

import json


def judge(result: dict) -> tuple[bool, str]:
    """Pass only if bennu-probe got an ACK (exit 0) for the batched write."""
    n, rc, reply = result.get("tags"), result.get("rc"), result.get("reply", "")
    if rc == 0:
        return True, f"provider ACKed one WRITE of {n} tags: {reply}"
    return False, f"provider did not ACK one WRITE of {n} tags (probe exit {rc}): {reply}"


def judge_fifo(ctx):
    found = sorted(p for d in ctx.dirs for p in d.glob("burger-fifo.json"))
    if not found:
        return False, "no burger-fifo.json received from the VM"
    return judge(json.loads(found[0].read_text()))
