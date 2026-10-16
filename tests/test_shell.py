import dataclasses
import subprocess

import pytest

from burger.config import BurgerError, Settings
from burger.shell import Host
from tests.fakes import FakeHost


def test_settings_from_env_defaults(monkeypatch, tmp_path):
    for k in ("PHENIX_CONTAINER", "MINIMEGA_CONTAINER", "API", "PHENIX_DIR", "WORK"):
        monkeypatch.delenv(f"BURGER_{k}", raising=False)
    s = Settings.from_env(tmp_path)
    assert s.ctr == "phenix"
    assert s.mm_ctr == "minimega"
    assert s.api == "http://localhost:3000/api/v1"
    assert s.phenix_dir == "/phenix"
    assert s.work == tmp_path.resolve() / "work"


def test_settings_empty_container_means_native(monkeypatch, tmp_path):
    monkeypatch.setenv("BURGER_PHENIX_CONTAINER", "")
    assert Settings.from_env(tmp_path).ctr == ""


@pytest.mark.parametrize("ctr,want", [("phenix", ["docker", "exec", "phenix", "ls"]), ("", ["ls"])])
def test_ctr_wraps_in_docker_exec(monkeypatch, settings, ctr, want):
    seen = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )
    Host(dataclasses.replace(settings, ctr=ctr)).ctr("ls")
    assert seen == [want]


def test_run_failure_raises(settings):
    host = Host(settings)
    with pytest.raises(BurgerError, match=r"command failed \(1\)"):
        host.run(["false"])
    assert host.run(["false"], check=False).returncode == 1


def test_run_missing_binary(settings):
    assert Host(settings).run(["no-such-bin-xyz"], check=False).returncode == 127


def test_fake_host_matches_prefix():
    fake = FakeHost({"git -C r log": subprocess.CompletedProcess([], 0, "abc", "")})
    assert fake.run(["git", "-C", "r", "log"]).stdout == "abc"
    assert fake.run(["ls"]).returncode == 0
    assert fake.calls == [["git", "-C", "r", "log"], ["ls"]]
    assert fake.api("/x") is None
