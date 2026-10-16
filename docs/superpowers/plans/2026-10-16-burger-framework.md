# burger framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild burger as a Python package where each SCORCH component, phēnix app, and bennu behavior is one YAML item file (config, routes, checks). Saved switchboards pick the PRs, the route, and which items are on. `burger run <switchboard>` ends in a pass/fail table and a report.

**Architecture:** The current single script (`./burger`, ~1,100 lines) is split into a `src/burger/` package. An **offline tier** (load, validate, resolve, compose, render; pure Python) sits behind a `Host` seam that does all subprocess, docker, and phēnix REST work. Old rounds become switchboards. Old inline SCORCH blocks and `rounds/models.yaml` become item files. Golden renders taken from the old script before it is deleted prove nothing was lost.

**Tech Stack:** Python ≥3.10, PyYAML, loguru, rich, pyfiglet, pytest, ruff, vulture; setuptools `pyproject.toml`.

**Spec:** `docs/superpowers/specs/2026-10-16-burger-framework-design.md`

## Global Constraints

- Python floor 3.10 (the phēnix host). Install: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` (pip works on the host).
- Runtime deps exactly: `PyYAML`, `loguru`, `rich`, `pyfiglet`. Dev: `pytest`, `ruff`, `vulture`. Nothing else.
- `make check` = `ruff check .`, `ruff format --check .`, `vulture src tests`. `make test` = `pytest`. Both pass at the end of every task.
- Every env var the old script reads keeps its name and default: `BURGER_PHENIX_CONTAINER` (`phenix`), `BURGER_MINIMEGA_CONTAINER` (`minimega`), `BURGER_API` (`http://localhost:3000/api/v1`), `BURGER_TOKEN`, `BURGER_WORK` (`work/`), `BURGER_PHENIX_DIR` (`/phenix`), `BURGER_{APPS,BENNU,TOPOLOGIES}_REPO`, `BURGER_VM_PASSWORD`, `BURGER_RTDS_*` (rtds item values). New: `BURGER_SITE_TOPOLOGY`, `BURGER_SITE_SCENARIO`, `BURGER_SITE_PROVIDER` (default `power-provider`) for the `site` route.
- Experiment name `ebwes-<switchboard>`, at most 15 characters.
- No `${` may reach a cc command or validator. A cc command that has a validator must print something.
- Topology asset paths `/phenix/topologies/<dir>` are rewritten to `<phenix_dir>/topologies/burger-<dir>` in both documents. The `site` route is not rewritten.
- Library code raises `BurgerError(message)` and never calls `sys.exit`. Only `cli.main` turns errors into exit codes (1 for a failure, 2 for usage).
- Logging uses loguru only: stderr plus `work/<switchboard>/burger.log`. User-facing tables and the banner use rich. The banner is suppressed when stdout is not a TTY or with `--quiet`.
- `work/<name>/` keeps `manifest.json`, `report.md`, `results/run-N/`, `out/`, and `state.json`.

## Review Focus

- **An item enabled on a route it doesn't list** should fail before sync with "item X supports [soap], switchboard route is waterway". Test: `test_resolve_rejects_unsupported_route` (Task 5).
- **A check whose glob matches nothing** must FAIL with "no output found at …", never pass. Test: `test_files_check_missing_is_fail` and `test_pcap_check_missing_is_fail` (Task 6).
- **An unset env var** (`BURGER_VM_PASSWORD`, `BURGER_SITE_TOPOLOGY`) must be reported by name at resolve time, all of them at once. Test: `test_resolve_reports_all_unset_env` (Task 5).
- **A typo in an item or switchboard field** (`verfy:`, `route: waterwy`) must name the file and the field. It must not be silently ignored. Test: `test_load_item_unknown_field` (Task 4) and `test_switchboard_unknown_route` (Task 5).
- **Truncated or pcapng captures** (tcpdump `-i any` writes Linux SLL; mm captures may be pcapng) must parse. A truncated last record must not raise. Test: `test_pcap_sll_and_pcapng` and `test_pcap_truncated_tail` (Task 6).

---

## File Structure

```
pyproject.toml, Makefile, .gitignore            tooling
src/burger/__init__.py                          __version__
src/burger/__main__.py                          python -m burger
src/burger/config.py                            Settings, BurgerError, constants
src/burger/shell.py                             Host: run/git/ctr/phenix/mm/put/fetch/api (the seam)
src/burger/loader.py                            load_yaml with ${VAR}/${VAR:-d} expansion, strict field checks
src/burger/routes.py                            Route + load_route
src/burger/items.py                             Item, load_item, resolve_item, all_items
src/burger/switchboard.py                       Switchboard, Requires, Resolved, load_switchboard, resolve
src/burger/compose.py                           SCORCH components/runs + health component
src/burger/render.py                            topology/scenario rendering, monitor node, miniccc/debs injections
src/burger/bennu.py                             deb/wheel/pip-wheel builds, startup script, injections, bennu-check
src/burger/vcs.py                               sync (fetch, worktrees, merges, manifest), prs
src/burger/experiment.py                        install, up, wait, fix_routers, down, scorch loop, collect
src/burger/checks/__init__.py                   CheckContext, Row, CHECKS, run_checks
src/burger/checks/status.py                     evaluate, collect_status
src/burger/checks/files.py                      files, json, csv, log
src/burger/checks/pcap.py                       pcap/pcapng reader + pcap check
src/burger/checks/hook.py                       python escape hatch
src/burger/verify.py                            verify rows, health attribution, report.md
src/burger/ui.py                                banner (pyfiglet + mascot), rich tables, logging setup
src/burger/cli.py                               argparse commands
src/burger/templates/{scorch,app,bennu}.yaml    `burger new` templates
routes/{waterway,soap,site}.yaml                route facts
components/*.yaml  apps/*.yaml  bennu/*.yaml    item files (+ optional sibling .py)
switchboards/*.yaml                             baseline bennu pybennu metadata harden rtds provider iperf ssh
tests/fakes.py                                  FakeHost
tests/golden/<round>.{topology,scenario}.json   renders from the old script
tests/test_*.py                                 one file per module
```

