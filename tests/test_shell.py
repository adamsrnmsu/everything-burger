import dataclasses
import subprocess

import pytest

from burger.config import GIT_SAFE, BurgerError, Settings
from burger.shell import Host
from tests.fakes import FakeHost, StrictHost


def test_settings_from_env_defaults(monkeypatch, tmp_path):
    for k in ("PHENIX_CONTAINER", "MINIMEGA_CONTAINER", "API", "PHENIX_DIR", "WORK"):
        monkeypatch.delenv(f"BURGER_{k}", raising=False)
    s = Settings.from_env(tmp_path)
    assert s.ctr == "phenix"
    assert s.mm_ctr == "minimega"
    assert s.api == "http://localhost:3000/api/v1"
    assert s.phenix_dir == "/phenix"
    assert s.work == tmp_path.resolve() / "work"


def test_settings_root_is_the_checkout_from_any_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BURGER_WORK", raising=False)
    s = Settings.from_env()
    assert (s.root / "switchboards" / "baseline.yaml").is_file()
    assert s.work == s.root / "work"


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
                   *GIT_SAFE, "fetch", "x"]  # fmt: skip
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
    assert cmd == ["git", *GIT_SAFE, "fetch", "x"] and "env" not in kw


def test_git_ignores_ctime_fsmonitor_and_auto_gc(tmp_path, monkeypatch):
    # burger-15k: macOS bumps the ctime of new files ~2s after checkout (com.apple.provenance),
    # so `git merge` -> `git stash create` died with a bare "fatal: stash failed" at a random PR;
    # core.fsmonitor=true in ~/.gitconfig also left a daemon per worktree
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[core]\n\tfsmonitor = true\n[gc]\n\tauto = 1\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    host = Host(Settings.from_env(tmp_path))
    for key, want in [
        ("core.trustctime", "false"),
        ("core.fsmonitor", "false"),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    ]:
        assert host.run(["git", "config", key], capture=True).stdout.strip() == want


def test_mm_turns_an_error_line_into_a_failure(monkeypatch, tmp_path):
    """minimega -e exits 0 when the command fails and prints `Error (host): ...` on stderr."""
    host = Host(Settings.from_env(tmp_path))
    err = "INFO got command: `namespace e capture`\nError (rig): snaplen must be from 14 to 65535\n"
    monkeypatch.setattr(host, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", err))
    assert host.mm("e", "capture").returncode == 1
    monkeypatch.setattr(
        host, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "id,path\n", "INFO ok\n")
    )
    assert host.mm("e", "capture").returncode == 0


def test_mm_sees_an_error_line_on_either_stream(monkeypatch, tmp_path):
    host = Host(Settings.from_env(tmp_path))
    for out, err in (("Error (rig): bad\n", ""), ("", "Error (rig): bad\n")):
        monkeypatch.setattr(
            host,
            "run",
            lambda cmd, out=out, err=err, **k: subprocess.CompletedProcess(cmd, 0, out, err),
        )
        assert host.mm("e", "x").returncode == 1


def test_run_leaves_output_alone_unless_asked_to_capture(settings):
    host = Host(settings)
    assert host.run(["true"]).stdout is None
    assert host.run(["echo", "hi"], capture=True).stdout == "hi\n"


def test_native_put_copies_a_directory_into_a_directory_that_exists_and_a_file(tmp_path):
    host = Host(dataclasses.replace(Settings.from_env(tmp_path), ctr=""))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    host.put(src, str(tmp_path / "dst"))  # `mkdir -p` has made dst already
    assert (tmp_path / "dst" / "a.txt").read_text() == "a"
    host.put(src / "a.txt", str(tmp_path / "x" / "y" / "b.txt"))
    assert (tmp_path / "x" / "y" / "b.txt").read_text() == "a"


def test_native_fetch_makes_the_parent_and_a_missing_source_is_false(tmp_path):
    host = Host(dataclasses.replace(Settings.from_env(tmp_path), ctr=""))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    assert host.fetch(str(src), str(tmp_path / "deep" / "er" / "dst")) is True
    assert (tmp_path / "deep" / "er" / "dst" / "a.txt").read_text() == "a"
    assert host.fetch(str(tmp_path / "nope"), str(tmp_path / "out")) is False


def test_container_fetch_answers_false_when_docker_cp_fails(tmp_path):
    host = StrictHost({"docker cp": subprocess.CompletedProcess([], 1, "", "no such")})
    assert host.fetch("/a", str(tmp_path / "d" / "x")) is False
    assert (tmp_path / "d").is_dir()
    assert StrictHost().fetch("/a", str(tmp_path / "e" / "x")) is True


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.body


def _urlopen(monkeypatch, answer):
    """Replace urlopen: record the request and timeout, then answer or raise."""
    seen = []

    def fake(req, timeout=None):
        seen.append((req, timeout))
        if isinstance(answer, Exception):
            raise answer
        return _Resp(answer)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return seen


def test_api_gets_the_json_with_the_token_header(settings, monkeypatch):
    seen = _urlopen(monkeypatch, b'{"a": 1}')
    s = dataclasses.replace(settings, token="t0k")
    assert Host(s).api("/experiments") == {"a": 1}
    req, timeout = seen[0]
    assert req.full_url == s.api + "/experiments"
    assert req.get_header("X-phenix-auth-token") == "Bearer t0k"
    assert timeout == 15


def test_api_sends_no_token_header_without_a_token(settings, monkeypatch):
    seen = _urlopen(monkeypatch, b"[]")
    assert Host(dataclasses.replace(settings, token="")).api("/x") == []
    assert seen[0][0].get_header("X-phenix-auth-token") is None


@pytest.mark.parametrize("code", [400, 404, 500])
def test_api_answers_none_for_a_missing_name(settings, monkeypatch, code):
    import urllib.error

    _urlopen(monkeypatch, urllib.error.HTTPError("u", code, "m", {}, None))
    assert Host(settings).api("/x") is None


def test_api_raises_other_http_errors(settings, monkeypatch):
    import urllib.error

    _urlopen(monkeypatch, urllib.error.HTTPError("u", 403, "m", {}, None))
    with pytest.raises(urllib.error.HTTPError):
        Host(settings).api("/x")
