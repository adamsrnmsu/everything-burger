# mm-release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** run a board on a phēnix PR (as on a minimega one), and add host-side probes that test the riskiest minimega changes since 3.3.0 and phēnix PR 576, gathered in an `mm-release` board on `waterway-arm64`.

**Architecture:** a `phenix:` board key mirrors `minimega:` (sync, build, compose swap, verify row, restore) in a new `src/burger/phenix.py`. A `probes:` board key names host-side checks in `src/burger/probes/`, run by `verify` while the experiment is up and by `burger probe <board>`; `mm-check` becomes the `mm-bindings` probe.

**Tech Stack:** Python 3, pytest with `tests/fakes.py`, docker compose on the phēnix host.

**Spec:** `docs/superpowers/specs/2026-11-20-mm-release-design.md`

## Global Constraints

- Tests: `PYTHONPATH=src BURGER_TOPOLOGIES=$M/work/repos/topologies $M/.venv/bin/python -m pytest -q`, where `M=/Users/ryanadams/git/everything-burger-with-extra-sauce`. `ruff check`, `ruff format --check`, `vulture src tests` clean.
- No live range commands (no `./burger run|up|install`, no docker on the Lima VM): the lead runs those.
- Goldens: only through `make goldens BOARDS=<board>`, and read the diff.
- `${...}` only in YAML that burger expands at load; never in rendered cc commands.
- One conventional commit per task, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push.
- Docs are the source of truth: each task updates the doc pages its change touches (`docs/switchboards.md`, `docs/run-book.md`, `docs/routes.md`, `docs/coverage.md`).

## Review Focus

1. A killed run left phēnix swapped: the next board without `phenix:` must put stock phēnix back at `install`, as minimega does.
2. The phēnix and minimega swaps must not overwrite each other's compose override file.
3. Recreating phēnix drops burger's apps install and minimega.py: `install` swaps phēnix first, then installs apps, then minimega.
4. A probe on a board whose experiment is down gives a `skipped` row, not a traceback.
5. `mm-capture` deletes its captures and files in a `finally`, even when an assertion fails mid-way.

---

### Task 1: `phenix:` board key and swap (bead: phenix swap)

**Files:**
- Create: `src/burger/phenix.py`, `tests/test_phenix.py`
- Modify: `src/burger/switchboard.py`, `src/burger/vcs.py`, `src/burger/routes.py`, `routes/waterway-arm64.yaml`, `src/burger/experiment.py`, `src/burger/verify.py`, `src/burger/cli.py`, `src/burger/minimega.py` (override path only), docs.

