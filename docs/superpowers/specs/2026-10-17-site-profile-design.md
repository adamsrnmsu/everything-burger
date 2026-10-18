# Site profile: proxy, CA certificates and mirrors for burger's docker steps

Date: 2026-10-17. Status: design, reviewed; awaiting the user's approval.

## Intent

burger will move from an open lab host to a site host where:

- outbound traffic goes through a proxy that re-signs TLS with a site CA;
- packages and images come from internal mirrors (registry, PyPI, apt, Go,
  git), not the public services;
- docker on the host may not be set up for either. Assume burger has to pass
  the proxy in by hand on every docker command (the user's call).

Today every docker step burger runs reaches the public internet with the
image's default trust store. Behind the site proxy, each one fails on the CA
or on blocked hosts. bennu-check already has this listed as a limitation.

The goal: one site file, kept out of git, that makes every docker step and
git fetch work at the site. Nothing changes when the file is absent.

## Goals

- **One file per host.** Proxy, CA certificate files, mirror URLs, and an
  escape hatch for anything else. Every field is optional.
- **No changes to repos burger doesn't own.** bennu's
  `docker/test/Dockerfile` and `run.sh` stay untouched. burger adapts around
  them.
- **Every network-touching step is covered:**
  - `build`: the bennu build image (`docker build` of bennu's
    `docker/test`), the pybennu wheel and `pip wheel` extras inside it, the
    Go mirror binary (`golang:1.24`), and the jammy debs (`ubuntu:22.04`
    `apt-get download`);
  - `bennu-check` (bennu's `run.sh`);
  - `sync`, `prs`: the host's `git clone` / `fetch` / `ls-remote` of the
    sandialabs repos.
- **`preflight` checks the site file:** the CA files exist and parse as PEM,
  and each configured mirror answers through the proxy.
- **Documented.** A docs page with a commented example site file.

## Non-goals

- Configuring the host's docker daemon or `~/.docker/config.json`.
- Anything inside the experiment VMs. They have no network; burger already
  injects everything they need.
- The phēnix container's `pip install`. It is `--no-deps` from a local
  tarball, so it never reaches the network.
- Credentials for mirrors. If a mirror needs auth, it goes in the URL or in
  the escape hatch. burger does not manage secrets.

## Anti-goals

- **Site details in git.** Hostnames, CA files and mirror URLs live only in
  the site file. The repo carries an example with placeholder values.
- **Any behavior change without a site file.** No site file means
  byte-identical docker commands to today. A test pins this.
- **Silent half-configuration.** A site file that names a CA path that does
  not exist, or a mirror that does not answer, must fail `preflight` and fail
  the first docker step with a message naming the field. It must never fall
  back to the public internet quietly.
- **Forking bennu's Dockerfile.** No copy kept in burger that drifts. The
  patch is computed from bennu's current file at build time.

## Constraints

- Python ≥3.10 and the existing deps only (PyYAML, loguru, rich, pyfiglet).
  No new runtime dependency.
- All docker and git calls go through the `Host` seam, so the offline tests
  can assert the exact commands.
- The site file is YAML, loaded with burger's existing loader (strict field
  checking, `${VAR}` expansion). It is found at `$BURGER_SITE_CONFIG` if set,
  otherwise `~/.config/burger/site.yaml` if it exists.

## The site file

Illustrative. Field names are the builder's to finalize; the shape holds.

```yaml
proxy:
  http: http://proxy.site:8080
  https: http://proxy.site:8080
  no_proxy: localhost,127.0.0.1,.site.internal
ca_certs:                       # PEM files; all of them are trusted
  - /etc/pki/site/root-ca.pem
  - /etc/pki/site/issuing-ca.pem
mirrors:
  registry: artifactory.site/docker-remote/          # prefix for every image burger pulls
  pypi: https://artifactory.site/api/pypi/pypi/simple
  apt: https://artifactory.site/ubuntu                # replaces archive/security.ubuntu.com
  go: https://artifactory.site/api/go/go-remote       # GOPROXY
  git:                                                # git url.<base>.insteadOf rewrites
    https://github.com/: https://gitlab.site/mirrors/github/
  urls:                                               # plain download URLs in Dockerfiles (wget/curl)
    https://github.com/: https://artifactory.site/github/
    https://go.dev/dl/: https://artifactory.site/go-dl/
    https://raw.githubusercontent.com/: https://artifactory.site/raw-github/
dockerfile_extra: |                                   # escape hatch, spliced after every FROM
  RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries
```

## How it works

One mechanism covers both the images burger builds and the images it runs: a
**site layer**.

- **Site layer.** For any image burger uses, a site file makes burger build
  a derived local image first: `FROM <registry mirror><image>`, then:
  - copy the CA files into the image and run `update-ca-certificates`, or
    the distro's equivalent;
  - set `ENV` for the proxy (upper and lower case) and the CA bundle
    variables (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`,
    `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`);
  - set `PIP_INDEX_URL` and `GOPROXY` when those mirrors are set;
  - point apt sources at the apt mirror when it is set;
  - add the `dockerfile_extra` lines.

  The derived image is tagged `burger-site/<image>` with a hash of the site
  file, so a change to the site file rebuilds it, and no change reuses it.
- **Images burger runs** (`golang:1.24`, `ubuntu:22.04`): `docker run` uses
  the site-layer image instead. The commands are otherwise unchanged.
- **Dockerfiles burger doesn't own** (bennu's `docker/test`): burger copies
  the build context to a temp directory and writes a patched Dockerfile.
  After each `FROM`, it inserts the site layer. Throughout the file, it
  applies `mirrors.urls` rewrites to the text, so `wget`/`curl` downloads go
  to the mirror. Proxy values go in as `--build-arg` too, since build steps
  before the layer can't see the `ENV`. The result is the same image tag
  burger uses today.
- **bennu-check.** bennu's `run.sh` builds `bennu-verify:22.04-lint` only if
  that tag is missing. burger pre-builds that tag from the patched
  Dockerfile, so `run.sh` reuses it. `run.sh` passes only a fixed list of env
  vars to its `docker run`, so the site layer's baked-in `ENV` (proxy, CA,
  `GOPROXY`) is what reaches `go test` and `pytest` inside. This removes the
  "bennu-check does not work behind a TLS-intercepting proxy" limitation.
- **Host git.**
  - `mirrors.git` becomes `-c url.<mirror>.insteadOf=<origin>` on burger's
    `git clone` / `fetch` / `ls-remote` calls.
  - `ca_certs` becomes `-c http.sslCAInfo=<bundle>`.
  - The proxy becomes `-c http.proxy=…`.
  - These are passed per command, so the user's global git config stays
    untouched.
  - `gh` (used by `prs`) gets the proxy and CA through its environment.
- **One CA bundle.** burger concatenates `ca_certs` into one PEM file under
  `work/site/` and uses that file everywhere.
- **Preflight.** New rows:
  - the site file loaded (with its path);
  - each CA file exists and contains a PEM certificate;
  - each mirror URL answers an HTTPS request through the proxy using the
    bundle (registry: `/v2/`; pypi: the simple index; apt: the `dists/`
    listing; go: the proxy root; git: `ls-remote` of one repo).

## Carrying over / compatibility

- No site file: every docker and git command is byte-identical to today. A
  test pins the exact `Host` calls for `build`, `bennu-check` and `sync`
  without a site file.
- Env names stay as they are. `BURGER_SITE_CONFIG` is the one new variable.
  It is distinct from `BURGER_SITE_TOPOLOGY` and `BURGER_SITE_SCENARIO`,
  which belong to the `site` route.
- README/docs: a "Running behind a proxy" page with the example site file.
  The bennu-check limitation is updated.

## The user's answers

- `github.com`, `raw.githubusercontent.com`, `go.dev`/`dl.google.com` and
  `sum.golang.org` are reachable through the proxy; only the site CA is
  needed for them. So `mirrors.urls` stays optional (for a site that blocks
  them), and the `golangci-lint` installer's own GitHub download works through
  the proxy.
- The proxy needs no credentials. Redaction of `user:pass@` in logs stays, for
  mirror URLs that carry a token.

## Calls made after the builder review

Claude's calls, unless marked as the user's.

- **Base-image pulls are the daemon's job.** burger does not configure the
  docker daemon. `preflight` tests what the daemon can do by running
  `docker pull <registry mirror>ubuntu:22.04`, not by an HTTPS probe from the
  client. When that pull fails, the row says the daemon needs the registry
  mirror's CA in `/etc/docker/certs.d/<host>/ca.crt`, and the proxy in its
  systemd drop-in. The docs page shows both. burger never works around the
  daemon (no skopeo).
- **CA bootstrap without apt.** The site layer copies the bundle to
  `/usr/local/share/ca-certificates/burger-site.crt`. It sets every
  CA-bundle variable to that file directly, so tools trust the site CA
  before any package is installed. It writes apt's
  `Acquire::https::CAInfo` to the same file, so an `https://` apt mirror
  works on an image without `ca-certificates`. `update-ca-certificates`
  runs only if it exists.
- **uv.** `mirrors.pypi` also sets `UV_INDEX_URL` and `UV_DEFAULT_INDEX`.
  `UV_NATIVE_TLS=1` is set whenever `ca_certs` is present, because uv
  ignores the system store otherwise.
- **Go checksum database.** New optional `mirrors.gosumdb`, which sets
  `GOSUMDB`. Unset leaves Go's default (`sum.golang.org` through the proxy).
  burger never turns verification off on its own. `off` is accepted only
  when the user writes it, and preflight shows it as a warning row.
- **No apt rewrite for `golang:1.24`.** burger only runs `go build` there.
  The apt mirror applies to Ubuntu-based images (`ubuntu:22.04` and bennu's
  `docker/test`), using Ubuntu's `sources.list` layout.
- **Verification.** Exact-`Host`-call tests for every step, with and
  without a site file. A Dockerfile-patch test against a saved copy of
  bennu's current `docker/test/Dockerfile`. A test that the site layer's
  rendered Dockerfile contains each env var. Behavior behind a real
  intercepting proxy stays unverified until the first site run, and the
  report says so.
- **Secrets in logs.** Userinfo in any proxy or mirror URL
  (`user:pass@`) is redacted in burger's logs and console output. The docs
  say that credentials in the site file end up in local image layers
  (`docker history`) and build args.

## Left to the builder

- The module layout. A `site.py` with `load_site()`, `site_image()`,
  `patch_dockerfile()` and `git_args()` is the obvious shape.
- The exact site-file field names, as long as every field is optional and
  unknown fields are rejected.
- How the distro is detected for CA installation. Debian/Ubuntu
  (`/usr/local/share/ca-certificates` + `update-ca-certificates`) covers
  every image burger uses today. Other distros may raise "unsupported base
  image" for now.
- The hash scheme for site-layer tags.
- Whether `mirrors.urls` rewrites also apply to `RUN` lines in the site layer
  itself.
