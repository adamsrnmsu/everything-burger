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


def run_checks(item: Item, ctx: CheckContext) -> list[Row]:
    rows = []
    for entry in item.verify:
        ((name, spec),) = entry.items()
        if ctx.linux_only and isinstance(spec, dict) and spec.get("windows"):
            label = f"vm {spec['host']}: {spec['run']}"[:64] if name == "vm" else name
            rows.append(Row(item.name, label, "skipped", "needs the Windows nodes", []))
            continue
        if name in HANDLED_BY_VERIFY:
            continue
        if name not in CHECKS:
            raise BurgerError(f"{item.path}: unknown check '{name}' (known: {', '.join(CHECKS)})")
        try:
            r = CHECKS[name](spec, ctx)
        except Exception as e:  # a broken check is one FAIL row; the rest still run
            r = Row(item.name, name, "FAIL", f"check raised {type(e).__name__}: {e}", [])
        if item.advisory and r.verdict == "FAIL":
            r.verdict = "advisory-FAIL"
        rows.append(r)
    return rows


__all__ = ["CHECKS", "CheckContext", "Row", "run_checks"]