Shared test fixture (in `tests/conftest.py`, Task 1): `MAN`, the same dict as the old `tests/test_burger.py`: apps head `"a"*40`, base `"b"*40`, bennu pr 58, sha `"c"*40`, deb_version `"6.0.1.abc1234"`, topologies `"d"*40`. Plus `topologies` (a Path from `BURGER_TOPOLOGIES`, skipping the test when unset) and `settings(tmp_path)` (a `Settings` with root = repo root, work = tmp_path).

---

### Task 1: Package skeleton, settings, Host seam

**Files:**
- Create: `pyproject.toml`, `Makefile`, `src/burger/__init__.py`, `src/burger/__main__.py`, `src/burger/config.py`, `src/burger/shell.py`, `tests/conftest.py`, `tests/fakes.py`, `tests/test_shell.py`
- Modify: `.gitignore` (add `.venv/`, `*.egg-info/`)

**Interfaces:**
- Produces: `class BurgerError(Exception)`; `@dataclass(frozen=True) Settings(root: Path, work: Path, ctr: str, mm_ctr: str, api: str, token: str, phenix_dir: str)` with `Settings.from_env(root: Path | None = None) -> Settings`. Constants: `REPOS` (the three sandialabs URLs), `MAX_EXP_NAME = 15`, `POLLERS = {"hoststats"}`, `GIT_ID`.
- Produces: `class Host(settings)` with `run(cmd, check=True, capture=False, **kw) -> CompletedProcess`, `git(repo, *a, **kw)`, `ctr(*a, **kw)`, `phenix(*a, **kw)`, `mm(exp, *a) -> CompletedProcess`, `put(src, dst)`, `fetch(src, dst) -> bool`, `api(path) -> dict | None`. Same behavior as the old `run/git/ctr/phenix/mm/put/fetch_from_phenix/api` helpers, except failures raise `BurgerError` and the `+ cmd` echo goes to `logger.debug`.
- Produces: `tests/fakes.py: FakeHost(responses: dict[str, CompletedProcess] | None)`, which records `calls: list[list[str]]`, answers `run` by the first key that is a prefix of the joined command (default rc 0, empty output), and offers `api_responses: dict[str, dict | None]`.

- [ ] **Step 1: Write failing tests** in `tests/test_shell.py`:
  - `test_settings_from_env_defaults(monkeypatch)`: with no BURGER_* vars set, `ctr == "phenix"`, `mm_ctr == "minimega"`, `api == "http://localhost:3000/api/v1"`, `phenix_dir == "/phenix"`, and `work == root / "work"`.
  - `test_settings_empty_container_means_native(monkeypatch)`: `BURGER_PHENIX_CONTAINER=""` gives `ctr == ""`.
  - `test_ctr_wraps_in_docker_exec()`: with `ctr="phenix"`, `Host.ctr("ls")` runs `["docker", "exec", "phenix", "ls"]`. With `ctr=""` it runs `["ls"]` (monkeypatch `subprocess.run`).
  - `test_run_failure_raises()`: `Host.run(["false"])` raises `BurgerError` whose message contains `"command failed (1)"`. With `check=False` it returns rc 1.
  - `test_run_missing_binary()`: `Host.run(["no-such-bin-xyz"], check=False).returncode == 127`.
- [ ] **Step 2: Run** `pytest tests/test_shell.py -v`. Expected: FAIL (`ModuleNotFoundError: burger`).
- [ ] **Step 3: Implement.** `pyproject.toml`: name `burger`, `requires-python = ">=3.10"`, the deps from Global Constraints, `[project.scripts] burger = "burger.cli:main"`, `[tool.setuptools.packages.find] where = ["src"]`, and package-data `burger = ["templates/*.yaml"]`. Ruff: line-length 100, target py310, select `E,F,W,I,UP,B,RUF`. Vulture: `min_confidence = 80`. `Makefile` targets `check`, `test`, `format`. `__main__.py` calls `burger.cli.main()`. Add a stub `cli.main` that handles only `--version`.
- [ ] **Step 4: Run** `pip install -e '.[dev]' && pytest tests/test_shell.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: burger package skeleton, settings and Host seam`.

### Task 2: Golden renders from the old script

**Files:**
- Create: `tests/golden/make_golden.py`, `tests/golden/*.json`, `tests/test_golden_files.py`

