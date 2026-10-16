import csv
import json
import re

from burger.checks.base import CheckContext, Row, find, missing, row


def files(spec: dict, ctx: CheckContext) -> Row:
    found = find(spec, ctx)
    if not found:
        return missing(spec, ctx, "files")
    need, min_bytes = spec.get("min", 1), spec.get("min_bytes", 0)
    big = [p for p in found if p.stat().st_size >= min_bytes]
    ok = len(big) >= need
    reason = f"{len(big)} file(s) match {spec['glob']}" + (
        f" at >= {min_bytes} bytes" if min_bytes else ""
    )
    return row(ctx, "files", ok, reason if ok else f"{reason}, need {need}", found)


def _dig(doc, field: str):
    for part in field.split("."):
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def _json_one(doc, spec: dict) -> str:
    """Empty string when the document passes, else why not."""
    field = spec.get("field", "")
    try:
        value = _dig(doc, field) if field else doc
    except (KeyError, IndexError, ValueError, TypeError):
        return "" if spec.get("exists") is False else f"field '{field}' is missing"
    if spec.get("exists") is False:
        return f"field '{field}' exists"
    if "equals" in spec and value != spec["equals"]:
        return f"field '{field}' is {value!r}, want {spec['equals']!r}"
    if "matches" in spec and not re.search(str(spec["matches"]), str(value)):
        return f"field '{field}' is {value!r}, does not match {spec['matches']!r}"
    return ""


def json_check(spec: dict, ctx: CheckContext) -> Row:
    found = find(spec, ctx)
    if not found:
        return missing(spec, ctx, "json")
    for p in found:
        try:
            why = _json_one(json.loads(p.read_text()), spec)
        except ValueError as e:
            why = f"not valid JSON ({e})"
        if why:
            return row(ctx, "json", False, f"{p.name}: {why}", found)
    return row(ctx, "json", True, f"{len(found)} JSON file(s) pass", found)


def csv_check(spec: dict, ctx: CheckContext) -> Row:
    found = find(spec, ctx)
    if not found:
        return missing(spec, ctx, "csv")
    need, cols = spec.get("min_rows", 0), spec.get("columns", [])
    for p in found:
        with p.open(newline="") as f:
            reader = csv.DictReader(f)
            n = sum(1 for _ in reader)
            lacking = [c for c in cols if c not in (reader.fieldnames or [])]
        if lacking:
            return row(ctx, "csv", False, f"{p.name}: no column {', '.join(lacking)}", found)
        if n < need:
            return row(ctx, "csv", False, f"{p.name}: {n} row(s), need {need}", found)
    return row(ctx, "csv", True, f"{len(found)} CSV file(s) pass", found)


def log(spec: dict, ctx: CheckContext) -> Row:
    found = find(spec, ctx)
    if not found:
        return missing(spec, ctx, "log")
    text = "\n".join(p.read_text(errors="replace") for p in found)
    if "contains" in spec and not re.search(spec["contains"], text, re.M):
        return row(ctx, "log", False, f"no line matches {spec['contains']!r}", found)
    if "lacks" in spec:
        hit = re.search(f"^.*(?:{spec['lacks']}).*$", text, re.M)
        if hit:
            return row(ctx, "log", False, f"found {hit[0].strip()[:200]!r}", found)
    return row(ctx, "log", True, "log patterns hold", found)
