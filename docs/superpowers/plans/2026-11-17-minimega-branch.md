# minimega PR/branch boards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any switchboard runs on a minimega PR or branch, chosen by a `minimega:` key or a `--minimega` flag (the flag wins, with a warning), and puts stock minimega back when its experiment is gone.

**Architecture:** Sync records the chosen ref in the manifest and checks it out as a worktree. Build makes `burger/minimega:<sha12>` from the worktree's own `docker/Dockerfile`. Install recreates the compose `minimega` service on that image through a burger override file and pip-installs its `minimega.py` into the phenix container. Down undoes it. Verify proves which minimega ran. The new code lives in one module, `src/burger/minimega.py`.

**Tech Stack:** Python 3.10+, argparse, loguru, pytest with `tests/fakes.FakeHost`, docker compose.

**Spec:** `docs/superpowers/specs/2026-11-17-minimega-branch-design.md`

## Global Constraints

- Bead: `burger-ud6`. Claim it before Task 1; close it after Task 6 with a reason.
- YAML: `minimega: {pr: N}` or `minimega: {branch: NAME, remote: fork|upstream}`; remote defaults to `fork`.
- Flag: `--minimega pr:N`, `--minimega branch:NAME`, `--minimega branch:NAME@upstream` (also `@fork`), on `run` and `sync`.
- Warning when the flag overrides a board value, exact form: `--minimega pr:1782 overrides switchboard 'x' minimega branch:foo (fork)`.
- Image tag: `burger/minimega:<first 12 of sha>`. Override file: `<work>/minimega/compose.override.yml`. Container label: `burger.minimega=<full sha>`.
- minimega upstream slug: `sandia-minimega/minimega` (`REPOS["minimega"]` already exists).
- No git commits unless the user asks (repo profile is conservative). `make test` must pass at the end of every task, with `BURGER_TOPOLOGIES` set to `work/repos/topologies` when it exists.

## Review Focus

1. `down` of one board while another board's experiment is up: restoring minimega would kill those VMs. Expected: restore is skipped with a warning naming the running experiments. Test in Task 4.
2. Re-test loop (`sync, build, install` while the board is up, same sha): expected no recreate and no refusal. Test in Task 4.
3. `BURGER_MINIMEGA_CONTAINER=''` with a minimega board: expected a clear BurgerError at install, not a docker error. Test in Task 4.
4. A minimega container not managed by compose: expected a BurgerError telling the user to run it under compose. Test in Task 4.
5. A board without minimega on a host left swapped by a killed run: install restores stock first, verify FAILs if it is still swapped. Tests in Task 4 and Task 5.

---

### Task 1: Selecting minimega (board key, flag, override warning)

**Files:**
- Modify: `src/burger/switchboard.py` (`_FIELDS`, new `_MINIMEGA_KEYS`, `Switchboard.minimega`, `load_switchboard`, new `parse_minimega_flag`, `with_minimega_flag`, `minimega_label`)
- Modify: `src/burger/update.py` (`main_board`: pass `minimega=None`)
- Modify: `src/burger/cli.py` (`--minimega` on `run` and `sync`; `_one` applies it)
- Test: `tests/test_switchboard.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `Switchboard.minimega: dict | None = None` and `Switchboard.minimega_from: str = "board"` (`"flag"` after `with_minimega_flag`) (`{"pr": int}` or `{"branch": str, "remote": "fork"|"upstream"}`); `parse_minimega_flag(text: str) -> dict`; `with_minimega_flag(board: Switchboard, flag: str | None) -> Switchboard` (returns the board unchanged when flag is None, else a copy with `minimega` replaced, logging the warning when the board had one); `minimega_label(spec: dict) -> str` (`"#1782"` or `"fork/foo"`).

- [ ] **Step 1: Write the failing tests** in `tests/test_switchboard.py`:

```python
def test_minimega_pr_and_branch_parse(settings, tmp_board):
    assert tmp_board("minimega: {pr: 1782}").minimega == {"pr": 1782}
    assert tmp_board("minimega: {branch: foo}").minimega == {"branch": "foo", "remote": "fork"}