**Interfaces:**
- Produces: `tests/golden/<round>.topology.json` and `<round>.scenario.json` for every round in `rounds/` except `models`. `<round>.linux.topology.json` and `.linux.scenario.json` for waterway rounds. The bennu manifest for each render is `MAN["bennu"]` with `pr` set to the round's `bennu.pr`, plus `wheel: "pybennu-6.0.0-py3-none-any.whl"` when the round installs pybennu and `wheels: ["loguru-0.7.3-py3-none-any.whl"]` when it has `pip`. `bennu: None` when the round has no bennu PR. `stage_dir = "/phenix/burger/x"`. Env for the render: `BURGER_VM_PASSWORD=lab`; for rtds, `BURGER_RTDS_TOPOLOGY/SCENARIO` point at `tests/golden/site-topology.yaml` and `site-scenario.yaml` (the two fixtures from the old `test_rtds_round`, saved as files) and `BURGER_RTDS_PROVIDER=pp1`.

- [ ] **Step 1: Write** `make_golden.py`. It imports the root `burger` script with `SourceFileLoader`, as the old tests do, and for each round calls `burger.render(rnd, man, topologies/<dir>, linux_only, "/phenix/burger/x")` and dumps sorted-key JSON. It takes the topologies path from `BURGER_TOPOLOGIES`.
- [ ] **Step 2: Run** `BURGER_TOPOLOGIES=../sceptre-phenix-topologies python tests/golden/make_golden.py`. Expected: files for baseline, bennu, pybennu, metadata, harden, iperf, ssh, provider, rtds, with `.linux` variants for the waterway rounds.
- [ ] **Step 3: Write** `test_golden_files.py::test_golden_files_load`: every golden JSON parses and has `metadata.name` starting with `ebwes-`.
- [ ] **Step 4: Run** `pytest tests/test_golden_files.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `test: golden renders of every round from the old script`.

### Task 3: YAML loader and routes

**Files:**
- Create: `src/burger/loader.py`, `src/burger/routes.py`, `routes/waterway.yaml`, `routes/soap.yaml`, `routes/site.yaml`, `tests/test_routes.py`

**Interfaces:**
- Produces: `load_yaml(path: Path, allowed: set[str] | None = None, env: Mapping[str, str] | None = None) -> tuple[dict, list[str]]`. It expands `${VAR}` and `${VAR:-default}` from `env` (default `os.environ`), with the same regex as the old `load_round`, and returns `(doc, unset_names)`. With `allowed`, any top-level key outside it raises `BurgerError("<path>: unknown field 'x' (allowed: ...)")`.
- Produces: `@dataclass Route(name: str, topology_dir: str | None, files: tuple[str, str] | None, base: dict | None, values: dict, bennu_nodes: list[str], drop_nodes: list[str], windows_images: list[str], unset: list[str])` and `load_route(name: str, settings: Settings) -> Route`.
- Route values. waterway: `rtus [rtu-1, rtu-2]`, `provider simulink-provider`, `ics_port 502`, `bennu_nodes [rtu-1, rtu-2, simulink-provider]`, files `waterway-topology.yaml`/`waterway-scenario.yaml`, `windows_images [opc.qc2, historian.qc2, hmi_win7.qc2, scada.qc2, engineer_workstation.qc2]`. soap: `rtus [rtu-1, rtu-2, rtu-3, rtu-4]`, `provider power-provider`, `ics_port 20000`, `bennu_nodes [power-provider, rtu-1, rtu-2, rtu-3, rtu-4, siren]`, files `topology.yaml`/`scenario.yaml`, `drop_nodes [ignition, netmonitor, attacker]`. site: `base {topology: ${BURGER_SITE_TOPOLOGY}, scenario: ${BURGER_SITE_SCENARIO}}`, `provider ${BURGER_SITE_PROVIDER:-power-provider}`, `bennu_nodes [<provider>]`.

- [ ] **Step 1: Write failing tests**:
  - `test_expand_default_and_unset`: `${A:-x}` gives `x`, and `${B}` stays literal with `"B"` in `unset`.
  - `test_unknown_field_names_file(tmp_path)`: a doc with key `verfy` and `allowed={"verify"}` raises a `BurgerError` whose message contains the file name and `'verfy'`.
  - `test_waterway_route`: `ics_port == 502` and `bennu_nodes == ["rtu-1", "rtu-2", "simulink-provider"]`.
  - `test_site_route_unset(monkeypatch)`: with `BURGER_SITE_*` unset, `Route.unset == ["BURGER_SITE_TOPOLOGY", "BURGER_SITE_SCENARIO"]` and `values["provider"] == "power-provider"`.
  - `test_unknown_route`: `load_route("waterwy", s)` raises, and the message lists `site, soap, waterway`.
- [ ] **Step 2: Run** `pytest tests/test_routes.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `loader.py` and `routes.py` plus the three route files.
- [ ] **Step 4: Run** `pytest tests/test_routes.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: yaml loader with env expansion and route files`.

### Task 4: Item files

**Files:**
- Create: `src/burger/items.py`, `components/*.yaml`, `apps/*.yaml`, `bennu/*.yaml`, `tests/test_items.py`

