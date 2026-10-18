import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from burger import bennu
from burger.config import BurgerError, Settings
from burger.site import build_dockerfile, load_site, patch_dockerfile, redact, site_image
from tests.fakes import FakeHost

PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def write_site(tmp_path, text):
    path = tmp_path / "site.yaml"
    path.write_text(text)
    return {"BURGER_SITE_CONFIG": str(path)}


def ca(tmp_path, name="ca.pem", body=PEM):
    path = tmp_path / name
    path.write_text(body)
    return path


FULL = """
proxy: {{http: "http://p:8080", https: "http://p:8080", no_proxy: "localhost,.site"}}
ca_certs: [{ca}]
mirrors:
  registry: reg.site/
  pypi: https://pypi.site/simple
  apt: https://apt.site/ubuntu
  go: https://go.site
  git: {{"https://github.com/": "https://gitlab.site/gh/"}}
  urls: {{"https://github.com/": "https://m.site/gh/"}}
dockerfile_extra: RUN true
"""


def test_no_site_file(tmp_path):
    assert load_site(env={}, home=tmp_path) is None


def test_env_var_path_must_exist(tmp_path):
    with pytest.raises(BurgerError, match="BURGER_SITE_CONFIG"):
        load_site(env={"BURGER_SITE_CONFIG": str(tmp_path / "nope.yaml")}, home=tmp_path)


def test_default_path(tmp_path):
    cfg = tmp_path / ".config" / "burger" / "site.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("proxy: {https: http://p:8080}\n")
    site = load_site(env={}, home=tmp_path)
    assert site.proxy == {"https": "http://p:8080"}
    assert site.path == cfg


def test_unknown_fields_rejected(tmp_path):
    with pytest.raises(BurgerError, match="'mirror'"):
        load_site(env=write_site(tmp_path, "mirror: {}\n"), home=tmp_path)
    with pytest.raises(BurgerError, match="'pypy'"):
        load_site(env=write_site(tmp_path, "mirrors: {pypy: x}\n"), home=tmp_path)
    with pytest.raises(BurgerError, match="'htps'"):
        load_site(env=write_site(tmp_path, "proxy: {htps: x}\n"), home=tmp_path)


def test_ca_missing_or_not_pem(tmp_path):
    good = ca(tmp_path)
    with pytest.raises(BurgerError, match=r"ca_certs\[0\].*missing\.pem"):
        load_site(env=write_site(tmp_path, f"ca_certs: [{tmp_path}/missing.pem]\n"), home=tmp_path)
    bad = ca(tmp_path, "bad.pem", "not a cert\n")
    with pytest.raises(BurgerError, match=r"ca_certs\[1\].*bad\.pem"):
        load_site(env=write_site(tmp_path, f"ca_certs: [{good}, {bad}]\n"), home=tmp_path)


def test_bundle_concatenates(tmp_path):
    a = ca(tmp_path, "a.pem", PEM.replace("MIIB", "AAAA"))
    b = ca(tmp_path, "b.pem", PEM.replace("MIIB", "BBBB"))
    site = load_site(env=write_site(tmp_path, f"ca_certs: [{a}, {b}]\n"), home=tmp_path)
    bundle = site.bundle(tmp_path / "work")
    assert bundle == tmp_path / "work" / "site" / "ca-bundle.pem"
    text = bundle.read_text()
    assert text.count("BEGIN CERTIFICATE") == 2 and "AAAA" in text and "BBBB" in text
    nosite = load_site(env=write_site(tmp_path, "proxy: {http: x}\n"), home=tmp_path)
    assert nosite.bundle(tmp_path / "work") is None


def test_env_values(tmp_path):
    site = load_site(env=write_site(tmp_path, FULL.format(ca=ca(tmp_path))), home=tmp_path)
    env = site.env("/ca.crt")
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        assert env[name] == env[name.lower()] == "http://p:8080"
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,.site"
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT", "CURL_CA_BUNDLE",
                 "GIT_SSL_CAINFO", "NODE_EXTRA_CA_CERTS"):  # fmt: skip
        assert env[name] == "/ca.crt"
    assert env["UV_NATIVE_TLS"] == "1"
    assert env["PIP_INDEX_URL"] == env["UV_INDEX_URL"] == env["UV_DEFAULT_INDEX"]
    assert env["PIP_INDEX_URL"] == "https://pypi.site/simple"
    assert env["GOPROXY"] == "https://go.site"
    assert "GOSUMDB" not in env
    off = load_site(env=write_site(tmp_path, 'mirrors: {gosumdb: "off"}\n'), home=tmp_path)
    assert off.env("/ca.crt") == {"GOSUMDB": "off"}


def test_redact():
    assert redact("https://u:p@h/x and http://h2/") == "https://***@h/x and http://h2/"


FIXTURE = Path(__file__).parent / "fixtures" / "bennu-test.Dockerfile"


