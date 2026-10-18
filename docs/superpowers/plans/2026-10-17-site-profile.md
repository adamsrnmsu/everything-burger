# Site profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A host-local site file (proxy, CA certificates, mirrors, extra Dockerfile lines) that makes every docker step and git fetch burger runs work behind an intercepting proxy with internal mirrors. With no site file, behavior is unchanged.

**Architecture:**
- A new `src/burger/site.py` loads the site file into a `Site` object. It renders a **site layer**: Dockerfile lines that trust the CA and set the proxy and mirror env vars.
- Every image burger runs is swapped for a derived `burger-site/...` image built from that layer.
- Dockerfiles burger doesn't own (bennu's `docker/test`) are built from a patched copy.
- `Host` carries the optional `Site`. It adds git `-c` options and the `gh` environment, and redacts URL credentials in logs and errors.

**Tech Stack:** Python ≥3.10, PyYAML, loguru, rich (existing deps only); docker; pytest with `tests/fakes.FakeHost`.

**Spec:** `docs/superpowers/specs/2026-10-17-site-profile-design.md`

## Global Constraints

- No new runtime dependency (PyYAML, loguru, rich, pyfiglet only).
- **No site file means byte-identical behavior:** every `Host.run` command and argument is the same as today, and a test pins it.
- **Where the site file comes from:** `$BURGER_SITE_CONFIG` if set (it must then exist), otherwise `~/.config/burger/site.yaml` if it exists, otherwise no site.
- **Every field is optional and unknown fields are rejected,** with an error naming the file and field. The file is loaded with `burger.loader.load_yaml` (`${VAR}` expansion).
- **Site-file fields** (exact names):
  - top level: `proxy {http, https, no_proxy}`, `ca_certs [paths]`, `mirrors`, `dockerfile_extra` (string);
  - under `mirrors`: `registry` (prefix), `pypi`, `apt`, `go`, `gosumdb`, `git {origin: mirror}`, `urls {origin: mirror}`.
- **CA file** inside images: `/usr/local/share/ca-certificates/burger-site.crt`. The concatenated bundle on the host goes in `<work>/site/ca-bundle.pem`.
- **CA variables:** when `ca_certs` is set, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO` and `NODE_EXTRA_CA_CERTS` all point at that file, and `UV_NATIVE_TLS=1`.
- **Proxy variables:** `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`, each in both upper and lower case.
- **Mirror variables:**
  - `PIP_INDEX_URL`, `UV_INDEX_URL` and `UV_DEFAULT_INDEX` from `mirrors.pypi`;
  - `GOPROXY` from `mirrors.go`;
  - `GOSUMDB` from `mirrors.gosumdb` (never set by default; `off` only when written by the user).
- **apt:** apt trusts the CA through `Acquire::https::CAInfo`. `update-ca-certificates` runs only if present.
- **Site image tags:** `burger-site/<image with / and : replaced by _>:<12-hex hash>` of the site file's bytes plus the bundle's bytes.
- **Credentials in URLs:** `user:pass@` in any URL is redacted as `***@` in log lines and `BurgerError` messages.
- Every docker and git call goes through `Host`. `make check` and `make test` (with `BURGER_TOPOLOGIES`) pass after every task, and prek hooks pass on commit.

## Review Focus

- **A CA path that does not exist, or a file with no PEM certificate,** must fail at load with an error naming `ca_certs[i]` and the path, before any docker step. Test: `test_ca_missing_or_not_pem` (Task 1).
- **A typo in a site field** (`mirror:` for `mirrors:`, `pypy:` under mirrors) must be rejected by name. Test: `test_unknown_fields_rejected` (Task 1).
- **Dockerfiles with several `FROM` lines, `FROM … AS name`, `FROM --platform=… img`, or a `FROM` of an earlier stage** must get the layer after every `FROM` of an external image. A `FROM <earlier stage>` and `FROM scratch` get nothing, and the registry prefix never touches a stage name or `scratch`. Test: `test_patch_multistage_and_flags` (Task 2).
- **A changed site file rebuilds the site image; an unchanged one reuses it.** No rebuild is driven by the `docker image inspect` result alone when the hash differs. Test: `test_site_image_reuse_and_rebuild` (Task 3).
- **A mirror token in a URL** (`https://user:tok@artifactory/...`) never appears in `burger.log` or an error message. Test: `test_redacts_credentials_in_logs_and_errors` (Task 4).

