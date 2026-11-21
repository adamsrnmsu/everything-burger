"""Host-side probes: checks a board names in `probes:` that drive the running experiment
directly, from the phēnix container or the minimega container, outside SCORCH."""

from collections.abc import Callable

from loguru import logger

from burger.checks import Row
from burger.config import BurgerError
from burger.experiment import not_running
from burger.probes import mm_bindings, mm_capture, workflow_vars

# host, settings, resolved board, manifest -> rows (each row's item is the probe name)
PROBES: dict[str, Callable] = {
    "mm-bindings": mm_bindings.run,
    "mm-capture": mm_capture.run,
    "workflow-vars": workflow_vars.run,
}


def run(host, s, res, man, names: list[str] | None = None) -> list[Row]:
    names = res.board.probes if names is None else names
    if not names:
        raise BurgerError(f"board has no probes; name some (known: {', '.join(PROBES)})")
    if bad := [n for n in names if n not in PROBES]:
        raise BurgerError(f"unknown probe '{bad[0]}' (known: {', '.join(PROBES)})")
    if down := not_running(host, res.board.exp):
        return [Row(n, "probe", "skipped", f"experiment not running: {down}", []) for n in names]
    rows: list[Row] = []
    for n in names:
        try:
            rows += PROBES[n](host, s, res, man)
        except Exception as e:  # a broken probe is a FAIL row, not a traceback
            logger.opt(exception=True).debug("probe {} raised", n)
            rows.append(Row(n, "probe", "FAIL", f"{type(e).__name__}: {e}", []))
    return rows
