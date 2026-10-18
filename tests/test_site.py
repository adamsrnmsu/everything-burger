import pytest

from burger.config import BurgerError
from burger.site import load_site, redact

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