---

## File Structure

```
src/burger/site.py            Site, load_site, bundle, env, layer, patch_dockerfile, tag, site_image,
                              build_dockerfile, git_args, host_env, redact, probe
src/burger/shell.py           Host(settings, site=None): git -c injection, gh env, redaction
src/burger/bennu.py           build_bennu / bennu_check use build_dockerfile and site_image
src/burger/experiment.py      golang:1.24 and ubuntu:22.04 through site_image
src/burger/cli.py             load_site in main; preflight site rows
tests/fixtures/bennu-test.Dockerfile   saved copy of bennu upstream/main docker/test/Dockerfile
tests/test_site.py            Tasks 1-3
tests/test_shell.py           Task 4 additions
tests/test_cli.py             Task 5 additions
docs/proxy.md                 "Running behind a proxy" page (+ mkdocs.yml nav, limitations, configuration)
```

---

### Task 1: Site model, loading, bundle, env, redaction

**Files:**
- Create: `src/burger/site.py`, `tests/test_site.py`

**Interfaces:**
- Consumes: `burger.loader.load_yaml(path, allowed, env) -> (doc, unset)`, `BurgerError`.
- Produces:
  - `@dataclass(frozen=True) Site(path: Path, proxy: dict[str, str], ca_certs: tuple[Path, ...], mirrors: dict, dockerfile_extra: str, raw: bytes)`.
  - `SITE_FIELDS = {"proxy", "ca_certs", "mirrors", "dockerfile_extra"}`, `PROXY_FIELDS = {"http", "https", "no_proxy"}`, `MIRROR_FIELDS = {"registry", "pypi", "apt", "go", "gosumdb", "git", "urls"}`.
  - `load_site(env: Mapping[str, str] | None = None, home: Path | None = None) -> Site | None`.
  - `Site.bundle(work: Path) -> Path | None`: writes the concatenated PEMs to `work/site/ca-bundle.pem` and returns the path; returns None without `ca_certs`.
  - `Site.env(ca_path: str) -> dict[str, str]`: proxy, CA and mirror variables per Global Constraints, with `ca_path` as the CA location.
  - `redact(text: str) -> str`.
  - `CA_IN_IMAGE = "/usr/local/share/ca-certificates/burger-site.crt"`.

- [ ] **Step 1: Write failing tests** in `tests/test_site.py`:
  - `test_no_site_file(tmp_path)`: `load_site(env={}, home=tmp_path) is None`.
  - `test_env_var_path_must_exist(tmp_path)`: with `BURGER_SITE_CONFIG` set to a missing file, raises `BurgerError` mentioning `BURGER_SITE_CONFIG`.
  - `test_default_path(tmp_path)`: `home/.config/burger/site.yaml` with `proxy: {https: http://p:8080}` loads, and `site.proxy == {"https": "http://p:8080"}`.
  - `test_unknown_fields_rejected(tmp_path)`: top-level `mirror:` raises naming `mirror`, and `mirrors: {pypy: x}` raises naming `pypy`.
  - `test_ca_missing_or_not_pem(tmp_path)`: a missing path raises naming `ca_certs[0]`, and a non-PEM file raises naming `ca_certs[1]`.
  - `test_bundle_concatenates(tmp_path)`: two PEM files give one bundle containing both `BEGIN CERTIFICATE` blocks, at `work/site/ca-bundle.pem`.
  - `test_env_values(tmp_path)`:
    - a full site gives both cases of every proxy variable, every CA variable equal to the given `ca_path`, `UV_NATIVE_TLS == "1"`, the three pypi variables and `GOPROXY`;
    - with no `gosumdb`, there is no `GOSUMDB` key;
    - with `gosumdb: off`, `GOSUMDB == "off"`.
  - `test_redact`: `redact("https://u:p@h/x and http://h2/") == "https://***@h/x and http://h2/"`.
- [ ] **Step 2: Run** `pytest tests/test_site.py -v`. Expected: FAIL (no module `burger.site`).
- [ ] **Step 3: Implement** in `src/burger/site.py`. PEM detection: the file contains `-----BEGIN CERTIFICATE-----`. `raw` is the site file's bytes (used for the tag in Task 2).
- [ ] **Step 4: Run** `pytest tests/test_site.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(site): load a host-local site file with proxy, CA and mirrors`.