**Interfaces:**
- Board: `phenix: {pr: N}` or `{branch, remote}`; `Switchboard.phenix: dict | None`, `phenix_from: str`. `--phenix pr:N|branch:x[@upstream]` on `run` and `sync`. Parse and flag-apply by generalising the minimega functions over the key, not by copying them.
- `vcs.REPOS["phenix"] = "sandialabs/sceptre-phenix"`; `sync` writes `work/<board>/phenix` and `man["phenix"] = {**spec, "sha", "from"}` (None without).
- Route: `phenix_sed: str | None`; `waterway-arm64.yaml`: `phenix_sed: ${BURGER_ARM64_RANGE:-/Users/ryanadams/git/phenix-waterway-range}/docker/phenix-arm64.sed`.
- `phenix.image_tag(sha: str, mm_image: str | None) -> str`: `burger/phenix:<sha12>-<12 hex of sha256(mm_image)>` or `-stock`.
- `phenix.build_image(host, wt: Path, sha: str, mm_image: str | None, sed: str | None) -> str`: Dockerfile = `docker/Dockerfile`, through `sed -f` when given, then its first `FROM` (the minimega stage) pointed at `mm_image` when given; written as `docker/Dockerfile.burger` in the worktree; built with `build-arg PHENIX_COMMIT=<sha>` through `site.build_dockerfile`. Reuses an existing tag.
- `phenix.ensure(host, s, want: str | None, image: str | None)`, `phenix.restore(host, s)`, `phenix.rows(host, s, man, board) -> list[Row]` (row `phenix container`), labels `burger.phenix` and `burger.phenix.image`, override `work/phenix/compose.override.yml`. Share the compose recreate code with minimega rather than duplicating it (move it to a helper taking the service's container, labels and override path).
- `experiment.build`: after minimega, `man["phenix"]["image"] = phenix.build_image(..., (man.get("minimega") or {}).get("image"), res.route.phenix_sed)`; the built key includes it.
- `experiment.install`: `phenix.ensure` first (stock when the board has none), then apps, then minimega. `up`: `phenix.ensure` before `minimega.ensure`. `down` in cli: `phenix.restore` after `minimega.restore`. `verify`: `phenix.rows` after `minimega.rows`.

- [ ] Write failing tests in `tests/test_phenix.py` with `tests/fakes.py` (model them on `tests/test_minimega.py`): tag; sed then FROM rewrite (first FROM only, `AS minimega` kept); build reuses an existing tag; ensure recreates with the override and labels; ensure(None) on a swapped container restores stock; ensure refuses while an experiment runs; rows FAIL when the running sha differs; the two override files differ. Switchboard: `phenix: {pr: "x"}` rejected; `--phenix pr:576` overrides the board's. Install order: phēnix ensure happens before the apps pip install.
- [ ] Run them, see them fail. Implement. Run the full suite and lint; commit `feat(phenix): run a board on a sceptre-phenix PR or branch`.

### Task 2: probes and the `mm-bindings` probe (bead: probes + bindings)

**Files:**
- Create: `src/burger/probes/__init__.py`, `src/burger/probes/mm_bindings.py`, `tests/test_probes.py`
- Delete: `src/burger/mmcheck.py`, `tests/test_mmcheck.py` (move what still applies)
- Modify: `src/burger/switchboard.py` (`probes: [str]`, unknown name is an error at load), `src/burger/verify.py`, `src/burger/cli.py` (`probe <board> [name ...]` replaces `mm-check`), docs (`run-book.md`, `coverage.md`, `switchboards.md`).

**Interfaces:**
- `probes.PROBES: dict[str, Callable[[host, Settings, Resolved, dict], list[Row]]]`; `probes.run(host, s, res, man, names=None) -> list[Row]`. Each row's item is the probe name. When the experiment is not running every probe gives one `skipped` row "experiment not running". A probe that raises gives a FAIL row with the exception text.
- `verify` runs the board's probes after the "experiment + VMs running" row.
- `mm_bindings.run`: runs a script in the phēnix container (`host.ctr("python3", "-c", SCRIPT, exp, vm)`) against the minimega.py phēnix imports (no pyapigen build: on a minimega board, `minimega.rows` already proves it is that container's). Assertions, each named in the FAIL message: `with minimega.connect(namespace=exp) as mm` gives a client whose `vm_info()` lists the VM; after the block a call fails (socket closed); `close()` twice does not raise; `copy.copy(mm)` works; `pickle.dumps(mm)` raises; `cc_filtered` sets, clears on exit and clears when the block raises (the old CHECK). No `__enter__` gives FAIL "minimega.py predates #1686".

- [ ] Failing tests: `probes.run` skipped rows when the experiment is down; exception becomes FAIL; unknown probe name in a board is a load error; `mm_bindings.run` turns the script's JSON result into rows (fake `host.ctr`), predates-1686 message. Implement, suite + lint, commit `feat(probes): host-side probes; mm-check becomes the mm-bindings probe`.

### Task 3: `mm-capture` probe (bead: capture probe; after Task 2)

**Files:** Create `src/burger/probes/mm_capture.py`, `tests/test_mm_capture.py`; modify `src/burger/probes/__init__.py`, `docs/coverage.md`.

**Interfaces:**
- `mm_capture.run(host, s, res, man) -> list[Row]`, one row per step. minimega through `docker exec <s.mm_ctr> minimega -e "namespace <exp> <cmd>"`; files dir from the container's `MM_FILEPATH` env (default `/tmp/minimega/files`); file names `burger-mmcap-*`; seconds `BURGER_MMCAP_SECONDS` (10).
- Steps: (1) `capture pcap vm <rtus[0]> 0 <f>`: classic pcap magic, frames > 0; (2) `capture pcap bridge <bridge> <f> vlans=<first vlan of the namespace>`: frames > 0 and every frame 802.1Q-tagged with that VLAN; (3) `capture pcap namespace <f> format=pcapng`: pcapng magic `0a0d0d0a`, frames > 0; (4) two VM captures, `capture pcap delete id <id of one>`: the other is still listed; (5) `snaplen=70000`, `vms=`, `format=` each give an error; (6) after the deletes, `bridge mirrors` lists none for the namespace.
- `pcap_frames(data: bytes) -> list[bytes]` reads classic pcap and pcapng (EPB/SPB) with `struct`; `vlan_of(frame: bytes) -> int | None`.
- `finally`: `capture pcap delete namespace all`, `capture pcap delete vm all` (whatever minimega accepts), remove `burger-mmcap-*`.

- [ ] Failing tests: the parsers on byte fixtures built in the test (classic, pcapng, tagged and untagged frames); each step's row from canned minimega output (fake host); cleanup runs when a step raises. Implement, suite + lint, commit `feat(probes): mm-capture checks minimega's capture engine`.

### Task 4: `workflow-vars` probe (bead: PR 576 probe; after Task 2)

**Files:** Create `src/burger/probes/workflow_vars.py`, `tests/test_workflow_vars.py`; modify `src/burger/probes/__init__.py`, `docs/coverage.md`.

**Interfaces:**
- `workflow_vars.run(host, s, res, man) -> list[Row]`; `skipped` "needs a phēnix with PR 576" when `man.get("phenix")` is None. Runs `phenix` in the phēnix container on a temp dir it writes there and removes after.
- Read PR 576's docs first for the exact CLI and file shapes: `gh pr diff 576 -R sandialabs/sceptre-phenix` (`docs/content/workflow.md`, `docs/content/configuration.md`, `docs/content/cli/workflow.md`, `docs/content/cli/config.md`).
- Rows: `-e` substitutes; `--env-file` is read and `-e` beats it; the env file beats the shell; `<dir>/.env` is read by what reads it (per the docs); `${NAME:default}` gives the default; `$${NAME}` stays literal `${NAME}`; a variable with no value and no default exits non-zero naming the variable; `config create -e` stores the substituted value (`config get`), then the config is deleted.

- [ ] Failing tests with a fake host returning canned CLI output: each row's pass and fail; skipped without `phenix`. Implement, suite + lint, commit `feat(probes): workflow-vars checks phenix PR 576's variables`.

### Task 5: `mm-release` board (bead: board; after 1-4)

**Files:** Create `switchboards/mm-release.yaml`, its goldens; modify item `routes:` (`components/*.yaml`), `docs/switchboards.md`, `docs/coverage.md`.

- Board: `route: waterway-arm64`, `apps: {prs: []}`, `minimega: {branch: master, remote: upstream}`, `phenix: {pr: 576}`, `probes: [mm-bindings, mm-capture, workflow-vars]`, items: `arm64`'s plus the minimega-facing items (`mm`, `pcap`, `tcpdump`, `vmstats`, `hoststats`, `disruption`, `cc-*`) whose `routes:` gain `waterway-arm64` where `./burger check mm-release` renders them. An item that cannot render on the arm64 route stays off, named in the board's comment with the reason.
- [ ] `./burger check mm-release`, `make goldens BOARDS=mm-release` (and any board whose render changed), suite + lint, commit `feat(boards): mm-release, minimega master with phenix 576 on the arm64 range`.

### Task 6: live runs (lead, on the range)

`./burger run arm64 --phenix pr:576`, then `./burger run mm-release`; read each report; file beads for failures.