**Interfaces:**
- Consumes: `load_yaml`, `Route`.
- Produces: `@dataclass Item(name, kind, type, about, routes, stages, holds, background, advisory, config, hosts, requires, verify, path, unset)`. `ITEM_FIELDS` = the YAML keys `name kind type about routes stages holds background advisory config hosts requires verify waterway soap site`. `kind` is one of `scorch|app|bennu`. Stages are a subset of `configure|start|stop|cleanup`. `routes: []` means validate-only.
- Produces: `load_item(ref: str, settings) -> Item`. `"pcap"` loads `components/pcap.yaml`, `"apps/mirror"` loads `apps/mirror.yaml`, and `"bennu/field-device"` loads `bennu/field-device.yaml`. `all_items(settings) -> list[Item]`.
- Produces: `resolve_item(item: Item, route: Route, override: dict | None) -> Item`. It deep-merges the item's `<route>` block, then `override` (dicts merge, lists replace). It then substitutes `{route.<key>}` and `{route.<key>[i]}` in every string. A string that is exactly one token takes the value's type: `"{route.ics_port}"` becomes `502`, `"{route.rtus}"` becomes a list. Finally it raises `BurgerError` if any `vm` check's `run` contains `${`, or if `background` is set without `stop` in `stages`, or with a type outside `POLLERS`.
- `requires` keys: `debs {packages, nodes}`, `apps [scenario app dicts]`, `monitor bool`, `env [names]`, `apps_prs [int]`, `mgmt_vlan str`, `cc_nodes [hosts]`, `keep_nodes [hosts]`, `windows bool` (item skipped under `--linux-only`).
- Check spec keys, one per list entry: `status: ok`, `files {glob, min, min_bytes}`, `pcap {glob, tcp_port | udp_port | ip_proto, min_packets}`, `json {file, field, equals | matches | exists}`, `csv {glob, min_rows, columns}`, `log {glob, contains | lacks}`, `vm {host, run, expect}`, `python {function}`.
- Item files to write, with configs copied verbatim from `rounds/*.yaml` and `rounds/models.yaml`:
  - **components**: `disruption` (stages configure/start/cleanup, `holds: true`, `current_disruption: baseline`, `run_duration: 30`); `disruption-dos` and `disruption-physical` (`routes: []`, from models.yaml); `pcap`; `tcpdump`; `mm`; `pipe`; `hoststats` (background, start/stop); `vmstats`; `qos`; `erspan` (advisory, configure/cleanup); `iperf` (requires iperf3 debs on `{route.rtus}`, `apps_prs [165]`); `ssh` (requires apps `mgmt_tap`, env `BURGER_VM_PASSWORD`, `apps_prs [170]`); `rtds`, `providerdata`, `collector` (soap and site, `apps_prs` per models); `kafka` (`routes: []` if models.yaml lists it, otherwise skip).
    - Each starts its `verify` with `status: ok`.
    - `pcap` also gets `files {glob: "*.pcap", min: 1}` and `pcap {glob: "*.pcap", tcp_port: "{route.ics_port}", min_packets: 10}`, and tcpdump gets the same.
    - Mark these exact output globs `# TODO(live): confirm file names from one run`. That is the only TODO allowed, and Task 12 resolves it.
  - **apps**: `mirror` (requires monitor; verify `vm {host: monitor, run: "timeout 30 tcpdump -nn -i any -c 3 'tcp port 502 or ip proto 47'"}`); `mgmt_tap`; `mgmt-vlan-alias` (requires `mgmt_vlan: mgmt`, `apps_prs [158]`).
  - **bennu**:
    - `field-device` (waterway: the old `ss -Hltn` :502 check, the anchored `pgrep`, and the provider query with `expect: lock`; soap override: the provider query with `expect: "bus-"`);
    - `modbus-polled` (`requires.windows: true`, the `ss -Htn state established` check);
    - `simulink-provider` (waterway);
    - `pybennu-provider` (soap and site: the anchored `pgrep`, plus the loguru log grep and the no-ERROR grep, both as `vm` checks);
    - `siren` (soap, advisory, `vm` test -s on the siren log);
    - `monitor-sees-mirror` (folded into `apps/mirror`).
- [ ] **Step 1: Write failing tests**:
  - `test_load_item_unknown_field`: the error names the file and the field.
  - `test_route_override_and_substitution`: `resolve_item(load_item("pcap"), soap)`'s pcap check `tcp_port == 20000`, and on waterway it is `502`.
  - `test_override_replaces_lists`: an override with `config.vms` of one entry replaces the list.
  - `test_vm_dollar_brace_rejected`: an item whose `vm.run` has `${X}` raises.
  - `test_background_requires_stop`: `hoststats` without `stop` in stages raises, and the message names `background`.
  - `test_every_item_loads`: every file under `components/`, `apps/` and `bennu/` loads and resolves on each route it lists.
  - `test_validate_only_items`: `disruption-dos.routes == []`.
