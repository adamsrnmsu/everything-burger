"""Python checks for the kafka items: read the NDJSON the listener wrote."""

import json


def _rows(ctx, name: str):
    found = sorted(p for d in ctx.dirs for p in d.glob(name))
    if not found:
        return None
    return [json.loads(ln) for p in found for ln in p.read_text().splitlines() if ln.strip()]


def judge_kafka(ctx):
    rows = _rows(ctx, "kafka_output.ndjson")
    if not rows:
        return False, "no rows in kafka_output.ndjson"
    wrong = [r for r in rows if not str(r.get("name", "")).startswith("bus")]
    topics = {r.get("topic") for r in rows}
    if wrong:
        return False, f"{len(wrong)} of {len(rows)} rows are not bus*: {wrong[0]}"
    if topics != {"burger.sensors.a", "burger.sensors.b"}:
        return False, f"topics {sorted(map(str, topics))}, want burger.sensors.a and .b"
    if not all("v" in r for r in rows):
        return False, "a row lost the message's own keys"
    return True, f"{len(rows)} bus* rows from {sorted(topics)}, no dev* row"
