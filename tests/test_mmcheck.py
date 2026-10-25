import dataclasses
import subprocess
import sys

import pytest

from burger import mmcheck
from burger.config import BurgerError, Settings
from tests.fakes import FakeHost

# A stand-in for the generated minimega.py: cc filter state, and #1684's
# cc_filtered as the template has it (GOOD) or without its finally (BAD).
FAKE = """
import contextlib

class minimega:
    def __init__(self, namespace):
        self.filter = None
    def cc_filter(self, filter=None):
        if filter is not None:
            self.filter = filter
            return []
        return [{"Tabular": [["", "", "", "", "", "", self.filter]] if self.filter else None}]
    def clear_cc_filter(self):
        self.filter = None
    @contextlib.contextmanager
    def cc_filtered(self, *filters):
        self.cc_filter(" ".join(filters))
        %s

def connect(namespace=None):
    return minimega(namespace)
"""
GOOD = "try:\n            yield self\n        finally:\n            self.clear_cc_filter()"
BAD = "yield self\n        self.clear_cc_filter()"


def run_check(tmp_path, body):
    (tmp_path / "minimega.py").write_text(FAKE % body)
    cmd = [sys.executable, "-c", mmcheck.CHECK, str(tmp_path), "ebwes-main", "rtu-1"]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_check_passes_when_the_filter_is_cleared(tmp_path):
    res = run_check(tmp_path, GOOD)
    assert res.returncode == 0, res.stderr
    assert "cc_filtered ok in ebwes-main" in res.stdout


def test_check_fails_when_a_raise_leaves_the_filter(tmp_path):
    res = run_check(tmp_path, BAD)
    assert res.returncode != 0
    assert "filter left after the block raised" in res.stderr


def test_mm_check_commands(tmp_path, monkeypatch):
    monkeypatch.delenv("BURGER_MINIMEGA_REPO", raising=False)
    s = dataclasses.replace(Settings.from_env(tmp_path), work=tmp_path / "work")
    repo = s.work / "repos" / "minimega"
    sha = subprocess.CompletedProcess([], 0, "abc1234\n", "")
    host = FakeHost({f"git -C {repo} rev-parse": sha}, s)

    mmcheck.mm_check(host, s, "ebwes-main", ["rtu-1", "rtu-2"])

    cmds = [" ".join(c) for c in host.calls]
    assert any("+refs/pull/1684/head:refs/burger/pr/1684" in c for c in cmds)
    assert "docker exec minimega minimega -cli" in cmds
    build = next(c for c in host.calls if c[:3] == ["docker", "run", "--rm"])
    assert "golang:1.24" in build and "archive abc1234" in build[-1]
    assert host.calls[-1][:5] == ["docker", "exec", "phenix", "python3", "-c"]
    assert host.calls[-1][-3:] == [mmcheck.STAGE, "ebwes-main", "rtu-1"]

    # the second run reuses the bindings generated for that sha
    (s.work / "cache" / "minimega-py" / "abc1234" / "minimega.py").write_text("")
    host.calls.clear()
    mmcheck.mm_check(host, s, "ebwes-main", ["rtu-1"])
    assert not any(c[:2] == ["docker", "run"] for c in host.calls)


def test_mm_check_needs_rtus(tmp_path):
    with pytest.raises(BurgerError, match="RTUs"):
        mmcheck.mm_check(FakeHost(), Settings.from_env(tmp_path), "ebwes-x", [])
