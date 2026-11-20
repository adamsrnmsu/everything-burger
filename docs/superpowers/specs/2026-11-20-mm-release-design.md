# mm-release: test a minimega release candidate with a phēnix PR on the arm64 range

## Why

We are about to cut a minimega release from `master` (3.3.0 was 2026-09-22; about
37 PRs have merged since). phēnix PR 576 (variables in configs and workflows) is
about to land too. Before the release we want one burger run on the arm64 Lima
range that puts minimega `master` under phēnix 576 and goes deep on the riskiest
minimega changes, not wide over all 37.

## Goals

- A board can run on a phēnix PR or branch, as it can on a minimega one.
- A new `mm-release` board on `waterway-arm64`: minimega `master`, phēnix PR 576,
  the minimega-relevant items, plus new checks for the risky changes below.
- Run it, plus `arm64` on PR 576 alone, so a failure points at one side.

## Non-goals

- Coverage of every merged minimega PR (cc column widths, nfcat, vnc, miniplumber,
  vmbetter, iomeshage, cross-user cc, auto-update). Health checks still exercise
  cc, file sends and miniccc on every run.
- The x86 rig. Lima VM changes only if the run needs them.

## Risky areas and their checks

1. **Python bindings (#1686, #1789).** `lib/` moved to `pyproject.toml` with
   `setuptools>=77`; the phēnix image pip-installs it from the minimega stage on
   Ubuntu 24.04 (setuptools 68). Checks: the phēnix image builds against the
   board's minimega (the build is the test), and `mm-check` grows from #1684's
   `cc_filtered` to the #1686 API: `with minimega.connect(...) as mm`, `close()`,
   the namespace copy wrapper, `cc_filtered`. `mm-check` takes its minimega from
   the board (no hard-coded PR) and runs as a verify row on a board with its own
   minimega.
2. **Capture engine (#1699, #1800, #1707).** A new item `mm-capture` drives
   minimega directly in the experiment namespace during its run: VM capture,
   bridge capture with `vlans=`, namespace capture in pcapng, `capture pcap delete
   id`, option errors (`snaplen=70000`, empty values). Its python check reads the
   files (frame counts, pcapng magic, VLAN tags) and requires `bridge mirrors` to
   be empty after the deletes. The existing `mm`, `pcap` and `tcpdump` items join
   `waterway-arm64` where they already work.
3. **phēnix PR 576.** burger's `up` uses `phenix config create`, which now
   substitutes `${NAME}`; burger's rendered configs carry none, so every run is a
   regression check. A new offline item `workflow-vars` runs in the phēnix
   container: `phenix workflow render` with `-e`, `--env-file` and `.env`,
   `$${NAME}` stays literal, a variable with no value is an error, and
   `config create -e` fills a tiny config.

## The `phenix:` board key

Mirrors `minimega:`: `pr: N` (sandialabs/sceptre-phenix) or `branch` with
`remote`. `--phenix pr:576` on `run`/`sync`.

- `sync` fetches it into `work/<board>/phenix`, manifest `phenix: {pr|branch, sha}`.
- `build` builds the phēnix image from that tree's `docker/Dockerfile`. A route may
  name a `phenix_sed` (waterway-arm64: the range's `docker/phenix-arm64.sed`)
  applied to the Dockerfile first. The minimega stage's `FROM` points at the
  board's minimega base image (`burger/minimega-base:<sha12>`, or the plain build)
  so the image pip-installs that minimega's bindings; with no board minimega it
  stays as written. Tag `burger/phenix:<sha12>-<minimega sha12 or stock>`.
- `install` recreates the compose `phenix` service on that image through the same
  override file, labelled `burger.phenix`, before the minimega swap and the apps
  install (the phēnix entrypoint reinstalls upstream apps on boot).
- `verify` adds a `phenix container` row; `down` puts stock phēnix back.

Recreating phēnix does not kill VMs, but `ensure` still refuses while another
experiment runs, as for minimega.

## Approaches considered

- **Hand-build images with the range Makefile, run the stock boards (rejected):**
  quickest once, but not repeatable, and verify cannot prove which phēnix ran.
- **`phenix:` key mirroring `minimega:` (chosen):** reuses the swap, override,
  verify and restore code; repeatable for the next phēnix PR.

## Left to the builder

Item mechanics (stage that holds the run open, how the python check reaches
minimega), exact test names, docs wording.