@pytest.mark.parametrize(
    "text,err",
    [
        ("minimega: {pr: x}", "minimega.pr must be an integer"),
        ("minimega: {pr: 1, branch: a}", "not both"),
        ("minimega: {prs: [1]}", "unknown minimega field 'prs'"),
        ("minimega: {}", "minimega needs 'pr' or 'branch'"),
    ],
)
def test_minimega_errors(tmp_board, text, err): ...  # BurgerError matching err


@pytest.mark.parametrize(
    "flag,spec",
    [
        ("pr:1782", {"pr": 1782}),
        ("branch:foo", {"branch": "foo", "remote": "fork"}),
        ("branch:a/b@upstream", {"branch": "a/b", "remote": "upstream"}),
    ],
)
def test_parse_minimega_flag(flag, spec):
    assert parse_minimega_flag(flag) == spec


@pytest.mark.parametrize("bad", ["1782", "pr:", "pr:x", "branch:", "branch:a@nope", "tag:v1"])
def test_parse_minimega_flag_rejects(bad): ...  # BurgerError naming the accepted forms


def test_flag_overrides_board_with_warning(tmp_board, caplog_loguru):
    b = with_minimega_flag(tmp_board("minimega: {branch: foo}"), "pr:1782")
    assert b.minimega == {"pr": 1782}
    assert "--minimega pr:1782 overrides switchboard" in caplog_loguru.text
    assert "minimega fork/foo" in caplog_loguru.text
```

Use whatever fixture the file already has for a temporary board and for loguru capture; add a small one if none exists.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_switchboard.py -k minimega -v`. Expected: FAIL (ImportError / AttributeError).

- [ ] **Step 3: Implement.** Validate the key with the existing `_source(path, "minimega", spec, {"pr","branch","remote"}, "fork")`; `_source` builds its `prkeys` from `key == "apps"`, so make `minimega` take `["pr"]` only. The flag parser reuses `_BRANCH` and `REMOTES`. `with_minimega_flag` uses `dataclasses.replace`. In `cli.py` add `("--minimega", {"help": "pr:N or branch:NAME[@fork|@upstream]; overrides the board's minimega"})` to `run` and `sync`, and in `_one` replace `res.board` with `with_minimega_flag(res.board, getattr(args, "minimega", None))` before `vcs.sync`.

- [ ] **Step 4: Add the CLI test** in `tests/test_cli.py`: `burger sync <board> --minimega pr:1782` with `vcs.sync` monkeypatched to capture `res.board.minimega == {"pr": 1782}`. Run `.venv/bin/pytest tests/test_switchboard.py tests/test_cli.py -v`. Expected: PASS.

### Task 2: Sync, manifest and `burger prs`

**Files:**
- Modify: `src/burger/vcs.py` (`SLUGS`, `sync`, `_pr_sha`, `prs`)
- Test: `tests/test_vcs.py`

**Interfaces:**
- Consumes: `Switchboard.minimega`, `minimega_label` (Task 1).
- Produces: `manifest["minimega"]`: `None`, or `{"pr": N, "sha": str, "from": "flag"|"board"}` / `{"branch", "remote", "sha", "from"}`; worktree at `board_dir(s, name) / "minimega"`. `from` is `Switchboard.minimega_from`.

