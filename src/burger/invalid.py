"""Items whose config must be rejected (`expect_invalid`): the pass is the rejection.

Some failures stop the experiment before SCORCH runs (a bad VM name, a missing
broker), so no board can express them as a pass. Such an item is validate-only
(`routes: []`) and is judged here, on the synced apps tree, without a VM:

- a scorch item: `phenix-scorch-validate` must report it `invalid`, with output
  matching the item's regex;
- an app item: the app's `configure` and `pre-start` stages run with `--dry-run`
  on the rendered topology; one must exit non-zero with output matching the regex.
"""

import json
import re
from pathlib import Path

from burger.checks import Row
from burger.items import Item
from burger.switchboard import Resolved
from burger.vcs import board_dir

_RECORD = re.compile(r"(?m)^(?=(?:ok|invalid|warning|skipped|deferred)\s)")
_HEAD = re.compile(r"(\w+)\s+.*?: (\S+) \(\S*\)")
UV = ["uv", "run", "--no-project", "--quiet", "--with"]
VALIDATOR = "phenix-scorch-validate"


def _indented(lines: list[str]) -> int:
    n = 0
    while n < len(lines) and lines[n][:1].isspace():
        n += 1
    return n


def expected(res: Resolved, kind: str) -> list[Item]:
    return [i for i in [*res.items, *res.validate_only] if i.expect_invalid and i.kind == kind]


def judge(output: str, expect: dict[str, str]) -> tuple[list[str], list[Row]]:
    """Read phenix-scorch-validate output against {component: regex}.

    Returns the `invalid` records no item expected, and a row per expected item.
    A record can span lines (pydantic's message does), so split on the status words.
    """
    unexpected, found = [], {}
    for rec in _RECORD.split(output):
        # a record is its first line plus the indented ones; the rest is log noise
        head, *more = rec.split("\n")
        rec = "\n".join([head, *(ln for ln in more[: _indented(more)])])
        m = _HEAD.match(rec)
        if m and m[2] in expect:
            if m[1] != "warning":
                found[m[2]] = (m[1], rec)
        elif rec.startswith("invalid"):
            unexpected.append(" ".join(rec.split()))
    rows = []
    for name, want in expect.items():
        kind, rec = found.get(name, ("", ""))
        msg = " ".join(rec.split()).split("): ", 1)[-1][:200]  # drop the path and name
        if kind == "invalid" and re.search(want, rec):
            row = ("ok", msg)
        elif kind == "invalid":
            row = ("FAIL", f"rejected, but not matching /{want}/: {msg}")
        elif kind:
            row = ("FAIL", f"{kind}, want it rejected: {msg}")
        else:
            row = ("FAIL", "the validator did not report this component")
        rows.append(Row(name, f"rejected by {VALIDATOR}", *row, []))
    return unexpected, rows


def _skip(items, why: str) -> list[Row]:
    return [Row(i.name, "expect_invalid", "skipped", why, []) for i in items]


def _src(res: Resolved, settings) -> Path | None:
    src = board_dir(settings, res.board.name) / "apps" / "src" / "python"
    return src if (src / "pyproject.toml").is_file() else None


def scorch_rows(res: Resolved, settings, host, models: Path) -> list[Row]:
    """The scorch items that must be rejected, judged with the synced tree's validator."""
    items = expected(res, "scorch")
    if not items:
        return []
    src = _src(res, settings)
    if not src:
        return _skip(items, f"apps not synced; burger sync {res.board.name}")
    got = host.run([*UV, src, VALIDATOR, models], check=False, capture=True)
    if got.returncode == 127:
        return _skip(items, "uv not found")
    unexpected, rows = judge(f"{got.stdout}{got.stderr}", {i.name: i.expect_invalid for i in items})
    if unexpected:
        rows.append(Row("-", f"{VALIDATOR} (offline)", "FAIL", unexpected[0][-200:], []))
    return rows


def app_rows(res: Resolved, settings, host, topo: dict, workdir: Path) -> list[Row]:
    """The app items that must be rejected: run the app's early stages on `topo`."""
    items = expected(res, "app")
    if not items:
        return []
    src = _src(res, settings)
    if not src:
        return _skip(items, f"apps not synced; burger sync {res.board.name}")
    rows = []
    for item in items:
        exp = {
            "metadata": {"name": res.board.exp, "annotations": {}},
            "spec": {
                "experimentName": res.board.exp,
                "baseDir": str(workdir / item.name),
                "topology": topo["spec"],
                "scenario": {"apps": [{"name": item.type, "metadata": item.config}]},
            },
        }
        for stage in ("configure", "pre-start"):
            got = host.run(
                [*UV, src, f"phenix-app-{item.type}", stage, "--dry-run"],
                check=False,
                capture=True,
                input=json.dumps(exp),
            )
            if got.returncode:
                break
        out = f"{got.stdout}{got.stderr}"
        label = f"{item.type} {stage} rejects"
        if got.returncode == 127:
            rows += _skip([item], "uv not found")
        elif not got.returncode:
            rows.append(Row(item.name, label, "FAIL", "every stage accepted the config", []))
        elif re.search(item.expect_invalid, out):
            rows.append(Row(item.name, label, "ok", "", []))
        else:
            tail = " ".join(out.split())[-200:]
            rows.append(
                Row(item.name, label, "FAIL", f"not matching /{item.expect_invalid}/: {tail}", [])
            )
    return rows
