---
name: using-burger
description: Use when running, debugging or changing burger (everything-burger-with-extra-sauce), the switchboard test tool for sceptre-phenix-apps and bennu on a phēnix host - running a board, reading a red or WARN row, health "not healthy yet" retries, re-testing a PR, adding a PR, item or switchboard, or updating the rig.
---

# Using burger

## Overview

A switchboard (`switchboards/<name>.yaml`) is a set of items run as one phēnix
experiment, `ebwes-<name>`. `./burger run <board>` does sync, build, install,
up, scorch, verify, down. The docs are the source of truth: `docs/run-book.md`
for commands, `docs/checks.md` and `docs/items.md` for verdicts and items.

Which board: `main` is upstream main with no PRs (the state of main).
`integrate` is main plus every open apps PR. Per-PR boards (`ssh`, `iperf`, …)
say which PR a failure belongs to. `./burger list` shows them all;
`./burger prs` names the boards that carry each of your PRs.

## Quick reference

| Task | Commands |
|---|---|
| Before a round | `./burger preflight` (add `--linux-only` without Windows images); `./burger prs` shows STALE boards |
| Run a board | `./burger run <board>`; add `--keep` to inspect a passing board afterwards |
| Re-test after a PR push | `./burger sync <board> && ./burger build <board> && ./burger install <board>`, then `./burger scorch <board> --run N && ./burger verify <board>` while it is still up |
| Re-test a phēnix app change (mirror, mgmt_tap, sceptre) | apps run at experiment start, so after `install`: `./burger down <board> && ./burger up <board>`, then `scorch` and `verify` |
| Debug by hand | the step commands in `docs/run-book.md`; `./burger down <board>` when done |
| Offline check of a board edit | `./burger check <board>` |
| Restore upstream apps in phēnix | `./burger install baseline` |

## Reading results

- The board fails only on `FAIL`. Exit code 0 means it passed.
- `WARN` is not a failure. It comes from a component that logged an ERROR line
  without failing, or a `python` check returning `None`. `advisory-FAIL` comes
  from an `advisory` item. Report both as notes with their reason, not as
  failures.
- For a red row, read its reason in the table, then `work/<board>/report.md`,
  then the attempt log the console names
  (`work/<board>/results/run-<i>-attempt-<n>.log`) and the status files under
  `work/<board>/results/run-<i>/<component>/`.
- A failing `run` leaves the experiment up. Inspect the VMs before `down`. In-VM
  logs: `/var/log/burger-bennu.log`, `/var/log/burger-miniccc.log`,
  `/var/log/burger-debs.log`.

## Health retries are normal

The health run retries until it passes or `--settle` (900 s, counted after
the first attempt) runs out. Field devices sleep 60 s and Windows boots slowly,
so several "not healthy yet" attempts are expected. Let it run, and read the
failing check named on the status line. Burger kills a stuck first attempt
itself at `--first-cap`. Only when the settle cap ends the run do you debug: the
named check, then the in-VM logs above. Then `scorch <board>` again, or raise
`--settle`.

## Changing burger

- Testing a new apps PR: add it to `apps.prs` in `switchboards/integrate.yaml`
  (main plus every open PR), or to a per-PR board. Give items that need it
  `requires: {apps_prs: [N]}`. When a PR merges, remove both and move its items
  to `main.yaml`.
- New item: `./burger new scorch|app|bennu <name>`, switch it on in a board,
  then `./burger check <board>`.
- cc commands: minimega expands a bare `$name`, and SCORCH eats `${...}`. Keep
  `$` out of rendered commands (`docs/integration-notes.md`).
- Then run the tests (`make test`), with `BURGER_TOPOLOGIES` pointing at a
  sceptre-phenix-topologies checkout so the render tests run. If a golden
  differs because of your change, regenerate only that board's
  `tests/golden/<board>*.json`: with `scripts/regen_goldens.py` if it exists,
  otherwise as `json.dumps(doc, indent=2, sort_keys=True)`. Read the diff.

## On the rig

- `./burger update` refuses a dirty checkout. Never discard local changes:
  show `git diff` to the operator, then commit them or
  `git stash push -u`, pull, `git stash apply` and resolve conflicts.
- After a phēnix container restart, run `./burger install <board>` again. The
  entrypoint reinstalls upstream apps, and `verify` flags the mismatch.
- Never `down` an experiment someone kept with `--keep` without asking.
