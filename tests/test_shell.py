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


# ---------------------------------------------------------------- site profile (git, gh, redaction)


def _site(tmp_path):
    from burger.site import load_site

    cert = tmp_path / "ca.pem"
    cert.write_text("-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n")
    cfg = tmp_path / "site.yaml"
    cfg.write_text(
        "proxy: {https: 'http://p:8080'}\n"
        f"ca_certs: [{cert}]\n"
        "mirrors: {git: {'https://github.com/': 'https://gitlab.site/gh/'}}\n"
    )
    return load_site(env={"BURGER_SITE_CONFIG": str(cfg)}, home=tmp_path)


def _capture(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_git_gets_site_options(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    settings = dataclasses.replace(Settings.from_env(tmp_path), work=tmp_path / "work")
    Host(settings, _site(tmp_path)).run(["git", "fetch", "x"])
    ((cmd, kw),) = seen
    bundle = tmp_path / "work" / "site" / "ca-bundle.pem"
    assert cmd == ["git", "-c", "url.https://gitlab.site/gh/.insteadOf=https://github.com/",
                   "-c", f"http.sslCAInfo={bundle}", "-c", "http.proxy=http://p:8080",
                   "fetch", "x"]  # fmt: skip
    assert kw["env"]["HTTPS_PROXY"] == "http://p:8080"


def test_gh_gets_env_not_args(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    settings = dataclasses.replace(Settings.from_env(tmp_path), work=tmp_path / "work")
    Host(settings, _site(tmp_path)).run(["gh", "pr", "list"])
    ((cmd, kw),) = seen
    assert cmd == ["gh", "pr", "list"]
    assert kw["env"]["https_proxy"] == "http://p:8080"
    assert kw["env"]["SSL_CERT_FILE"].endswith("ca-bundle.pem")


def test_no_site_git_unchanged(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    Host(Settings.from_env(tmp_path)).run(["git", "fetch", "x"])
    ((cmd, kw),) = seen
    assert cmd == ["git", "fetch", "x"] and "env" not in kw


def test_redacts_credentials_in_logs_and_errors(tmp_path):
    from loguru import logger

    lines = []
    sink = logger.add(lines.append, level="DEBUG", format="{message}")
    try:
        with pytest.raises(BurgerError) as err:
            Host(Settings.from_env(tmp_path)).run(["false", "https://u:tok@m.site/x"])
    finally:
        logger.remove(sink)
    assert "tok" not in str(err.value) and "***@m.site" in str(err.value)
    assert lines and not any("tok" in str(ln) for ln in lines)
