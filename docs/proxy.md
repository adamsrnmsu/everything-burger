# Running behind a proxy

At a site where outbound traffic goes through a proxy that re-signs TLS with
a site CA, and packages come from internal mirrors, give burger a **site
file**. burger reads it on every command and uses it on each docker build and
run, and on git and `gh`. Without a site file nothing changes.

burger looks for it at `$BURGER_SITE_CONFIG` (which must then exist), and
otherwise at `~/.config/burger/site.yaml`. It never goes in git.

## The site file

Every field is optional; unknown fields are rejected by name. `${VAR}` and
`${VAR:-default}` read the environment.

```yaml
proxy:
  http: http://proxy.site:8080
  https: http://proxy.site:8080
  no_proxy: localhost,127.0.0.1,.site.internal
ca_certs:                       # PEM files, all trusted (burger joins them into one bundle)
  - /etc/pki/site/root-ca.pem
  - /etc/pki/site/issuing-ca.pem
mirrors:
  registry: artifactory.site/docker-remote/          # prefix for every image burger pulls
  pypi: https://artifactory.site/api/pypi/pypi/simple
  apt: https://artifactory.site/ubuntu                # replaces archive/security.ubuntu.com
  go: https://artifactory.site/api/go/go-remote       # GOPROXY
  gosumdb: sum.golang.org https://artifactory.site/api/go/sumdb   # optional GOSUMDB
  git:                                                # git url.<mirror>.insteadOf=<origin>
    https://github.com/: https://gitlab.site/mirrors/github/
  urls:                                               # download URLs inside Dockerfiles
    https://github.com/: https://artifactory.site/github/
dockerfile_extra: |                                   # spliced after every FROM
  RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries
```

## What it changes

| Field | Effect |
|---|---|
| `proxy` | `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` (both cases) in every image burger runs or builds, as build args, for apt, and for git (`http.proxy`) and `gh` on the host |
| `ca_certs` | copied into each image as `/usr/local/share/ca-certificates/burger-site.crt`; `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS` point at it, `UV_NATIVE_TLS=1`, apt's `Acquire::https::CAInfo` too, and `update-ca-certificates` runs when the image has it; git gets `http.sslCAInfo` |
| `mirrors.registry` | prefixed to every image burger pulls, including the `FROM` lines of bennu's Dockerfile |
| `mirrors.pypi` | `PIP_INDEX_URL`, `UV_INDEX_URL`, `UV_DEFAULT_INDEX` |
| `mirrors.apt` | rewrites Ubuntu's `sources.list` in each image |
| `mirrors.go`, `mirrors.gosumdb` | `GOPROXY`, `GOSUMDB` (burger never turns checksum verification off itself; `off` shows as a WARN row in preflight) |
| `mirrors.git` | `url.<mirror>.insteadOf=<origin>` on burger's git commands, so your global git config is untouched |
| `mirrors.urls` | text rewrites in Dockerfiles burger does not own (bennu's `wget`/`curl` downloads) |
| `dockerfile_extra` | your own lines after every `FROM` |

How: each image burger runs (`golang:1.24`, `ubuntu:22.04`) is replaced by a
local `burger-site/...` image with a **site layer** on top; the tag hashes the
site file and the CA files, so editing either rebuilds it. bennu's
`docker/test` image is built from a patched copy of its Dockerfile (the layer
after each `FROM`), and burger builds `bennu-verify:22.04-lint` the same way
before `bennu-check`, so bennu's `run.sh` reuses it.

## What burger does not set up

The docker **daemon** pulls the base images, so a registry mirror behind the
site CA needs the daemon configured once (as root):

```bash
mkdir -p /etc/docker/certs.d/artifactory.site
cp /etc/pki/site/root-ca.pem /etc/docker/certs.d/artifactory.site/ca.crt
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/http-proxy.conf <<'CONF'
[Service]
Environment="HTTP_PROXY=http://proxy.site:8080" "HTTPS_PROXY=http://proxy.site:8080" "NO_PROXY=localhost,127.0.0.1,.site.internal"
CONF
systemctl daemon-reload && systemctl restart docker
```

`./burger preflight` checks this for real with a `docker pull` from the
registry mirror, and probes each mirror through the proxy with the CA bundle.

## Notes

- Values from the site file are baked into the local `burger-site/...` and
  bennu build images (`docker history` shows them). Keep secrets out of it;
  burger redacts `user:pass@` in URLs in its own logs and errors.
- The site layer targets Debian/Ubuntu images, which is every image burger
  uses today.
- This is tested against the exact commands burger issues and a saved copy of
  bennu's Dockerfile; behavior behind a real intercepting proxy is unverified
  until the first site run.
