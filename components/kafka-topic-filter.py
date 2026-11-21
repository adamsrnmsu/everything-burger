"""Python checks for kafka-topic-filter: read the NDJSON the listener wrote."""

import json


def _rows(ctx, name: str):
    found = sorted(p for d in ctx.dirs for p in d.glob(name))
    if not found:
        return None
    return [json.loads(ln) for p in found for ln in p.read_text().splitlines() if ln.strip()]


def judge_topic_filter(ctx):
    rows = _rows(ctx, "kafka-topic-filter_output.ndjson")
    if not rows:
        return False, "no rows in kafka-topic-filter_output.ndjson"
    names = {r.get("name") for r in rows}
    if "bus1" not in names:
        return False, f"no bus1 row, names {sorted(map(str, names))}"
    if names - {"bus1"}:
        extra = sorted(map(str, names - {"bus1"}))
        return (
            False,
            f"rows from other topic filters landed: {extra} (a filter applies to every topic)",
        )
    return True, f"{len(rows)} rows, only bus1"