def full_site(tmp_path, extra=""):
    return load_site(env=write_site(tmp_path, FULL.format(ca=ca(tmp_path)) + extra), home=tmp_path)


def test_layer_order_and_values(tmp_path):
    lines = full_site(tmp_path).layer().splitlines()
    first = {key: next(i for i, ln in enumerate(lines) if ln.startswith(key))
             for key in ("COPY burger-site.crt", "ENV ", "RUN ")}  # fmt: skip
    assert first["COPY burger-site.crt"] < first["ENV "] < first["RUN "]
    text = "\n".join(lines)
    assert 'Acquire::https::CAInfo "/usr/local/share/ca-certificates/burger-site.crt";' in text
    assert 'HTTPS_PROXY="http://p:8080"' in text
    assert "apt.site/ubuntu" in text
    assert lines[-1] == "RUN true"  # dockerfile_extra last
    proxy_only = load_site(
        env=write_site(tmp_path, "proxy: {https: http://p:8080}\n"), home=tmp_path
    )
    assert "COPY" not in proxy_only.layer() and "SSL_CERT_FILE" not in proxy_only.layer()


def test_patch_bennu_fixture(tmp_path):
    site = full_site(tmp_path)
    original = FIXTURE.read_text()
    patched = patch_dockerfile(original, site)
    plines = patched.splitlines()
    assert plines[1] == "FROM reg.site/ubuntu:22.04"  # after the leading comment
    assert plines[2 : 2 + len(site.layer().splitlines())] == site.layer().splitlines()
    runs = [ln for ln in original.splitlines() if ln.startswith("RUN ")]
    tail = plines[2 + len(site.layer().splitlines()) :]
    assert [ln for ln in tail if ln.startswith("RUN ")] == [
        r.replace("https://github.com/", "https://m.site/gh/") for r in runs
    ]
    assert "https://m.site/gh/zeromq/libzmq/releases" in patched
    assert "https://raw.githubusercontent.com/golangci" in patched  # not under github.com/


def test_patch_multistage_and_flags(tmp_path):
    site = full_site(tmp_path)
    text = "FROM --platform=linux/amd64 golang:1.24 AS build\nRUN make\nFROM build\nFROM scratch\n"
    out = patch_dockerfile(text, site).splitlines()
    layer = site.layer().splitlines()
    assert out[0] == "FROM --platform=linux/amd64 reg.site/golang:1.24 AS build"
    assert out[1 : 1 + len(layer)] == layer
    assert out[1 + len(layer) :] == ["RUN make", "FROM build", "FROM scratch"]


def test_tag_changes_with_site_and_ca(tmp_path):
    cert = ca(tmp_path)
    env = write_site(tmp_path, f"ca_certs: [{cert}]\n")
    t1 = load_site(env=env, home=tmp_path).tag()
    assert len(t1) == 12 and int(t1, 16) >= 0
    cert.write_text(PEM.replace("MIIB", "ZZZZ"))
    t2 = load_site(env=env, home=tmp_path).tag()
    write_site(tmp_path, f"ca_certs: [{cert}]\nproxy: {{http: x}}\n")
    t3 = load_site(env=env, home=tmp_path).tag()
    assert len({t1, t2, t3}) == 3


def test_image_name(tmp_path):
    assert full_site(tmp_path).image_name("ubuntu:22.04") == "reg.site/ubuntu:22.04"
    plain = load_site(env=write_site(tmp_path, "proxy: {http: x}\n"), home=tmp_path)
    assert plain.image_name("ubuntu:22.04") == "ubuntu:22.04"


# ---------------------------------------------------------------- docker steps (Task 3)


def _host(tmp_path, site=None, responses=None):
    settings = Settings.from_env(tmp_path)
    settings = dataclasses.replace(settings, work=tmp_path / "work")
    return FakeHost(responses or {}, settings, site=site)


