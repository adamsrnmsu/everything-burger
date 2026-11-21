from rich.console import Console

from burger import ui

STEPS = ("sync", "build", "up")


def test_step_states_and_elapsed():
    t = iter([0.0, 5.0, 5.0, 9.0, 12.0])
    v = ui.RunView(STEPS, clock=lambda: next(t))
    assert {s["state"] for s in v.steps.values()} == {"pending"}
    v.start("sync")  # t=0
    v.finish("sync")  # t=5
    v.start("build")  # t=5
    v.finish("build", False)  # t=9
    assert v.steps["sync"] | {} == {"state": "ok", "began": 0.0, "secs": 5.0}
    assert v.steps["build"]["state"] == "FAIL" and v.steps["build"]["secs"] == 4.0
    assert v.steps["up"]["state"] == "pending"


def test_log_tail_keeps_the_last_lines():
    v = ui.RunView(STEPS, tail=2)
    v.log("a\nb\n")
    v.log("c\n")
    assert list(v.lines) == ["b", "c"]


def test_falls_back_off_a_terminal(monkeypatch):
    plain = Console(force_terminal=False)
    with ui.run_view(plain, STEPS) as view:
        assert view is None
    tty = Console(force_terminal=True, width=60)
    monkeypatch.setenv("CI", "1")
    assert not ui.interactive(tty)
    monkeypatch.delenv("CI")
    monkeypatch.setenv("NO_COLOR", "1")
    assert not ui.interactive(tty)
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert not ui.interactive(Console(force_terminal=True))


def test_live_view_renders_both_panes(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    con = Console(force_terminal=True, width=60, height=24, record=True)
    with ui.run_view(con, STEPS) as view:
        view.start("sync")
        view.log("hello from the log")
        assert ui._view is view
    assert ui._view is None
    text = con.export_text()
    assert "steps" in text and "sync" in text and "hello from the log" in text


def test_log_pane_shows_the_newest_lines_on_a_short_terminal():
    v = ui.RunView(STEPS, tail=50)
    v.log("\n".join(f"line{i}" for i in range(40)))
    con = Console(width=40, height=12, record=True)
    con.print(v)
    text = con.export_text()
    assert "line39" in text and "line0\n" not in text


def test_mmss_and_stays_swapped():
    assert [ui.mmss(s) for s in (0, 5.9, 60, 125, 3725)] == [
        "0:00",
        "0:05",
        "1:00",
        "2:05",
        "62:05",
    ]
    assert ui.stays_swapped("minimega") == "minimega stays swapped"
    assert ui.stays_swapped("minimega and phenix") == "minimega and phenix stay swapped"


def rows(*verdicts):
    from burger.checks import Row

    return [Row("i", "c", v, "", []) for v in verdicts]


def test_summary_panel_box():
    p = ui.summary_panel("main", rows("ok", "ok", "FAIL"), "r.md", {"sync": 65, "up": 5}, False)
    assert (p.title, p.border_style) == ("main", "red")
    assert p.renderable.plain == (
        "2 ok   1 FAIL   \nsync 1:05 · up 0:05   total 1:10\nreport: r.md   experiment brought down"
    )
    p = ui.summary_panel("main", rows("ok", "WARN"), "r.md", left_up=True, swapped="phenix")
    assert p.border_style == "green"
    assert p.renderable.plain == (
        "1 ok   0 FAIL   1 WARN   \n"
        "report: r.md   experiment left up; phenix stays swapped until ./burger down main"
    )
    p = ui.summary_panel("main", rows("ok"), "r.md", left_up=True)  # nothing swapped to name
    assert p.renderable.plain.endswith("experiment left up")
    assert ui.summary_panel("main", [], "r.md").renderable.plain == "0 ok   0 FAIL   \nreport: r.md"


def test_results_table_styles_each_verdict():
    table = ui.results_table(rows("ok", "FAIL", "WARN", "advisory-FAIL", "skipped", "odd"))
    styles = [str(t.style) for t in table.columns[2].cells]
    assert styles == ["green", "red", "yellow", "yellow", "dim", ""]


def test_console_format_icons_by_level():
    class L:
        def __init__(self, name):
            self.name = name

    fmt = lambda name: ui._console_format({"level": L(name)})  # noqa: E731
    assert "<red>✗</red>" in fmt("ERROR") and "<red>✗</red>" in fmt("CRITICAL")
    assert "<green>✓</green>" in fmt("SUCCESS") and "<yellow>!</yellow>" in fmt("WARNING")
    assert "<dim>·</dim>" in fmt("DEBUG") and "<blue>•</blue>" in fmt("INFO")
    assert fmt("INFO").endswith(" {message}\n{exception}")


def test_spinner_in_each_display(monkeypatch):
    from contextlib import contextmanager

    seen = []
    with ui.spinner(lambda msg, *a: seen.append(("plain", msg, a))) as say:  # not a terminal
        say("waiting {}", 1)
    assert seen == [("plain", "waiting {}", (1,))]

    monkeypatch.setattr(ui, "_view", ui.RunView(STEPS))
    logged = []
    sink = ui.logger.add(lambda m: logged.append(m.record["message"]), level="INFO")
    with ui.spinner() as say:  # the two-pane view: the text goes to the log
        say("{} left", 3)
    ui.logger.remove(sink)
    assert logged == ["3 left"]
    monkeypatch.setattr(ui, "_view", None)

    class Status:
        def update(self, text):
            seen.append(("status", str(text)))

    class FakeConsole:
        def __init__(self, stderr=False):
            seen.append(("console", stderr))

        @contextmanager
        def status(self, text):
            yield Status()

    monkeypatch.setattr(ui, "Console", FakeConsole)
    monkeypatch.setattr(ui.sys.stderr, "isatty", lambda: True, raising=False)
    with ui.spinner() as say:  # a terminal: one live status line
        say("pass {}", 2)
    assert seen[-2:] == [("console", True), ("status", "pass 2")]


def lines_of(renderable, width=60, height=24):
    con = Console(width=width, height=height, record=True)
    con.print(renderable)
    return con.export_text().splitlines()


def test_step_rows_are_adjacent_and_show_their_time():
    now = [0.0]
    v = ui.RunView(STEPS, clock=lambda: now[0])
    v.start("sync")
    now[0] = 65
    v.finish("sync")
    now[0] = 70
    v.start("build")
    now[0] = 100
    shown = [(i, x.strip("│ ").split()) for i, x in enumerate(lines_of(v))]
    steps = [(i, w) for i, w in shown if w[:1] in (["✓"], ["▶"], ["·"])]
    assert [w for _, w in steps] == [["✓", "sync", "1:05"], ["▶", "build", "0:30"], ["·", "up"]]
    assert [i for i, _ in steps] == [steps[0][0] + k for k in range(3)]  # no blank padding rows


def test_summary_panel_does_not_stretch_to_the_terminal():
    p = ui.summary_panel("main", rows("ok"), "r.md")
    assert max(len(x) for x in lines_of(p, width=80)) < 80