- [ ] **Step 2: Run** `pytest tests/test_items.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `items.py` and write the item files.
- [ ] **Step 4: Run** `pytest tests/test_items.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: item files for every component, app and bennu behavior`.

### Task 5: Switchboards and resolution

**Files:**
- Create: `src/burger/switchboard.py`, `switchboards/*.yaml`, `tests/test_switchboard.py`

**Interfaces:**
- Consumes: `load_route`, `load_item`, `resolve_item`.
- Produces: `@dataclass Switchboard(name, about, route, apps_prs: list[int], bennu: dict | None, items: dict[str, bool], overrides: dict[str, dict], runs: list[dict] | None, linux_only: bool, exp: str, unset: list[str])`. Allowed YAML keys: `about route apps bennu items overrides runs linux_only`. Bennu keys: `pr install pip`. `load_switchboard(name, settings)` and `all_switchboards(settings) -> list[str]`.
- Produces: `@dataclass Requires(debs_packages: list[str], debs_nodes: list[str], apps: list[dict], monitor: bool, env: list[str], mgmt_vlan: str | None, cc_nodes: list[str], keep_nodes: list[str])` and `@dataclass Resolved(board, route, items: list[Item], validate_only: list[Item], requires: Requires, linux_only: bool)`.
- Produces: `resolve(board, settings, linux_only=False) -> Resolved`. Items come in switchboard listing order and only the enabled ones are kept. Under `linux_only`, items with `requires.windows` are skipped. Every problem is collected and raised as one `BurgerError` with one line each:
  - unknown item;
  - route not in the item's routes (validate-only items are exempt and go to `validate_only`);
  - `apps_prs` missing from `board.apps_prs`;
  - unset env vars (from the board, the route, and the items' `requires.env`);
  - an experiment name longer than 15 characters.
- Switchboards to write, one per old round with the same PRs:
  - baseline: no bennu; field-device, simulink-provider, modbus-polled.
  - bennu: pr 59, install deb+pybennu, pip loguru; same items as baseline.
  - pybennu: soap, pr 59; field-device, pybennu-provider, siren.
  - metadata: PRs from the old round; bennu 58; disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos, erspan, apps/mgmt-vlan-alias, and the bennu waterway items. Overrides reproduce the old metadata exactly: pcap vms rtu-1; tcpdump on rtu-2 with filter `tcp port 502`; mm filename `burger-bridge.pcap`; vmstats vms `[rtu-1, rtu-2, simulink-provider]`; qos rtu-2 delay `20ms`.
  - harden: PRs 155 and 158; pcap, tcpdump (monitor host override), mm, disruption, apps/mirror, apps/mgmt_tap, and the bennu waterway items.
  - iperf; ssh; provider and rtds, each with explicit `runs` copied from the old rounds.
- [ ] **Step 1: Write failing tests**:
  - `test_resolve_rejects_unsupported_route`: `providerdata` on waterway; the message contains `supports` and `waterway`.
  - `test_resolve_reports_all_unset_env(monkeypatch)`: the ssh switchboard without `BURGER_VM_PASSWORD`, and a site board without `BURGER_SITE_*`; the message lists every name.
  - `test_resolve_missing_pr`: iperf enabled on a board without 165; the message names `#165`.
  - `test_switchboard_unknown_route`: the message names the file and `waterwy`.
  - `test_linux_only_skips_windows_items`: `modbus-polled` is absent from `resolve(baseline, linux_only=True).items`.
  - `test_every_switchboard_resolves(monkeypatch)`: with `BURGER_VM_PASSWORD=lab` and the site env pointing at the golden fixtures, every switchboard resolves.
- [ ] **Step 2: Run** `pytest tests/test_switchboard.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** and write the switchboards.
- [ ] **Step 4: Run** `pytest tests/test_switchboard.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: switchboards and up-front resolution`.

### Task 6: Checks library

**Files:**
- Create: `src/burger/checks/__init__.py`, `checks/status.py`, `checks/files.py`, `checks/pcap.py`, `checks/hook.py`, `tests/test_checks.py`, `tests/test_pcap.py`

**Interfaces:**
- Produces: `@dataclass CheckContext(item: Item, dirs: list[Path], route: Route, manifest: dict, work: Path)`, where `dirs` holds the item component's `run-N/<component>/loop-*` directories. `@dataclass Row(item: str, check: str, verdict: str, reason: str, paths: list[str])`, where verdict is one of `ok|FAIL|WARN|skipped|advisory-FAIL`. `CHECKS: dict[str, Callable[[dict, CheckContext], Row]]` with keys `files json csv log pcap python`. `run_checks(item: Item, ctx: CheckContext) -> list[Row]` skips `status` and `vm` (handled by verify) and turns FAIL into `advisory-FAIL` when `item.advisory`.
- Produces: `status.evaluate(run_spec, results, background=frozenset())`, unchanged from the old script, and `status.collect_status(dst: Path, since: str = "") -> dict`, which is the old `collect` minus the fetch.
- Produces: `pcap.count_packets(path: Path, tcp_port=None, udp_port=None, ip_proto=None) -> int`. It reads classic pcap (both byte orders, micro and nanosecond magic) and pcapng (SHB/IDB/EPB). Link types: Ethernet 1 (VLAN 0x8100 tags skipped), Linux SLL 113, SLL2 276, raw IPv4 101/228. It matches IPv4 TCP/UDP with either port equal, or the IP protocol number (47 = GRE). A truncated final record ends the count without raising.
- Produces: `hook.load(item: Item, function: str) -> Callable[[CheckContext], tuple[bool, str]]`, which imports the item's sibling `.py` by path.
- [ ] **Step 1: Write failing tests**:
  - `test_evaluate` and `test_collect_skips_non_status_json`, ported verbatim from the old tests.
  - `test_files_check_missing_is_fail`: no matching file gives `FAIL`, and the reason starts with `"no output found at"`.
  - `test_files_min_bytes`.
  - `test_json_equals_and_exists`.
  - `test_csv_min_rows`.
  - `test_log_lacks`.
  - `test_python_hook(tmp_path)`: a sibling `.py` returning `(False, "nope")` gives `FAIL` with `"nope"`.
  - `test_advisory_downgrade`.
  - In `tests/test_pcap.py`, build captures with `struct` inside each test:
    - `test_pcap_counts_modbus`: 3 Ethernet IPv4 TCP packets with dport 502 plus one to port 80; the count for `tcp_port=502` is 3.
    - `test_pcap_sll_and_pcapng`: the same packets as SLL in classic pcap, and as Ethernet in pcapng, both give 3.
    - `test_pcap_gre`: `ip_proto=47` counts a GRE packet.
    - `test_pcap_truncated_tail`: the file is cut mid-record, giving count 3 and no exception.
    - `test_pcap_check_missing_is_fail`.
- [ ] **Step 2: Run** `pytest tests/test_checks.py tests/test_pcap.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command plus `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: check library with a stdlib pcap reader`.

### Task 7: Compose SCORCH pipeline and health

**Files:**
- Create: `src/burger/compose.py`, `tests/test_compose.py`

**Interfaces:**
- Consumes: `Resolved`, `Item`.
- Produces: `compose(res: Resolved, man: dict) -> tuple[list[dict], list[dict]]`, returning `(components, runs)`. Health comes first when present.
  - **Components:** each scorch item becomes `{name: item.name, type: item.type, metadata: item.config}`, plus `background: true` when set.
  - **The main run** is named after the switchboard. Each stage lists its items in listing order, with `holds` items moved last in `start`.
  - **Advisory items** each get their own `extras-<item>` run.
  - **An explicit `board.runs`** replaces the main and extras runs.
  - **Validation:** every run name must refer to a known component, and every `POLLERS` type in a run's `start` needs `background` and a `stop` entry. These are the old render checks and messages, raised as `BurgerError`.
- Produces: `health_component(res: Resolved, man: dict) -> dict | None`. For each `bennu_nodes` host (only when a bennu build is installed), it first adds the old install check, then the old version check. Both keep their validators, byte-identical to the old `health_component`. It then appends every enabled item's `vm` checks:
  - `host` is a string or a list, and a list expands to one check per host;
  - the command is `run`, or `run | grep -Eq '<expect>'` (shell-quoted) when `expect` is set;
  - each becomes `{type: exec, args, wait: true}`, with no validator.
  - Checks are grouped per hostname in first-seen order, giving `{name: "health", type: "cc", metadata: {vms: [...]}}`.
- Produces: `item_locations(runs) -> dict[str, tuple[int, str]]`, mapping an item name to its run index and component name.
- [ ] **Step 1: Write failing tests**:
  - `test_metadata_runs_match_old_membership`: compose the metadata switchboard. Per stage, the set of component types equals the old golden scorch stage sets. `disruption` is last in start. `extras-erspan` exists.
  - `test_pollers_need_background_and_stop`: ported, same messages.
  - `test_health_install_checks_byte_identical`: on the bennu board (waterway), the install and version check dicts for rtu-1 equal the golden `health` component's first two entries for rtu-1.
  - `test_expect_compiles_to_grep`: a `vm` check with `expect: lock` gives args ending in `| grep -Eq lock` and no `validator` key.
  - `test_health_waterway_covers_old_checks`: rtu-1 args include `-Hltn`, `state established`, `bennu-probe`, and the anchored pgrep. Under `linux_only`, `state established` is absent.
  - `test_explicit_runs_override`: the provider board's runs equal the YAML runs, after the health run.
- [ ] **Step 2: Run** `pytest tests/test_compose.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_compose.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: compose SCORCH runs and the health component from items`.

### Task 8: Render topology and scenario, bennu artifacts

**Files:**
- Create: `src/burger/render.py`, `src/burger/bennu.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `compose`, `health_component`, `Resolved`.
- Produces: `render(res: Resolved, man: dict, topology_dir: Path | None, stage_dir: str, settings: Settings) -> tuple[dict, dict]`. This is the old `render` driven by `Resolved`:
  - **Dropped nodes:** `route.drop_nodes` minus `requires.keep_nodes`, plus the Windows nodes when `linux_only`. Drop apps that lose all their hosts.
  - **Topology changes:** add the monitor node when `requires.monitor`; apply the `requires.mgmt_vlan` rename.
  - **Host checks:** the old missing-host checks for `bennu_nodes`, `cc_nodes`, and the debs nodes.
  - **Injections:** bennu, miniccc (`cc_nodes` or `bennu_nodes`, plus the monitor; never Windows), and debs.
  - **Scenario apps:** add `requires.apps`; replace the scorch app with `compose` output.
  - **Paths:** rewrite asset paths unless the route has `base`.
- Produces in `bennu.py`: `bennu_startup(man) -> str`, `bennu_injections(man, stage_dir) -> list`, `build_bennu(host, wt: Path, out: Path, man: dict, install: list[str], pip: list[str]) -> None`, and `bennu_check(host, wt: Path) -> None`, all ported unchanged. `MINICCC_STARTUP`, `DEBS_STARTUP`, `miniccc_injections`, `debs_injections`, and `MONITOR_NODE` live in `render.py`, unchanged.
- [ ] **Step 1: Write failing tests**. The golden parity tests use the `topologies` fixture; each switchboard pairs with the old round of the same name:
  - `test_golden_topology(name, linux_only)`: the topology equals the golden one. **Intended deviation:** `provider`, where soap's `drop_nodes` now apply. That test asserts the golden topology minus `ignition`, `netmonitor` and `attacker` nodes.
  - `test_golden_scenario_apps(name, linux_only)`: every non-scorch app equals the golden one (`mirror` dropped for `provider`).
  - `test_golden_scorch_components(name, linux_only)`: the multiset of `(type, json.dumps(metadata, sort_keys=True))` for non-health components equals the golden one.
  - `test_pybennu_render`: ports the old `test_pybennu_round`. Its assertions on hosts, the mirror drop, injections, and the pip validator run through bash.
  - `test_site_route_render`: ports the old `test_rtds_round`, with `BURGER_SITE_*` env and the injection order `[00-burger-bennu.sh, pybennu wheel, burger-miniccc, 01-burger-miniccc.sh]`.
  - `test_no_dollar_brace_in_scorch`: holds for every switchboard.
- [ ] **Step 2: Run** `BURGER_TOPOLOGIES=../sceptre-phenix-topologies pytest tests/test_render.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command plus `make check`. Expected: PASS. If a golden diff shows an item config off by a value, fix the item or switchboard override. Never edit the golden files.
- [ ] **Step 5: Commit** `feat: render switchboards; golden parity with the old rounds`.

### Task 9: Pipeline steps (sync, build, install, up, scorch, down, prs)

**Files:**
- Create: `src/burger/vcs.py`, `src/burger/experiment.py`, `tests/test_experiment.py`, `tests/test_vcs.py`

**Interfaces:**
- Consumes: `Host`, `Settings`, `Resolved`, `render`, `build_bennu`, `status.collect_status`, `status.evaluate`.
- Produces in `vcs.py`:
  - `sync(res, settings, host) -> dict` returns the manifest. It does the old `sync_round`: `refs/burger/*` fetch, fresh worktrees, `--no-ff` merges with the union CHANGELOG attributes, the conflict message naming files, and the topologies archive. A `site` route has no topologies archive.
  - `prs(settings, host, author: str) -> list[dict]` returns rows `{repo, number, head, boards, title, stale}`.
- Produces in `experiment.py`:
  - `build(res, settings, host)`;
  - `install(res, settings, host)`;
  - `up(res, settings, host, timeout: int)`, which writes `state.json`;
  - `wait_running`;
  - `not_running(host, exp) -> str`;
  - `is_router(node) -> bool`;
  - `vm_uuids(host, exp, want, timeout=60) -> dict`;
  - `fix_routers(host, settings, exp, topo)`;
  - `down(res, host)`;
  - `scorch(res, settings, host, run: int | None, settle: int, clock=time)`, which keeps the old retry logic and the `already running` wait;
  - `collect(res, settings, host, i: int, since="") -> dict`, which fetches `run-i` into `work/<name>/results/run-i`, then calls `collect_status`.
- [ ] **Step 1: Write failing tests**:
  - `test_fix_routers` and `test_scorch_settle_is_a_hard_cap`, ported verbatim from the old tests onto `FakeHost` and a fake clock. Same assertions: attempts `[0, 360, 720]`, `clock <= 1300`, and settle 0 gives `[0]`.
  - `test_sync_conflict_names_files(tmp_path)`: two local git repos whose PR refs conflict on `a.txt`. `BurgerError` mentions `a.txt` and `#2`.
  - `test_install_refuses_stale_build`: `built.json` mismatch raises and names `burger build`.
  - `test_down_stops_then_deletes`: `FakeHost` call order is `experiment stop`, then `experiment delete`, then `config delete`.
- [ ] **Step 2: Run** `pytest tests/test_experiment.py tests/test_vcs.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** by porting the old functions onto `Host` and `Resolved`.
- [ ] **Step 4: Run** the same command plus `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: sync, build, install, up, scorch and down on the Host seam`.

### Task 10: Verify and report

**Files:**
- Create: `src/burger/verify.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `run_checks`, `evaluate`, `collect`, `item_locations`, `Row`.
- Produces: `verify(res, settings, host) -> list[Row]`. Rows are built in this order:
  1. **Install:** the phēnix-apps install marker matches the manifest head.
  2. **Experiment:** it and every VM are running.
  3. **Model validation:** `phenix-scorch-validate` runs over every item config, including `validate_only`. It writes the item configs to `work/<name>/out/models.yaml` in the old `components:` list format, puts the file into phēnix, and runs it against the scenario. When the validator is missing, the row is FAIL if any item has `apps_prs`, and skipped otherwise.
  4. **Status:** a row per item per stage from `evaluate` (`extras-*` runs give advisory verdicts).
  5. **Health attribution:** if the health stage is ok, every `vm` check row is ok. If it failed, the check whose command appears in the error text (`command '<args>' failed`) is FAIL, and the later checks on that host are `skipped` with "not reached".
  6. **Item checks:** `run_checks` per item, with `dirs` from `item_locations`.
- Produces: `render_report(res, man, state, rows) -> str`, the old header (apps, bennu, topologies, synced) plus a table with columns `item | check | verdict | reason | paths`, then `**PASS**` or `**FAIL**: N failing check(s)`. `verify` writes `work/<name>/report.md`. `advisory-FAIL` does not count as failing.
- [ ] **Step 1: Write failing tests**:
  - `test_health_attribution`: the error text names the provider query; that row is FAIL, the later ones are skipped, and earlier ones are ok.
  - `test_report_paths_and_verdict`: one FAIL row gives `**FAIL**: 1`, and the paths column is present.
  - `test_advisory_not_failing`.
  - `test_models_yaml_includes_validate_only`: the written models file contains `disruption-dos`.
- [ ] **Step 2: Run** `pytest tests/test_verify.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_verify.py -v && make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: verify items and write report.md with output paths`.

### Task 11: CLI, banner, list/new/check, preflight

**Files:**
- Create: `src/burger/cli.py` (replaces the stub), `src/burger/ui.py`, `src/burger/templates/{scorch,app,bennu}.yaml`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `cli.main(argv: list[str] | None = None) -> int`, with these commands:
  - `run <board>` and its alias `round <board>`: sync, build, install, up, scorch, verify, then down unless `--keep`. Options `--linux-only --timeout 900 --settle 900 --keep`.
  - `sync <board>...`, `build`, `install`, `up`, `scorch [--run N] [--settle]`, `verify`, `down`, and `bennu-check <board>`: one step each, taking a board name.
  - `preflight [--linux-only]`: the old checks, with images from the route file (waterway images; Windows images only without `--linux-only`).
  - `prs [--author @me]`, rendered as a rich table.
  - `list`: two rich tables. Switchboards show name, route, PRs and about. Items show ref, kind, routes (`validate-only` when empty) and about.
  - `new <scorch|app|bennu> <name>`: copies the template to `components|apps|bennu/<name>.yaml` and refuses to overwrite an existing file.
  - `check <board> [--linux-only]`: resolves and renders offline, writes `work/<board>/check/{topology,scenario}.json`, and prints the item and stage summary. It needs `BURGER_TOPOLOGIES` or the synced topologies.
  - Global flags: `--quiet` (no banner) and `-v` (debug logs).
- Exit codes: 0 for a pass; 1 for `BurgerError` or a failing verify, printed as one red line; 2 for usage errors.
- Produces in `ui.py`:
  - `banner(console) -> None`: pyfiglet `"BURGER"` (font `standard`) above an original ASCII burger-boy mascot holding a burger on a tray, with a pompadour and overalls. No "BIG BOY" text.
  - `results_table(rows: list[Row]) -> Table`: green ok, red FAIL, yellow WARN and advisory-FAIL, dim skipped.
  - `setup_logging(work_dir: Path | None, verbose: bool)`.
- Templates: commented YAML showing every field, with `about` filled in as instructions and a `status: ok` check.
- [ ] **Step 1: Write failing tests**:
  - `test_version`.
  - `test_list_shows_boards_and_items(capsys)`: the output contains `metadata`, `bennu/field-device`, and `validate-only`.
  - `test_new_refuses_overwrite(tmp_path)`: the second call exits with 1.
  - `test_new_template_loads(tmp_path)`: the created file passes `load_item`.
  - `test_check_offline(tmp_path)`: `check pybennu` exits 0 and writes both JSON files (needs topologies).
  - `test_error_is_one_line(capsys)`: `check nosuch` exits 1, and stderr contains `no switchboard 'nosuch'` without a traceback.
  - `test_banner_suppressed_when_not_tty(capsys)`: there is no figlet text in captured output.
- [ ] **Step 2: Run** `pytest tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -v && make check`. Expected: PASS for the whole suite.
- [ ] **Step 5: Commit** `feat: burger CLI with banner, list, new and offline check`.

### Task 12: Cut over: docs, shim, delete the old script

**Files:**
- Delete: the root `burger` script, `rounds/`, `tests/test_burger.py`, `tests/golden/make_golden.py`
- Create: the root `burger` shim (bash)
- Modify: `README.md`, `components/pcap.yaml` and `components/tcpdump.yaml` (the TODO globs, if a live output listing is available)

**Interfaces:**
- Produces: the root `./burger` shim. It runs `exec "$here/.venv/bin/burger" "$@"`, or prints `burger: run 'python3 -m venv .venv && .venv/bin/pip install -e .' first` and exits 1 when the venv is missing.
- [ ] **Step 1: Write a failing test**, `tests/test_shim.py::test_shim_without_venv(tmp_path)`. Copy the shim into an empty directory and run it with bash. It exits 1 and stderr contains `pip install -e .`.
- [ ] **Step 2: Run** `pytest tests/test_shim.py -v`. Expected: FAIL (the shim does not exist yet).
- [ ] **Step 3: Replace** the old script with the shim and delete the listed files. Rewrite `README.md` with these sections:
  - Install.
  - Run book: `./burger preflight`, `list`, `run <board>`, `check <board>`, and the steps.
  - Switchboards, Items, Checks: a table of check types with one example each.
  - Adding a test: `burger new`, edit the file, `burger check`, add it to a switchboard.
  - Routes, Configuration: the env table.
  - Where files go, Coverage: the PR table, renamed from rounds to switchboards.
  - Limitations, carried over word for word where still true.

  The pcap and tcpdump output-glob TODOs are resolved now if a live output listing exists. Otherwise, file a bead (`bd create "Confirm pcap/tcpdump output globs from a live run" --deps discovered-from:<epic>`) and drop the TODO comment.
- [ ] **Step 4: Run** `pytest -v && make check && ./burger --version`. Expected: PASS and the version printed. `grep -rn "rounds/" README.md src` gives no hits.
- [ ] **Step 5: Commit** `feat!: switch to the burger package; remove the old script and rounds`.
