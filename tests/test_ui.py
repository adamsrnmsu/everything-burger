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
