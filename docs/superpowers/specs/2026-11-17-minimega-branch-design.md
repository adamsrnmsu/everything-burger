# Run switchboards on a proposed minimega change (burger-ud6)

## Why

A proposed minimega change (a PR or a branch on the fork) should be checked
against sceptre-phenix before it merges, the way burger already checks bennu and
phenix-apps PRs: build it, run the switchboards we already have on it, and see
whether phenix still works.

## Where minimega reaches phenix

1. The minimega daemon: the compose service `minimega`, image
   `ghcr.io/sandia-minimega/minimega:master`.
2. `minimega.py`: the phenix image pip-installs it from that image's
   `/opt/minimega/lib`; the apps and SCORCH use it.
3. miniccc in the VMs: `burger up` already copies it out of the minimega
   container, so it follows the swap with no change.

The phenix Go binary does not import the minimega repo (go.mod only has
activeshadow/libminimega), so phenix is not rebuilt.

## Goals, non-goals

- Goal: any switchboard runs on a chosen minimega PR or branch, and the report
  says which minimega it ran on.
- Goal: the host is back on stock minimega when the experiment is gone.
- Non-goal: building from a local, unpushed checkout. The build runs on the
  phēnix host; push to the fork first (or point `BURGER_MINIMEGA_REPO` at a clone
  on that host).
- Non-goal: rebuilding the phenix image.
- Anti-goal: a green board that silently ran on stock minimega.

## Approaches

- A. A `--minimega` flag on `run` and `sync`, for any board.
- B. A `minimega:` key in the switchboard YAML, like `bennu:`.

Chosen (by the user): both. When both are set the flag wins, and burger warns
that it overrides the board's value, naming that value.

## Design

**Selecting.** YAML: `minimega: {pr: 1782}` or
`minimega: {branch: NAME, remote: fork|upstream}` (remote defaults to fork),
checked by the same `_source` rules as bennu. Flag: `--minimega pr:1782`,
`--minimega branch:NAME` or `branch:NAME@upstream`. On a board with its own
value the flag logs a warning:
`--minimega pr:1782 overrides switchboard 'x' minimega branch:foo (fork)`.

**Sync.** Fetches the ref into `work/repos/minimega` with the existing
`_fetch`/`_fetch_branch` (minimega added to `SLUGS`), makes a fresh worktree at
`work/<board>/minimega`, and records
`manifest["minimega"] = {pr|branch, remote, sha, from: "flag"|"board"}`.
The later steps read the manifest, so `build`, `install`, `up` and `verify` need
no flag.

**Build.** `docker build -f docker/Dockerfile` of the worktree through
`site.build_dockerfile` (it learns a `dockerfile` argument), tagged
`burger/minimega:<sha12>`; skipped when that tag exists. minimega's Dockerfile
runs its own check, build, test and doc scripts, so a change that breaks
minimega fails here, before any VM starts.

**Swap (in `install`, after the apps).** A no-op when the running container
already carries this sha (the re-test loop `sync, build, install` with the
board still up). Otherwise it refuses while any experiment runs
(`guard_experiments`): recreating minimega kills the VMs. Writes `work/minimega/compose.override.yml`
(`image: burger/minimega:<sha12>`, label `burger.minimega=<sha>`), recreates the
service with the original compose files plus the override
(`up -d --no-deps --force-recreate minimega`), waits with `wait_phenix`, then
pip-installs the new image's `/opt/minimega/lib` into the phenix container
(`--no-deps --force-reinstall`).

**Restore (in `down`, after the experiment is deleted).** If the minimega
container carries the `burger.minimega` label: recreate it from the original
compose files only, wait, and reinstall `minimega.py` from the stock container.
`compose_labels` drops burger's override from `config_files`, so
`burger update --only containers` also restores. `install` for a board with no
minimega restores a leftover swap (a killed run) before anything else.
A failing or `--keep` run leaves its experiment up, so minimega stays swapped
until `./burger down <board>`; the summary says so.

**Verify.** Two rows when the manifest has minimega: the running minimega
container's `burger.minimega` label is the manifest sha, and the phenix
container's `minimega.py` matches the built image's file (sha256). Either
mismatch is a FAIL.

**`burger prs`.** Lists minimega PRs the boards carry, like apps and bennu.

## Left to the builder

- Output layout of the compose override and the exact log wording.
- Whether `minimega -e version` shows the commit inside a worktree build (the
  worktree's `.git` is a file outside the build context); verify does not rely
  on it.

## Testing

Unit tests with the existing fakes: YAML parsing and errors, flag parsing and
the override warning, the manifest from sync, the build/swap/restore command
sequences, the verify rows, and the `compose_labels` filter. Docker is not on
this machine, so a live run on the phēnix host is a separate bead.
