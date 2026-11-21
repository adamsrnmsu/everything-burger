"""Python checks for kafka-bad-json: read the NDJSON the listener wrote."""

import json


def _rows(ctx, name: str):
    found = sorted(p for d in ctx.dirs for p in d.glob(name))
    if not found:
        return None
    return [json.loads(ln) for p in found for ln in p.read_text().splitlines() if ln.strip()]


def judge_bad_json(ctx):
    rows = _rows(ctx, "kafka-bad-json_output.ndjson")
    # one round of good messages can land before the first non-JSON one: demand many
    if not rows or len(rows) < 5:
        return (
            False,
            f"{len(rows or [])} row(s) landed, want 5: the listener died on non-JSON",
        )
    return True, f"{len(rows)} rows landed with non-JSON messages in the topic"
