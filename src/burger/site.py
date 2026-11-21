"""Site profile: proxy, CA certificates and mirrors for a host behind a re-signing proxy.

The site file lives outside git: $BURGER_SITE_CONFIG, else ~/.config/burger/site.yaml.
Without one, nothing here changes what burger runs.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import urllib.error
import urllib.parse
import urllib.request
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
_UBUNTU = (
    "http://archive.ubuntu.com/ubuntu",
    "http://security.ubuntu.com/ubuntu",
    "http://ports.ubuntu.com/ubuntu-ports",  # arm64
)


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

    def bundle(self, work: Path) -> Path | None:
        """All CA files as one PEM bundle under work/site/, or None without ca_certs."""
        if not self.ca_certs:
            return None
        path = work / "site" / "ca-bundle.pem"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(p.read_text().rstrip("\n") + "\n" for p in self.ca_certs)
        if not path.exists() or path.read_text() != text:  # git and gh call this per command
            path.write_text(text)
        path.chmod(0o644)  # COPY keeps the mode; apt (_apt) and -u uid builds must read it
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
            quoted = {
                k: json.dumps(v).replace("$", "\\$") for k, v in env.items()
            }  # no $ expansion
            lines.append("ENV " + " ".join(f"{k}={v}" for k, v in quoted.items()))
        lines += self._apt_lines()
        if self.dockerfile_extra.strip():
            lines += self.dockerfile_extra.strip().splitlines()
        return "\n".join(lines)

    def _apt_lines(self) -> list[str]:
        """Dockerfile lines for apt: the CA and proxy conf, the CA refresh, the mirror rewrite."""
        lines = []
        apt = []
        if self.ca_certs:
            apt.append(f'Acquire::https::CAInfo "{CA_IN_IMAGE}";')
        for key, scheme in (("http", "http"), ("https", "https")):
            if self.proxy.get(key):
                apt.append(f'Acquire::{scheme}::Proxy "{self.proxy[key]}";')
        if apt:
            conf = "\\n".join(apt).replace("%", "%%")  # printf's format: a URL's %40 is literal
            fmt = shlex.quote(conf + "\\n")
            lines.append(f"RUN printf {fmt} > /etc/apt/apt.conf.d/99burger-site")
        if self.ca_certs:
            lines.append(
                "RUN command -v update-ca-certificates >/dev/null && update-ca-certificates || true"
            )
        if self.mirrors.get("apt"):
            new = re.sub(r"([&#\\])", r"\\\1", self.mirrors["apt"])  # sed replacement specials
            subs = " ".join("-e " + shlex.quote(f"s#{u}#{new}#g") for u in _UBUNTU)
            lines.append(
                f"RUN if [ -f /etc/apt/sources.list ]; then sed -i {subs} /etc/apt/sources.list; fi"
            )
        return lines

    def tag(self) -> str:
        """12 hex of what a site image is made of (the expanded layer, the registry and the
        CA bytes), so a change to any of them, or to a ${VAR} behind them, rebuilds it."""
        h = hashlib.sha256((self.layer() + "\n" + self.image_name("")).encode())
        for ca in self.ca_certs:
            h.update(ca.read_bytes())
        return h.hexdigest()[:12]

    def git_args(self, bundle: Path | None) -> list[str]:
        """git -c options: mirror rewrites, the CA bundle and the proxy, per command."""
        args = []
        for origin, mirror in (self.mirrors.get("git") or {}).items():
            args += ["-c", f"url.{mirror}.insteadOf={origin}"]
        if bundle:
            args += ["-c", f"http.sslCAInfo={bundle}"]
        proxy = self.proxy.get("https") or self.proxy.get("http")
        if proxy:
            args += ["-c", f"http.proxy={proxy}"]
        return args

    def host_env(self, bundle: Path | None) -> dict[str, str]:
        """Environment for git and gh on the host: the proxy and the CA bundle."""
        env = {k: v for k, v in self.env("").items() if k.lower().endswith("_proxy")}
        if bundle:
            env.update(SSL_CERT_FILE=str(bundle), GIT_SSL_CAINFO=str(bundle))
        return env

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
        external = image.lower() != "scratch" and image.lower() not in stages  # Docker ignores case
        if stage:
            stages.add(stage.group(1).lower())
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


def _site_path(env: Mapping[str, str], home: Path | None) -> Path | None:
    if env.get("BURGER_SITE_CONFIG"):
        path = Path(env["BURGER_SITE_CONFIG"]).expanduser()
        if not path.is_file():
            raise BurgerError(f"BURGER_SITE_CONFIG points at {path}, which does not exist")
        return path
    path = (home or Path.home()) / ".config" / "burger" / "site.yaml"
    return path if path.is_file() else None


def _mirrors(path: Path, doc: dict) -> dict:
    mirrors = doc.get("mirrors") or {}
    _check_fields(path, "mirrors", mirrors, MIRROR_FIELDS)
    for key in ("git", "urls"):
        if not isinstance(mirrors.get(key) or {}, dict):
            raise BurgerError(f"{path}: mirrors.{key} must map origin URLs to mirror URLs")
    mirrors = {k: v if isinstance(v, dict) or v is None else str(v) for k, v in mirrors.items()}
    if mirrors.get("registry") and not str(mirrors["registry"]).endswith("/"):
        mirrors = {**mirrors, "registry": f"{mirrors['registry']}/"}  # a prefix: reg/x + /ubuntu
    return mirrors


def _ca_certs(path: Path, spec) -> tuple[Path, ...]:
    if not isinstance(spec or [], list):
        raise BurgerError(f"{path}: ca_certs must be a list of PEM file paths")
    cas = []
    for i, ca in enumerate(spec or []):
        ca = Path(str(ca)).expanduser()
        if not ca.is_file():
            raise BurgerError(f"{path}: ca_certs[{i}] {ca} does not exist")
        if _PEM not in ca.read_text(errors="replace"):
            raise BurgerError(f"{path}: ca_certs[{i}] {ca} holds no PEM certificate")
        cas.append(ca)
    return tuple(cas)


def load_site(env: Mapping[str, str] | None = None, home: Path | None = None) -> Site | None:
    env = os.environ if env is None else env
    if (path := _site_path(env, home)) is None:
        return None
    doc, unset = load_yaml(path, SITE_FIELDS, env)
    if unset:
        raise BurgerError(f"{path}: unset environment variables: {', '.join(sorted(set(unset)))}")
    proxy = doc.get("proxy") or {}
    _check_fields(path, "proxy", proxy, PROXY_FIELDS)
    mirrors = _mirrors(path, doc)
    return Site(
        path=path,
        proxy={k: str(v) for k, v in proxy.items()},
        ca_certs=_ca_certs(path, doc.get("ca_certs")),
        mirrors=mirrors,
        dockerfile_extra=str(doc.get("dockerfile_extra") or ""),
    )


def _context(host, name: str, source: Path | None = None) -> Path:
    """A fresh build directory under <work>/site/build/, seeded from source and the CA."""
    ctx = host.s.work / "site" / "build" / name
    shutil.rmtree(ctx, ignore_errors=True)
    if source:
        shutil.copytree(source, ctx)
    ctx.mkdir(parents=True, exist_ok=True)
    bundle = host.site.bundle(host.s.work)
    if bundle:
        shutil.copy(bundle, ctx / "burger-site.crt")
    return ctx


def site_image(host, image: str) -> str:
    """The image to run: `image` itself without a site, else its site-layer image,
    built only when that tag (which hashes the site file and CAs) is not there yet."""
    site = host.site
    if site is None:
        return image
    tag = f"burger-site/{re.sub(r'[/:]', '_', image)}:{site.tag()}"
    if host.run(["docker", "image", "inspect", tag], check=False, capture=True).returncode:
        ctx = _context(host, tag.split("/", 1)[1].replace(":", "-"))
        (ctx / "Dockerfile").write_text(f"FROM {site.image_name(image)}\n{site.layer()}\n")
        host.run(["docker", "build", *_build_args(site), "-t", tag, ctx])
    return tag


def _build_args(site: Site) -> list[str]:
    args = []
    for key, value in site.env("").items():
        if key.lower() in ("http_proxy", "https_proxy", "no_proxy"):
            args += ["--build-arg", f"{key}={value}"]
    return args


def retarget_first_from(text: str, image: str) -> str:
    """The Dockerfile with its first FROM pointed at `image` (flags and stage name kept)."""
    lines, done = [], False
    for line in text.splitlines():
        m = None if done else _FROM.match(line)
        lines.append(f"{m[1]}{m[2]}{image}{m[4]}" if m else line)
        done = done or bool(m)
    return "\n".join(lines) + "\n"


def build_dockerfile(
    host,
    context: Path,
    tag: str,
    platform: str | None = None,
    dockerfile: str = "Dockerfile",
    build_args: tuple[str, ...] = (),
) -> None:
    """docker build of a Dockerfile we do not own: as is without a site, else from a
    patched copy of its context (site layer after each FROM, mirrors.urls applied).
    `dockerfile` is its path inside the context."""
    site = host.site
    plat = ["--platform", platform] if platform else []
    plat += [a for kv in build_args for a in ("--build-arg", kv)]
    ctx = context if site is None else _context(host, re.sub(r"[/:]", "-", tag), context)
    plat += [] if dockerfile == "Dockerfile" else ["-f", ctx / dockerfile]
    if site is None:
        host.run(["docker", "build", *plat, "-t", tag, ctx])
        return
    path = ctx / dockerfile
    path.write_text(patch_dockerfile(path.read_text(), site))
    host.run(["docker", "build", *plat, *_build_args(site), "-t", tag, ctx])


def probe(
    url: str, site: Site, bundle: Path | None, timeout: int = 15, strict: bool = False
) -> str | None:
    """GET url through the site proxy, trusting the site CA. None on any answer below 500,
    or below 400 when strict (a URL that must exist)."""
    proxies = {k: v for k, v in site.proxy.items() if k in ("http", "https")}
    host = urllib.parse.urlsplit(url).hostname or ""
    if urllib.request.proxy_bypass_environment(host, {"no": site.proxy.get("no_proxy", "")}):
        proxies = {}
    context = ssl.create_default_context(cafile=str(bundle) if bundle else None)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies), urllib.request.HTTPSHandler(context=context)
    )
    try:
        with opener.open(url, timeout=timeout):
            return None
    except urllib.error.HTTPError as e:
        return None if e.code < (400 if strict else 500) else f"HTTP {e.code}"
    except (OSError, ValueError) as e:
        return redact(str(getattr(e, "reason", e)))