### Task 2: Site layer, Dockerfile patch, tag

**Files:**
- Modify: `src/burger/site.py`, `tests/test_site.py`
- Create: `tests/fixtures/bennu-test.Dockerfile` (a verbatim copy of `git -C ../sceptre-bennu show upstream/main:docker/test/Dockerfile`; if that clone is missing, fetch the file from `https://raw.githubusercontent.com/sandialabs/sceptre-bennu/main/docker/test/Dockerfile`)

**Interfaces:**
- Consumes: `Site`, `CA_IN_IMAGE` (Task 1).
- Produces:
  - `Site.layer() -> str`: the Dockerfile lines inserted after a `FROM`, in this order:
    1. `COPY burger-site.crt <CA_IN_IMAGE>` (only with `ca_certs`);
    2. one `ENV` line with `Site.env(CA_IN_IMAGE)`, values double-quoted;
    3. `RUN` lines writing `/etc/apt/apt.conf.d/99burger-site` with `Acquire::https::CAInfo "<CA_IN_IMAGE>";` and, with a proxy, `Acquire::http::Proxy` / `Acquire::https::Proxy`, then `command -v update-ca-certificates >/dev/null && update-ca-certificates || true`;
    4. with `mirrors.apt`, a `RUN` that rewrites `http://archive.ubuntu.com/ubuntu` and `http://security.ubuntu.com/ubuntu` in `/etc/apt/sources.list` (when that file exists) to the mirror;
    5. `dockerfile_extra` verbatim.
  - `Site.tag() -> str`: 12-hex sha256 of `raw` plus each CA file's bytes.
  - `Site.image_name(image: str) -> str`: `mirrors.registry + image`, or `image` without a registry.
  - `patch_dockerfile(text: str, site: Site) -> str`: after each `FROM` of an external image, insert `layer()` and rewrite the reference to `image_name()`. A `FROM` naming an earlier stage (defined above with `AS <name>`) or `scratch` is left alone, with nothing inserted. Keep `--platform=` flags and `AS name`. Apply `mirrors.urls` (origin → mirror) as plain text replacements on the original lines (not on the inserted layer).
- [ ] **Step 1: Write failing tests**:
  - `test_layer_order_and_values`: with a full site, `COPY burger-site.crt` comes before `ENV`, `ENV` comes before the apt `RUN`, `Acquire::https::CAInfo "/usr/local/share/ca-certificates/burger-site.crt";` is present, and `dockerfile_extra` is the last line. Without `ca_certs` there is no `COPY` and no CA variables.
  - `test_patch_bennu_fixture`: on `tests/fixtures/bennu-test.Dockerfile` with `registry: reg.site/`, the first line becomes `FROM reg.site/ubuntu:22.04` followed by the layer, and every `RUN` line of the original appears unchanged after it, in order. With `urls: {"https://github.com/": "https://m.site/gh/"}`, the zeromq `wget` URL starts with `https://m.site/gh/` and `raw.githubusercontent.com` is untouched.
  - `test_patch_multistage_and_flags`: `FROM --platform=linux/amd64 golang:1.24 AS build` then `FROM build` then `FROM scratch` gives `FROM --platform=linux/amd64 reg.site/golang:1.24 AS build` followed by the layer, then `FROM build` and `FROM scratch` unchanged with nothing inserted after them. The layer appears exactly once.
  - `test_tag_changes_with_site_and_ca(tmp_path)`: the tag changes when the site file bytes change, and when a CA file's bytes change.
- [ ] **Step 2: Run** `pytest tests/test_site.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_site.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(site): site layer and Dockerfile patch for images we do not own`.

### Task 3: Docker steps through the site layer

**Files:**
- Modify: `src/burger/site.py`, `src/burger/bennu.py`, `src/burger/experiment.py`, `src/burger/shell.py` (only `Host.__init__(self, settings, site=None)`, storing `self.site`), `tests/fakes.py` (`FakeHost(responses=None, settings=None, site=None)`), `tests/test_site.py`, `tests/test_experiment.py`

