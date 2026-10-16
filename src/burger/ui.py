import sys
from pathlib import Path

import pyfiglet
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.text import Text

from burger.checks import Row

MASCOT = r"""
         _.-~~~~-._
       .~  ~~~~~~  ~.        pompadour
      /  ~~~~~~~~~~  \
     |   .--.  .--.   |
     |   |oo|  |oo|   |
      \   '--'  '--'  /
       \    \____/   /
     ___'.  `----'  .'___
    /   \ '-.____.-' /   \
   |  _  \__/ || \__/  _  |   <- burger on a tray
   | (_)  [=======]  (_) |
   |  ||==[ BURGER ]==||  |
   |  ||  [=======]  ||  |
   '--'|  |  overalls |  |'--'
       |__|__|    |__|__|
       (___)(___) (___)(___)
"""

_STYLE = {
    "ok": "green",
    "FAIL": "red",
    "WARN": "yellow",
    "advisory-FAIL": "yellow",
    "skipped": "dim",
}


def banner(console: Console) -> None:
    console.print(pyfiglet.figlet_format("BURGER", font="standard"), style="bold red", end="")
    console.print(MASCOT, style="yellow", highlight=False, markup=False)


def results_table(rows: list[Row]) -> Table:
    table = Table("item", "check", "result", "reason")
    for r in rows:
        table.add_row(
            Text(r.item),
            Text(r.check),
            Text(r.verdict, style=_STYLE.get(r.verdict, "")),
            Text(r.reason),
        )
    return table


def setup_logging(work_dir: Path | None, verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")
    if work_dir:
        logger.add(Path(work_dir) / "burger.log", level="DEBUG")
