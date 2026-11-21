import subprocess
from typing import ClassVar

import pytest

from burger.config import BurgerError
from burger.probes import workflow_vars
from tests.conftest import res as resolved
from tests.fakes import FakeHost
from tests.test_verify import enforce

FILE_VARS = {"BV_A": "from-file", "BV_B": "file-b"}


class FakePhenix(FakeHost):
    """phenix PR 576's variable rules, small enough to read: -e > env file (or the dir's
    .env) > shell. `bugs` switches a rule off."""

    def __init__(self, settings, bugs=()):
        super().__init__(None, settings)
        self.bugs, self.stored = set(bugs), ""

    def run(self, cmd, check=True, capture=False, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        out = lambda s="", rc=0, err="": subprocess.CompletedProcess(cmd, rc, s, err)  # noqa: E731
        if "render" in cmd:
            return self.render(cmd, out)
        if "create" in cmd:
            self.stored = cmd[cmd.index("-e") + 1].split("=", 1)[1]
            return out()
        if "get" in cmd:
            return out(f"spec:\n  cfg: {self.stored}\n")
        return out()  # setup, delete, cleanup

    def render(self, cmd, out):
        shell = dict(a.split("=", 1) for a in cmd[: cmd.index("workflow") - 1] if "=" in a)
        flags = dict(a.split("=", 1) for i, a in enumerate(cmd) if cmd[i - 1] == "-e")
        files = [a for i, a in enumerate(cmd) if cmd[i - 1] == "--env-file"]
        d = cmd[cmd.index("render") + 1]
        if d.endswith("/needs"):
            if "missing-ok" in self.bugs:
                return out("x: \n")
            return out("", 1, "no value for BV_NEED in x.yml line 1\n")
        v = dict(shell)
        if files:
            v |= FILE_VARS
        elif d.endswith("/dotenv") and "no-dotenv" not in self.bugs:
            v |= {"BV_B": "dotenv"}
        if "shell-wins" in self.bugs:
            v |= shell
        if "e-loses" in self.bugs:
            flags = {k: x for k, x in flags.items() if k not in v}
        v |= flags
        esc = "" if "esc-expanded" in self.bugs else "${BV_ESC}"
        return out(
            "# phenix-configs/topology.yml\nspec:\n"
            f"  a: {v.get('BV_A', 'none')}\n  b: {v.get('BV_B', 'none')}\n"
            f"  dflt: fallback\n  esc: {esc}\n"
        )


def run(settings, bugs=(), man=None, host=None):
    man = {"phenix": {"pr": 576}} if man is None else man
    host = host or FakePhenix(settings, bugs)
    return host, workflow_vars.run(host, settings, resolved(settings, "main"), man)


def cleaned(host):
    return any("rm" in call and "-rf" in call for call in host.calls)


def test_skipped_without_phenix_576(settings):
    host, rows = run(settings, man={})
    assert [(r.item, r.verdict) for r in rows] == [("workflow-vars", "skipped")]
    assert "PR 576" in rows[0].reason and not host.calls


def test_all_rows_pass(settings):
    host, rows = run(settings)
    assert [r.verdict for r in rows] == ["ok"] * 9, [(r.check, r.reason) for r in rows]
    assert cleaned(host)


def test_each_rule_has_a_failing_row(settings):
    failing = {
        "shell-wins": "env file beats shell",
        "e-loses": "-e beats env file",
        "no-dotenv": "dir .env read",
        "esc-expanded": "$${NAME} stays literal",
        "missing-ok": "missing variable fails",
    }
    for bug, check in failing.items():
        _, rows = run(settings, bugs=[bug])
        assert check in {r.check for r in rows if r.verdict == "FAIL"}, bug


def test_setup_failure_is_an_error_and_cleans_up(settings):
    class NoSetup(FakePhenix):
        def run(self, cmd, check=True, capture=False, **kw):
            if "sh" in cmd:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "read-only fs")
            return super().run(cmd, check, capture, **kw)

    host = NoSetup(settings)
    try:
        run(settings, host=host)
    except BurgerError as e:
        assert "read-only fs" in str(e)
    else:
        raise AssertionError("no error")
    assert cleaned(host)


def test_cleans_up_when_a_row_raises(settings):
    class Boom(FakePhenix):
        def render(self, cmd, out):
            raise RuntimeError("boom")

    host = Boom(settings)
    try:
        run(settings, host=host)
    except RuntimeError:
        pass
    assert cleaned(host)


class StrictPhenix(FakePhenix):
    """FakePhenix with the real Host's check/capture contract; cleanup commands exit 1 (they
    must not raise); `fail` replaces any command containing its key (`R:` keys: renders only)."""

    fail: ClassVar[dict] = {}

    def run(self, cmd, check=True, capture=False, **kw):
        res = super().run(cmd, check, capture, **kw)
        line = " ".join(cmd)
        if "rm" in cmd or ("config" in cmd and "delete" in cmd):
            res = subprocess.CompletedProcess(cmd, 1, "", "")
        for text, (rc, out, err) in self.fail.items():
            if text.removeprefix("R:") in line and (text[:2] != "R:" or "render" in cmd):
                res = subprocess.CompletedProcess(cmd, rc, out, err)
        return enforce(res, cmd, check, capture)


def strict(settings, **fail):
    host = type("Host", (StrictPhenix,), {"fail": fail})(settings)
    return host, workflow_vars.run(
        host, settings, resolved(settings, "main"), {"phenix": {"pr": 576}}
    )


def test_rows_under_the_real_host_contract(settings):
    host, rows = strict(settings)
    assert [r.verdict for r in rows] == ["ok"] * 9, [(r.check, r.reason) for r in rows]
    assert cleaned(host)


def test_setup_failure_message_under_the_real_host_contract(settings):
    with pytest.raises(BurgerError, match="cannot write the test files: read-only fs"):
        strict(settings, **{" sh ": (1, "", "read-only fs")})


def test_missing_variable_must_fail_and_name_the_variable(settings):
    by = {r.check: r for r in strict(settings, **{"R:/needs": (1, "", "boom")})[1]}
    assert by["missing variable fails"].verdict == "FAIL"
    by = {r.check: r for r in strict(settings, **{"R:/needs": (0, "BV_NEED is unset", "")})[1]}
    assert by["missing variable fails"].verdict == "FAIL"


def test_config_create_that_fails_is_a_failed_row_with_its_output(settings):
    rows = strict(settings, **{"config create": (1, "", "no space left")})[1]
    row = rows[-1]
    assert row.verdict == "FAIL" and row.reason == "rc=1: no space left"


def test_failed_row_shows_the_output_tail(settings):
    rows = strict(settings, **{"R:/plain": (1, "", "x" * 300 + "tail")})[1]
    assert rows[0].reason == "rc=1: " + ("x" * 300 + "tail")[-200:]
    rows = strict(settings, **{"R:/plain": (1, "from stdout", "from stderr")})[1]
    assert rows[0].reason == "rc=1: from stdout"


def test_config_get_that_fails_is_a_failed_row(settings):
    rows = strict(settings, **{"config get": (1, "", "gone")})[1]
    assert rows[-1].verdict == "FAIL" and rows[-1].reason == "rc=1: gone"