**Interfaces:**
- Consumes: Task 2's `layer`, `tag`, `image_name`, `patch_dockerfile`; Task 1's `bundle`.
- Produces:
  - `site_image(host, image: str) -> str`. Without `host.site` it returns `image` and runs nothing. With a site it returns `burger-site/<sanitized image>:<tag>`; it builds that image only when `docker image inspect <tag>` fails, from a temp context holding `Dockerfile` (`FROM <image_name(image)>` plus `layer()`) and `burger-site.crt`.
  - `build_dockerfile(host, context: Path, tag: str) -> None`. Without a site it runs exactly `["docker", "build", "-t", tag, context]`. With a site it copies `context` to a temp dir, writes the patched Dockerfile and `burger-site.crt`, and runs `docker build` with `--build-arg` for each proxy variable (both cases), then `-t tag <tmp>`.
  - In `bennu.build_bennu`, the `docker build` becomes `build_dockerfile(host, wt / "docker" / "test", image)`. The `docker run` commands keep using `image`, which now carries the layer.
  - In `bennu.bennu_check`, with a site, `build_dockerfile(host, wt / "docker" / "test", "bennu-verify:22.04-lint")` runs before `run.sh`, so `run.sh` reuses the tag.
  - In `experiment.build`, `"golang:1.24"` and `"ubuntu:22.04"` become `site_image(host, "golang:1.24")` and `site_image(host, "ubuntu:22.04")`.
- [ ] **Step 1: Write failing tests**:
  - `test_no_site_commands_unchanged(settings, tmp_path)`: with `FakeHost(site=None)`, the recorded calls from `build_bennu` (deb plus pybennu, with `pip`), `bennu_check` and the golang/ubuntu steps of `experiment.build` equal a literal list of the commands today's code issues. Build that list by running the code at the parent commit and pasting the result; the test hardcodes it.
  - `test_site_image_reuse_and_rebuild(tmp_path)`:
    - when `docker image inspect` answers rc 0 for the tag, no `docker build` is recorded;
    - when it answers rc 1, one `docker build -t burger-site/golang_1.24:<tag>` is recorded;
    - after the site file's bytes change, the tag differs.
  - `test_build_dockerfile_with_site(tmp_path)`:
    - the build context dir has a `Dockerfile` whose first line is `FROM reg.site/ubuntu:22.04`, and a `burger-site.crt`;
    - the command has `--build-arg HTTPS_PROXY=http://p:8080` and `--build-arg https_proxy=http://p:8080`;
    - bennu's original `context` dir is unmodified (compare file bytes before and after).
  - `test_bennu_check_prebuilds_verify_image_with_site`: the first recorded command builds `bennu-verify:22.04-lint`, and the last runs `run.sh`.
- [ ] **Step 2: Run** `pytest tests/test_site.py tests/test_experiment.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The temp contexts live under `<work>/site/build/`; use `shutil.copytree(..., dirs_exist_ok=True)` into a fresh directory each time.
- [ ] **Step 4: Run** `make test && make check` (with `BURGER_TOPOLOGIES`). Expected: PASS.
- [ ] **Step 5: Commit** `feat(site): run and build every docker step through the site layer`.

### Task 4: Host git and gh options, redaction

**Files:**
- Modify: `src/burger/site.py`, `src/burger/shell.py`, `tests/test_shell.py`

**Interfaces:**
- Consumes: `Site`, `bundle`, `redact`.
- Produces:
  - `Site.git_args(bundle: Path | None) -> list[str]`: `-c url.<mirror>.insteadOf=<origin>` for each `mirrors.git` entry; `-c http.sslCAInfo=<bundle>` when a bundle exists; `-c http.proxy=<https or http proxy>` when a proxy is set.
  - `Site.host_env(bundle: Path | None) -> dict[str, str]`: proxy variables (both cases) plus `SSL_CERT_FILE` and `GIT_SSL_CAINFO` pointing at the bundle.
  - In `Host.run`:
    - when `self.site` is set and `cmd[0] == "git"`, insert `git_args` right after `"git"`;
    - when `cmd[0] in ("git", "gh")`, pass `env={**os.environ, **host_env}`, merged with any `env` the caller gave;
    - the debug log line and the `BurgerError` message go through `redact`.
  - With `site=None`, `Host.run` is unchanged, except that redaction also applies.
- [ ] **Step 1: Write failing tests** in `tests/test_shell.py` (monkeypatch `subprocess.run` to capture args and kwargs):
  - `test_git_gets_site_options(tmp_path)`: `Host(settings, site).run(["git", "fetch", "x"])` runs `git -c url.https://gitlab.site/gh/.insteadOf=https://github.com/ -c http.sslCAInfo=<bundle> -c http.proxy=http://p:8080 fetch x`, and its `env` has `HTTPS_PROXY`.
  - `test_gh_gets_env_not_args(tmp_path)`: `gh pr list` args are unchanged, and `env["https_proxy"] == "http://p:8080"`.
  - `test_no_site_git_unchanged`: `Host(settings).run(["git", "fetch", "x"])` runs exactly `["git", "fetch", "x"]` with no `env` kwarg.
  - `test_redacts_credentials_in_logs_and_errors(caplog)`: a failing command containing `https://u:tok@m.site/x` produces a log line and a `BurgerError` without `tok`. Add a loguru sink to capture the log lines.
