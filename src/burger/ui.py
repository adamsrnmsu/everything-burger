import os
import sys
import time
from collections import deque
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import pyfiglet
from loguru import logger
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
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


# Console lines: time, one icon, the message. burger.log keeps loguru's full format.
_ICON = {
    "DEBUG": "<dim>·</dim>",
    "SUCCESS": "<green>✓</green>",
    "WARNING": "<yellow>!</yellow>",
    "ERROR": "<red>✗</red>",
    "CRITICAL": "<red>✗</red>",
}


def _console_format(record) -> str:
    icon = _ICON.get(record["level"].name, "<blue>•</blue>")
    return "<dim>{time:HH:mm:ss}</dim> " + icon + " {message}\n{exception}"


def mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def summary_panel(
    name: str, rows: list[Row], report, timings=None, left_up=None, swapped: bool = False
) -> Panel:
    """The end of a run in one box: verdict counts, time per step, report, and
    whether the experiment is still up (`swapped`: on the board's own minimega)."""
    counts = Text()
    for verdict, style in (("ok", "green"), ("FAIL", "red"), ("WARN", "yellow")):
        n = sum(r.verdict == verdict for r in rows)
        if n or verdict != "WARN":  # no yellow at all unless something warned
            counts.append(f"{n} {verdict}   ", style=f"bold {style}" if n else "dim")
    lines = [counts]
    if timings:
        steps = " · ".join(f"{k} {mmss(v)}" for k, v in timings.items())
        lines.append(Text(f"{steps}   total {mmss(sum(timings.values()))}", style="dim"))
    tail = f"report: {report}"
    if left_up is not None:
        tail += "   experiment left up" if left_up else "   experiment brought down"
        if left_up and swapped:
            tail += f"; minimega stays swapped until ./burger down {name}"
    lines.append(Text(tail))
    bad = any(r.verdict == "FAIL" for r in rows)
    return Panel(Text("\n").join(lines), title=name, title_align="left",
                 border_style="red" if bad else "green", expand=False)  # fmt: skip


_MARK = {"pending": ("·", "dim"), "running": ("▶", "yellow"), "ok": ("✓", "green"),
         "FAIL": ("✗", "red")}  # fmt: skip


class RunView:
    """Two panes for ./burger run: step progress on top, a log tail below."""

    def __init__(self, steps, tail: int = 50, clock=time.monotonic):
        self.steps = {n: {"state": "pending", "began": 0.0, "secs": 0.0} for n in steps}
        self.lines: deque[str] = deque(maxlen=tail)
        self.clock = clock

    def start(self, name: str) -> None:
        self.steps[name].update(state="running", began=self.clock())

    def finish(self, name: str, ok: bool = True) -> None:
        st = self.steps[name]
        st.update(state="ok" if ok else "FAIL", secs=self.clock() - st["began"])

    def log(self, msg: str) -> None:
        self.lines.extend(msg.rstrip("\n").split("\n"))

    def __rich_console__(self, console, options):
        table = Table.grid(padding=(0, 2))
        for name, st in self.steps.items():
            icon, style = _MARK[st["state"]]
            secs = self.clock() - st["began"] if st["state"] == "running" else st["secs"]
            table.add_row(
                Text(icon, style=style), name, mmss(secs) if st["state"] != "pending" else ""
            )
        room = max(1, console.height - len(self.steps) - 4)  # the pane crops from the bottom
        log = Text("\n").join(Text.from_ansi(x) for x in list(self.lines)[-room:])
        layout = Layout()
        layout.split_column(
            Layout(Panel(table, title="steps", title_align="left"), size=len(self.steps) + 2),
            Layout(Panel(log, title="log", title_align="left")),
        )
        yield layout


_view: RunView | None = None


def interactive(console: Console) -> bool:
    """A real terminal that wants fancy output: not piped, not dumb, not NO_COLOR, not CI."""
    return (console.is_terminal and not console.is_dumb_terminal
            and not os.environ.get("NO_COLOR") and not os.environ.get("CI"))  # fmt: skip


@contextmanager
def run_view(console: Console, steps):
    """Yield a RunView, live on a terminal, or None (today's plain lines) elsewhere."""
    global _view
    if not interactive(console):
        yield None
        return
    _view = RunView(steps)
    try:
        with Live(_view, console=console, refresh_per_second=4):
            yield _view
    finally:
        _view = None


def setup_logging(work_dir: Path | None, verbose: bool) -> None:
    logger.remove()
    # sys.stderr looked up per message, so a live spinner's redirect keeps log lines above it
    logger.add(
        lambda m: _view.log(m) if _view else sys.stderr.write(m),
        level="DEBUG" if verbose else "INFO",
        format=_console_format,
        colorize=sys.stderr.isatty(),
    )
    if work_dir:
        logger.add(Path(work_dir) / "burger.log", level="DEBUG")


@contextmanager
def spinner(fallback=logger.info):
    """Yield say(msg, *args) for a loop that would print a line per pass. On a
    terminal it is one live status line (the text also goes to the log file at
    debug); elsewhere each call goes to `fallback`."""
    if _view:  # the two-pane view is the live display; the text lands in its log pane
        yield lambda msg, *args: logger.info(msg, *args)
        return
    if not sys.stderr.isatty():
        yield fallback
        return
    with Console(stderr=True).status("") as status:

        def say(msg, *args):
            logger.debug(msg, *args)
            status.update(Text(msg.format(*args)))

        yield say
