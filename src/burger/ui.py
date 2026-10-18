import sys
from importlib.resources import files
from pathlib import Path

import pyfiglet
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.text import Text

from burger.checks import Row

# Optional truecolor ASCII art (scripts/make_mascot.py), shipped as package data so
# nothing extra is installed to draw it. Without the file, the banner is the figlet.
_MASCOT_FILE = files("burger") / "assets" / "mascot.ans"
MASCOT = Text.from_ansi(_MASCOT_FILE.read_text()) if _MASCOT_FILE.is_file() else None

_STYLE = {
    "ok": "green",
    "FAIL": "red",
    "WARN": "yellow",
    "advisory-FAIL": "yellow",
    "skipped": "dim",
}


def banner(console: Console) -> None:
    console.print(pyfiglet.figlet_format("BURGER", font="standard"), style="bold red", end="")
    if MASCOT is not None:
        console.print(MASCOT)


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