- [ ] **Step 2: Run** `pytest tests/test_shell.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(site): git and gh use the site proxy, CA and mirrors; redact URL credentials`.

### Task 5: CLI wiring, preflight rows, docs

**Files:**
- Modify: `src/burger/site.py`, `src/burger/cli.py`, `tests/test_cli.py`, `mkdocs.yml`, `docs/limitations.md`, `docs/configuration.md`
- Create: `docs/proxy.md`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `cli.main` calls `load_site()` and builds `Host(s, site)`. A load error prints as one red line with exit code 1, like any `BurgerError`.
  - `probe(url: str, site: Site, bundle: Path | None, timeout: int = 15) -> str | None`: an HTTPS GET through the site proxy with an `ssl` context from `cafile=bundle`. Returns None on success (any HTTP status below 500), or the error text.
  - **Preflight rows** when a site is loaded. Labels are exact:
    - `site file <path>` ok;
    - `CA bundle (<n> certs)` ok;
    - `registry mirror: docker pull <image_name("ubuntu:22.04")>` via `host.run(["docker", "pull", ...], check=False)`. The FAIL hint names `/etc/docker/certs.d/<registry host>/ca.crt` and the docker daemon's proxy drop-in;
    - `pypi mirror`, `apt mirror` (probes `<apt>/dists/jammy/Release`) and `go mirror`, via `probe`;
    - `git mirror` via `git ls-remote <apps repo URL> HEAD`, which gets the site options from `Host`;
    - `GOSUMDB=off` gives a `WARN` row: "Go checksum verification is off".
  - With no site file there are no new rows.
  - **`docs/proxy.md`, "Running behind a proxy":**
    - a commented example site file with every field;
    - where burger looks for it;
    - what each field changes;
    - the docker daemon setup that burger does not do (`/etc/docker/certs.d`, a systemd `http-proxy.conf` drop-in);
    - that values are baked into local site images;
    - that behavior behind a real proxy is unverified until the first site run.
  - **Other docs:** in `mkdocs.yml`, add `Running behind a proxy: proxy.md` under Getting started. `docs/limitations.md`: replace the bennu-check proxy limitation with a pointer to `proxy.md`. `docs/configuration.md`: add a `BURGER_SITE_CONFIG` row.
- [ ] **Step 1: Write failing tests** in `tests/test_cli.py`:
  - `test_preflight_site_rows(monkeypatch, tmp_path, capsys)`:
    - with a site file (registry, pypi, apt, go, git) and `probe` monkeypatched to return None for pypi and `"connection refused"` for apt, the output has rows `site file`, `CA bundle (1 certs)`, `registry mirror`, `pypi mirror` ok, `apt mirror` FAIL with `connection refused`, `go mirror` and `git mirror`;
    - with `gosumdb: off`, there is a WARN row.
  - `test_preflight_no_site_no_rows(monkeypatch, tmp_path, capsys)`: no `site file` text in the output.
  - `test_bad_site_file_one_line(monkeypatch, tmp_path, capsys)`: a site file with `mirror:` makes `main(["list"])` exit 1 with one stderr line naming `mirror`, and no traceback.
- [ ] **Step 2: Run** `pytest tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the code and docs.
- [ ] **Step 4: Run** `make test && make check && .venv/bin/mkdocs build --strict`. Expected: PASS, and `site/proxy/index.html` exists.
- [ ] **Step 5: Commit** `feat(site): preflight checks for the site file and mirrors; proxy docs`.
