"""Python check for cc-recv-stream: the streamed file matches the sha256 the VM computed."""

import hashlib


def judge_stream(ctx):
    data = sorted(p for d in ctx.dirs for p in d.glob("burger-stream.bin"))
    sums = sorted(p for d in ctx.dirs for p in d.glob("burger-stream.sha256"))
    if not data:
        return False, "no burger-stream.bin received (stream recv)"
    if not sums:
        return False, "no burger-stream.sha256 received (default mount recv)"
    want = sums[-1].read_text().strip()
    got = hashlib.sha256(data[-1].read_bytes()).hexdigest()
    size = data[-1].stat().st_size
    if got != want:
        return False, f"sha256 mismatch: VM {want[:12]}, host {got[:12]} ({size} bytes)"
    return True, f"{size} bytes streamed, sha256 matches the VM's"