- [ ] **Step 1: Write the failing tests** (mirror the existing bennu sync tests in `tests/test_vcs.py`):
  - `test_sync_minimega_pr`: fetch spec `+refs/pull/1782/head:refs/burger/pr/1782` from `REPOS["minimega"]`, a worktree add at `<work>/<board>/minimega`, and `man["minimega"] == {"pr": 1782, "sha": <rev>, "from": "board"}`.
  - `test_sync_minimega_branch_fork`: fetch from `https://github.com/<owner>/minimega.git` into `refs/burger/branch/fork/foo`.
  - `test_sync_without_minimega_records_none`: `man["minimega"] is None`.
  - `test_prs_lists_minimega_prs_the_boards_carry`: a board with `minimega: {pr: 1782}` gives a row with `repo == "sandia-minimega/minimega"` and that board in `boards`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_vcs.py -k minimega -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Add `"minimega": "sandia-minimega/minimega"` to `SLUGS`. In `sync`, after the bennu block, the same `branch` / `pr` paths as bennu (no `prs`), on `_ensure_repo(host, settings, "minimega")`; extend the closing log line with `minimega <label> <sha10>`. In `prs`, collect `board.minimega["pr"]` into `carried["minimega"]`; `_pr_sha` reads `man["minimega"]`.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_vcs.py -v`. Expected: PASS.

### Task 3: Build the minimega image

**Files:**
- Create: `src/burger/minimega.py`
- Modify: `src/burger/site.py` (`build_dockerfile` gains `dockerfile: str = "Dockerfile"`)
- Modify: `src/burger/experiment.py` (`_built_key`, `build`)
- Test: `tests/test_minimega.py` (new), `tests/test_site.py`

**Interfaces:**
- Consumes: `manifest["minimega"]`, worktree path (Task 2).
- Produces: `image_tag(sha: str) -> str` (`"burger/minimega:" + sha[:12]`); `build_image(host, wt: Path, sha: str) -> str` (returns the tag; skips the build when `docker image inspect <tag>` succeeds); `_built_key` gains `"minimega": (man.get("minimega") or {}).get("sha")`.

- [ ] **Step 1: Write the failing tests:**
  - `test_build_image_runs_the_repo_dockerfile`: no site, tag missing: the call is `docker build -f <wt>/docker/Dockerfile -t burger/minimega:cccccccccccc <wt>`.
  - `test_build_image_skips_an_existing_tag`: `docker image inspect` answers 0: no `docker build` call.
  - `test_build_dockerfile_with_site_patches_the_named_dockerfile` (in `test_site.py`): with a site, the copy's `docker/Dockerfile` gets the site layer and the build passes `-f <copy>/docker/Dockerfile`.
  - `test_build_marks_minimega_sha`: `built.json` holds the minimega sha; `_require_built` refuses after the manifest's minimega sha changes.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_minimega.py tests/test_site.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** `build_dockerfile` passes `-f <context>/<dockerfile>` and, with a site, patches that file in the copy. `experiment.build` calls `minimega.build_image(host, rd / "minimega", man["minimega"]["sha"])` when the manifest has minimega, before writing `built.json`.

- [ ] **Step 4: Run** `make test`. Expected: PASS (goldens unchanged: build output is not rendered).

### Task 4: Swap in `install`, restore in `down`

**Files:**
- Modify: `src/burger/minimega.py`
- Modify: `src/burger/update.py` (`compose_labels` drops files under `<work>/minimega/`; needs the settings, so take `s: Settings | None = None`)
- Modify: `src/burger/experiment.py` (`install` ends with `minimega.ensure(...)`)
- Modify: `src/burger/cli.py` (`down`: `minimega.restore(host, s)` after `experiment.down`)
- Test: `tests/test_minimega.py`, `tests/test_update.py`

**Interfaces:**
- Consumes: `image_tag`, `update.compose_labels`, `update._compose`, `update.wait_phenix`, `update.running_experiments` (Task 3 and existing).
- Produces: `running_sha(host, s) -> str | None` (the `burger.minimega` label of `s.mm_ctr`, None when absent); `ensure(host, s, want: str | None) -> None` (want = manifest sha or None; no-op when `running_sha == want`; else refuses while any experiment runs, swaps or restores, then reinstalls `minimega.py`); `restore(host, s) -> None` (no-op when not swapped; when experiments run, logs a warning naming them and returns).

- [ ] **Step 1: Write the failing tests** with `FakeHost` and compose labels as in `tests/test_update.py::compose_host`:
  - `test_swap_recreates_minimega_with_the_override`: want sha `c*40`, not swapped, nothing running: the override file holds `image: burger/minimega:cccccccccccc` and label `burger.minimega: <sha>`; the call `<compose base> -f <work>/minimega/compose.override.yml up -d --no-deps --force-recreate minimega`; then `phenix mm version` is polled; then `docker cp minimega:/opt/minimega/lib ...` and a `pip install --no-deps --force-reinstall` in the phenix container.
  - `test_same_sha_is_a_no_op_even_while_up` (Review Focus 2): label equals want, experiment running: no compose call, no error.
  - `test_swap_refuses_while_an_experiment_runs`: BurgerError naming the experiment.
  - `test_restore_uses_the_original_files_only`: swapped container whose `config_files` include the override: the `up` call has neither the override nor its `-f`.
  - `test_restore_skips_while_another_experiment_runs` (Review Focus 1): warning names it, no compose call.
  - `test_board_without_minimega_restores_a_leftover_swap` (Review Focus 5): `ensure(host, s, None)` with the label present calls restore.
  - `test_no_minimega_container_setting` (Review Focus 3): `mm_ctr=""` and want set: BurgerError mentioning `BURGER_MINIMEGA_CONTAINER`.
  - `test_minimega_not_under_compose` (Review Focus 4): labels `null`: BurgerError saying minimega must run under docker compose.
  - In `tests/test_update.py`: `test_compose_labels_drop_burgers_override`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_minimega.py tests/test_update.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the three functions; `install` calls `minimega.ensure(host, settings, (man.get("minimega") or {}).get("sha"))` after the apps marker is written; `down` in `_one` calls `minimega.restore(host, s)` after `experiment.services(..., "stop")`. Write the override with `yaml.safe_dump`.

- [ ] **Step 4: Run** `make test`. Expected: PASS.

### Task 5: Verify rows and report line

**Files:**
- Modify: `src/burger/verify.py` (`verify`, `render_report`)
- Modify: `src/burger/minimega.py` (`rows(host, s, man) -> list[Row]`)
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `running_sha` (Task 4), `minimega_label` (Task 1).
- Produces: two rows after the install row: `Row("-", "minimega container", ok|FAIL, reason, [])`: ok when `running_sha == man sha` (or both None: stock); `Row("-", "minimega.py in phēnix", ok|FAIL, reason, [])`: ok when the sha256 of `/opt/minimega/lib/minimega.py` in `s.mm_ctr` equals that of the module `python3 -c 'import minimega'` loads in the phenix container. Report line `- minimega: #1782 \`cccccccccc\` (flag)` or `- minimega: stock`.

