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
HANDLED_BY_VERIFY = ("status", "vm")


def run_checks(item: Item, ctx: CheckContext) -> list[Row]:
    rows = []
    for entry in item.verify:
        ((name, spec),) = entry.items()
        if name in HANDLED_BY_VERIFY:
            continue
        if name not in CHECKS:
            raise BurgerError(f"{item.path}: unknown check '{name}' (known: {', '.join(CHECKS)})")
        r = CHECKS[name](spec, ctx)
        if item.advisory and r.verdict == "FAIL":
            r.verdict = "advisory-FAIL"
        rows.append(r)
    return rows


__all__ = ["CHECKS", "CheckContext", "Row", "run_checks"]
