"""Site profile: proxy, CA certificates and mirrors for a host behind a re-signing proxy.

The site file lives outside git: $BURGER_SITE_CONFIG, else ~/.config/burger/site.yaml.
Without one, nothing here changes what burger runs.
"""

import hashlib
import json
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
_FROM = re.compile(r"^(\s*FROM\s+)((?:--\S+\s+)*)(\S+)(.*)$", re.IGNORECASE)
_STAGE = re.compile(r"\bAS\s+(\S+)", re.IGNORECASE)
_UBUNTU = ("http://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu")


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

    def layer(self) -> str:
        """Dockerfile lines inserted after a FROM: trust the CA, set proxy and mirrors."""
        lines = []
        if self.ca_certs:
            lines.append(f"COPY burger-site.crt {CA_IN_IMAGE}")
        env = self.env(CA_IN_IMAGE)
        if env:
            lines.append("ENV " + " ".join(f"{k}={json.dumps(v)}" for k, v in env.items()))
        apt = []
        if self.ca_certs:
            apt.append(f'Acquire::https::CAInfo "{CA_IN_IMAGE}";')
        for key, scheme in (("http", "http"), ("https", "https")):
            if self.proxy.get(key):
                apt.append(f'Acquire::{scheme}::Proxy "{self.proxy[key]}";')
        if apt:
            conf = "\\n".join(apt)
            lines.append(f"RUN printf '{conf}\\n' > /etc/apt/apt.conf.d/99burger-site")
        if self.ca_certs:
            lines.append(
                "RUN command -v update-ca-certificates >/dev/null && update-ca-certificates || true"
            )
        if self.mirrors.get("apt"):
            subs = " ".join(f"-e 's#{u}#{self.mirrors['apt']}#g'" for u in _UBUNTU)
            lines.append(
                f"RUN if [ -f /etc/apt/sources.list ]; then sed -i {subs} /etc/apt/sources.list; fi"
            )
        if self.dockerfile_extra.strip():
            lines += self.dockerfile_extra.strip().splitlines()
        return "\n".join(lines)

    def tag(self) -> str:
        """12 hex of the site file and CA bytes: a site-image tag that changes with either."""
        h = hashlib.sha256(self.raw)
        for ca in self.ca_certs:
            h.update(ca.read_bytes())
        return h.hexdigest()[:12]

    def image_name(self, image: str) -> str:
        return (self.mirrors.get("registry") or "") + image


def patch_dockerfile(text: str, site: Site) -> str:
    """Insert the site layer after each FROM of an external image, point it at the
    registry mirror, and apply mirrors.urls to the original lines."""
    urls = site.mirrors.get("urls") or {}
    stages, out = set(), []
    for line in text.splitlines():
        for origin, mirror in urls.items():
            line = line.replace(origin, mirror)
        m = _FROM.match(line)
        if not m:
            out.append(line)
            continue
        head, flags, image, rest = m.groups()
        stage = _STAGE.search(rest)
        external = image.lower() != "scratch" and image not in stages
        if stage:
            stages.add(stage.group(1))
        if not external:
            out.append(line)
            continue
        out.append(f"{head}{flags}{site.image_name(image)}{rest}")
        out += site.layer().splitlines()
    return "\n".join(out) + "\n"


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
