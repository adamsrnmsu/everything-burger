from collections.abc import Callable

from burger.checks import files, hook, pcap
from burger.checks.base import CheckContext, Row
from burger.config import BurgerError
from burger.items import Item

CHECKS: dict[str, Callable[[dict, CheckContext], Row]] = {
    "files": files.files,
    "json": files.json_check,
    "csv": files.csv_check,
    "log": files.log,
    "pcap": pcap.pcap,
    "python": hook.python,
}
HANDLED_BY_VERIFY = ("vm",)


def _windows_skip(item: Item, name: str, spec, ctx: CheckContext) -> Row | None:
    """A skipped row for a check that needs the Windows nodes this route lacks."""
    if not (
        ctx.route.skips_windows(ctx.linux_only) and isinstance(spec, dict) and spec.get("windows")
    ):
        return None
    label = f"vm {spec['host']}: {spec['run']}"[:64] if name == "vm" else name
    return Row(item.name, label, "skipped", "needs the Windows nodes", [])


def _run_one(item: Item, name: str, spec, ctx: CheckContext) -> Row:
    if name not in CHECKS:
        raise BurgerError(f"{item.path}: unknown check '{name}' (known: {', '.join(CHECKS)})")
    try:
        r = CHECKS[name](spec, ctx)
    except Exception as e:  # a broken check is one FAIL row; the rest still run
        r = Row(item.name, name, "FAIL", f"check raised {type(e).__name__}: {e}", [])
    if item.advisory and r.verdict == "FAIL":
        r.verdict = "advisory-FAIL"
    return r


def run_checks(item: Item, ctx: CheckContext) -> list[Row]:
    rows = []
    for entry in item.verify:
        ((name, spec),) = entry.items()
        if skipped := _windows_skip(item, name, spec, ctx):
            rows.append(skipped)
        elif name not in HANDLED_BY_VERIFY:
            rows.append(_run_one(item, name, spec, ctx))
    return rows


__all__ = ["CHECKS", "CheckContext", "Row", "run_checks"]
