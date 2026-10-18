"""Site profile: proxy, CA certificates and mirrors for a host behind a re-signing proxy.

The site file lives outside git: $BURGER_SITE_CONFIG, else ~/.config/burger/site.yaml.
Without one, nothing here changes what burger runs.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from burger.config import BurgerError
from burger.loader import load_yaml

SITE_FIELDS = {"proxy", "ca_certs", "mirrors", "dockerfile_extra"}
PROXY_FIELDS = {"http", "https", "no_proxy"}
MIRROR_FIELDS = {"registry", "pypi", "apt", "go", "gosumdb", "git", "urls"}
CA_IN_IMAGE = "/usr/local/share/ca-certificates/burger-site.crt"
CA_VARS = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "PIP_CERT",
    "CURL_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "NODE_EXTRA_CA_CERTS",
)
_PEM = "-----BEGIN CERTIFICATE-----"
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


def redact(text: str) -> str:
    """Hide user:pass@ in any URL inside text."""
    return _USERINFO.sub(r"\1***@", text)


@dataclass(frozen=True)
class Site:
    path: Path
    proxy: dict
    ca_certs: tuple
    mirrors: dict
    dockerfile_extra: str
    raw: bytes

    def bundle(self, work: Path) -> Path | None:
        """All CA files as one PEM bundle under work/site/, or None without ca_certs."""
        if not self.ca_certs:
            return None
        path = work / "site" / "ca-bundle.pem"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(p.read_text().rstrip("\n") + "\n" for p in self.ca_certs))
        return path

    def env(self, ca_path: str) -> dict[str, str]:
        """Proxy, CA and mirror variables, with the CA bundle at ca_path."""
        env = {}
        for key, name in (
            ("http", "HTTP_PROXY"),
            ("https", "HTTPS_PROXY"),
            ("no_proxy", "NO_PROXY"),
        ):
            if self.proxy.get(key):
                env[name] = env[name.lower()] = self.proxy[key]
        if self.ca_certs:
            env.update(dict.fromkeys(CA_VARS, ca_path), UV_NATIVE_TLS="1")
        m = self.mirrors
        if m.get("pypi"):
            env.update(
                dict.fromkeys(("PIP_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX"), m["pypi"])
            )
        if m.get("go"):
            env["GOPROXY"] = m["go"]
        if m.get("gosumdb"):
            env["GOSUMDB"] = m["gosumdb"]
        return env


def _check_fields(path: Path, where: str, doc: dict, allowed: set[str]) -> None:
    if not isinstance(doc, dict):
        raise BurgerError(f"{path}: {where} must be a mapping")
    for key in doc:
        if key not in allowed:
            raise BurgerError(
                f"{path}: unknown field '{key}' in {where} (allowed: {', '.join(sorted(allowed))})"
            )


def load_site(env: Mapping[str, str] | None = None, home: Path | None = None) -> Site | None:
    env = os.environ if env is None else env
    if env.get("BURGER_SITE_CONFIG"):
        path = Path(env["BURGER_SITE_CONFIG"]).expanduser()
        if not path.is_file():
            raise BurgerError(f"BURGER_SITE_CONFIG points at {path}, which does not exist")
    else:
        path = (home or Path.home()) / ".config" / "burger" / "site.yaml"
        if not path.is_file():
            return None
    doc, unset = load_yaml(path, SITE_FIELDS, env)
    if unset:
        raise BurgerError(f"{path}: unset environment variables: {', '.join(sorted(set(unset)))}")
    proxy = doc.get("proxy") or {}
    mirrors = doc.get("mirrors") or {}
    _check_fields(path, "proxy", proxy, PROXY_FIELDS)
    _check_fields(path, "mirrors", mirrors, MIRROR_FIELDS)
    for key in ("git", "urls"):
        if not isinstance(mirrors.get(key) or {}, dict):
            raise BurgerError(f"{path}: mirrors.{key} must map origin URLs to mirror URLs")
    cas = []
    for i, ca in enumerate(doc.get("ca_certs") or []):
        ca = Path(str(ca)).expanduser()
        if not ca.is_file():
            raise BurgerError(f"{path}: ca_certs[{i}] {ca} does not exist")
        if _PEM not in ca.read_text(errors="replace"):
            raise BurgerError(f"{path}: ca_certs[{i}] {ca} holds no PEM certificate")
        cas.append(ca)
    return Site(
        path=path,
        proxy={k: str(v) for k, v in proxy.items()},
        ca_certs=tuple(cas),
        mirrors=mirrors,
        dockerfile_extra=str(doc.get("dockerfile_extra") or ""),
        raw=path.read_bytes(),
    )