- [ ] **Step 1: Write the failing tests:** both ok; container still stock while the manifest wants a sha (FAIL, reason names both); leftover swap on a board without minimega (FAIL, Review Focus 5); `minimega.py` hash mismatch (FAIL, reason `burger install <board>`); report line for flag and stock.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_verify.py -k minimega -v`. Expected: FAIL.

- [ ] **Step 3: Implement**; with `s.mm_ctr == ""` both rows are `skipped` with reason `BURGER_MINIMEGA_CONTAINER is empty`.

- [ ] **Step 4: Run** `make test`. Expected: PASS.

### Task 6: Docs, live-run bead, close

**Files:**
- Modify: `docs/switchboards.md` (the `minimega:` key next to `bennu:`), `docs/run-book.md` (the flag, the override warning, restore on `down`, `--keep` leaves minimega swapped), `docs/limitations.md` (no unpushed local checkouts; the worktree build may show no commit in `minimega -e version`)

- [ ] **Step 1:** Write the doc changes; run `make docs` (or `.venv/bin/mkdocs build --strict`). Expected: builds clean.
- [ ] **Step 2:** `./burger check main` and a dry run of `./burger sync main --minimega pr:1782` path through the tests. Expected: no errors.
- [ ] **Step 3:** `bd create` a live-run bead: run `./burger run main --minimega pr:<open PR>` on the phēnix host, confirm the verify rows and the restore, `--deps discovered-from:burger-ud6`.
- [ ] **Step 4:** `make test` and `prek run --all-files`; then `bd close burger-ud6 --reason "..."`.