def test_no_site_commands_unchanged(tmp_path, monkeypatch):
    """Without a site file, build_bennu and bennu_check issue exactly today's commands."""
    monkeypatch.delenv("BURGER_BENNU_REPO", raising=False)
    out = tmp_path / "out"
    (out / "wheels").mkdir(parents=True)
    (out / "pybennu-6.0.0-py3-none-any.whl").write_text("")
    version = subprocess.CompletedProcess([], 0, "6.0.1\n", "")
    host = _host(tmp_path, responses={"docker run --rm -v": version})
    wt = tmp_path / "wt"
    bennu.build_bennu(host, wt, out, {"bennu": {}}, ["deb", "pybennu"], ["loguru>=0.7,<1"])
    bennu.bennu_check(host, wt)
    T, R, W = str(tmp_path), str(tmp_path.resolve()), str((tmp_path / "work").resolve())
    uid = f"{os.getuid()}:{os.getgid()}"
    img = "burger-bennu-build:22.04"
    assert host.calls == [
        ["docker", "build", "-t", img, f"{T}/wt/docker/test"],
        ["docker", "run", "--rm", "-v", f"{R}/wt:{R}/wt:ro", "-v", f"{T}/out:/out", img,
         "bash", "-ec",
         f"cp -r {R}/wt/src/pybennu /tmp/pyb && pip3 install -q uv && uv build -q --wheel "
         f"-o /tmp/w /tmp/pyb && cp /tmp/w/pybennu-*.whl /out/ && chown {uid} /out/pybennu-*.whl"],
        ["docker", "run", "--rm", "-v", f"{T}/out:/out", img, "bash", "-ec",
         f"pip3 wheel -q --no-deps -w /out/wheels 'loguru>=0.7,<1' && chown -R {uid} /out/wheels"],
        ["docker", "run", "--rm", "-v", f"{W}/repos/bennu:{W}/repos/bennu:ro",
         "-v", f"{R}/wt:{R}/wt:ro", "-v", f"{T}/out:/out", img, "bash", "-ec",
         "git config --global --add safe.directory '*' && mkdir -p /tmp/b && cd /tmp/b && "
         f"cmake -DBUILD_TESTS=OFF {R}/wt >/dev/null && "
         f'make -j"$(nproc)" package >/dev/null && cp bennu-*.deb /out/bennu.deb && '
         f"chown {uid} /out/bennu.deb"],
        ["docker", "run", "--rm", "-v", f"{T}/out:/out", img, "dpkg-deb", "-f",
         "/out/bennu.deb", "Version"],
        ["bash", f"{T}/wt/docker/test/run.sh", "-s", T + "/wt", "build", "smoke", "ctest",
         "pytest", "gotest"],
    ]  # fmt: skip
    nosite = _host(tmp_path)
    assert site_image(nosite, "golang:1.24") == "golang:1.24" and nosite.calls == []


def test_site_image_reuse_and_rebuild(tmp_path):
    site = full_site(tmp_path)
    tag = f"burger-site/golang_1.24:{site.tag()}"
    present = _host(
        tmp_path, site, {f"docker image inspect {tag}": subprocess.CompletedProcess([], 0)}
    )
    assert site_image(present, "golang:1.24") == tag
    assert not [c for c in present.calls if c[:2] == ["docker", "build"]]
    missing = _host(tmp_path, site, {"docker image inspect": subprocess.CompletedProcess([], 1)})
    assert site_image(missing, "golang:1.24") == tag
    builds = [c for c in missing.calls if c[:2] == ["docker", "build"]]
    assert len(builds) == 1 and builds[0][builds[0].index("-t") + 1] == tag
    ctx = Path(builds[0][-1])
    assert ctx.joinpath("Dockerfile").read_text().splitlines()[0] == "FROM reg.site/golang:1.24"
    assert ctx.joinpath("burger-site.crt").read_text().startswith("-----BEGIN CERTIFICATE-----")
    changed = load_site(
        env=write_site(tmp_path, FULL.format(ca=ca(tmp_path)) + "# v2\n"), home=tmp_path
    )
    assert changed.tag() != site.tag()


def test_build_dockerfile_with_site(tmp_path):
    site = full_site(tmp_path)
    context = tmp_path / "bennu" / "docker" / "test"
    context.mkdir(parents=True)
    context.joinpath("Dockerfile").write_text(FIXTURE.read_text())
    context.joinpath("run.sh").write_text("#!/bin/sh\n")
    before = {p.name: p.read_bytes() for p in context.iterdir()}
    host = _host(tmp_path, site)
    build_dockerfile(host, context, "burger-bennu-build:22.04")
    (cmd,) = host.calls
    assert cmd[:2] == ["docker", "build"]
    assert "HTTPS_PROXY=http://p:8080" in cmd and "https_proxy=http://p:8080" in cmd
    assert cmd[cmd.index("-t") + 1] == "burger-bennu-build:22.04"
    ctx = Path(cmd[-1])
    assert ctx != context
    assert ctx.joinpath("Dockerfile").read_text().splitlines()[1] == "FROM reg.site/ubuntu:22.04"
    assert ctx.joinpath("burger-site.crt").is_file() and ctx.joinpath("run.sh").is_file()
    assert {p.name: p.read_bytes() for p in context.iterdir()} == before
    nosite = _host(tmp_path)
    build_dockerfile(nosite, context, "x:1")
    assert nosite.calls == [["docker", "build", "-t", "x:1", str(context)]]


def test_bennu_check_prebuilds_verify_image_with_site(tmp_path):
    host = _host(tmp_path, full_site(tmp_path))
    wt = tmp_path / "wt"
    (wt / "docker" / "test").mkdir(parents=True)
    (wt / "docker" / "test" / "Dockerfile").write_text("FROM ubuntu:22.04\n")
    bennu.bennu_check(host, wt)
    assert host.calls[0][:2] == ["docker", "build"]
    assert host.calls[0][host.calls[0].index("-t") + 1] == "bennu-verify:22.04-lint"
    assert host.calls[-1][:2] == ["bash", str(wt / "docker" / "test" / "run.sh")]
