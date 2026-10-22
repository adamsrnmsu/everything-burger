# `burger update`, branch sources, and the integration board

Status: design choices approved 2026-10-22 (all four update layers; branch
sources). Bead: burger-th6.

## Goal

1. One command that takes a stale phēnix host to what is on `main`: burger
   itself, the phēnix and minimega containers, phenix-apps in the phēnix
   container, and `bennu.qc2` built from bennu `main`.
2. A board that runs the exact tree `main` will hold once the open
   phenix-apps PRs merge in the planned order (order A), conflicts resolved
   once on an `integration` branch.

## Branch sources

Boards can name a branch instead of PR numbers. Both take the same shape:

```yaml
apps:  {branch: integration}            # adamsrnmsu fork by default
apps:  {branch: main, remote: upstream}  # upstream main, no PRs
bennu: {branch: main, remote: upstream, install: [deb]}
```

- `branch` and `prs` are mutually exclusive; `bennu.pr` and `bennu.branch`
  likewise. `remote` is `upstream` (sandialabs) or `fork` (the user's fork,
  owner from `gh api user`, the same identity `burger prs` uses as `@me`);
  default `fork` for apps branches, `upstream` for bennu branches.
- A branch board declares which PRs the branch holds:
  `apps: {branch: integration, contains: [159, 161, ...]}`. Items'
  `requires.apps_prs` resolve against `contains` exactly as they do against
  `prs`, so the same items work on PR boards and branch boards. `contains` is
  also the record of the merge order.
- `sync` fetches `refs/heads/<branch>` into `refs/burger/branch/<remote>/<branch>`
  and checks it out in the board's worktree. The manifest records the sha.
- Type errors in `prs`/`pr` (non-integers) become `BurgerError`s.
- Closes burger-byo (bennu from main without a PR).

## Integration board

`switchboards/integrate.yaml` (not `integration`: `ebwes-integration` is over the 15-character experiment name limit): `apps: {branch: integration, contains: [...]}`,
on waterway: the items of `metadata`, `harden`, `iperf`, `base` and `cc-empty`
(soap-only items such as `provider`'s are left out: one route per board).
Where boards override the same component differently, `metadata`'s override
wins, plus `harden`'s tcpdump capture on the monitor VM and its quoted mm
bridge filter; the header says so. The branch goes stale whenever a PR head
moves or upstream main advances, and must be rebuilt; `docs/integration-notes.md`
records each conflict and its resolution. Goldens are generated after the
branch is pushed (it is: `adamsrnmsu:integration`, f52aac3). The header lists the
merge order and points at the notes for each resolved conflict. The branch is
upstream `main` plus #159 #161 #192 #191 #194 #155 #164 #165 #166 #167 #168
#169 #170 #171 #172 #174 #175 #176 #177 #195, as merge commits.

## `burger update`

```
./burger update [--only burger,containers,apps,bennu] [--dry-run] [--force]
```

Layers run in that order; each prints what it found (sha, image digest,
version) before and after and is skipped with a reason when there is nothing to
do. `--dry-run` prints the commands without running them. Refuses to touch
containers or images while a phēnix experiment is running, unless `--force`.

1. **burger**: refuses on a dirty checkout or a branch other than `main`;
   `git pull --ff-only`; `.venv/bin/pip install -q -e .`; if HEAD moved,
   re-executes `./burger update` with the remaining layers so the new code runs
   them.
2. **containers**: for the phēnix and minimega containers, reads the compose
   labels (`com.docker.compose.project.working_dir`,
   `.config_files`, `.service`). Compose-managed: `docker compose pull <svc>`
   then `up -d <svc>`, then waits for `phenix version` / `phenix mm version`.
   Not compose-managed: prints what to run and skips (burger does not guess
   `docker run` flags). A service built from a local context is reported, not
   rebuilt.
3. **apps**: installs upstream `main` of sceptre-phenix-apps into the phēnix
   container through the normal sync/build/install path, using a built-in
   board `main` (`apps: {branch: main, remote: upstream}`). The phēnix
   entrypoint reinstalls upstream phenix-apps on every boot, but this makes the
   installed sha explicit and recorded in `/tmp/burger/installed.json`.
4. **bennu**: builds `bennu.deb`, the libzmq/helics debs it ships with, and the
   pybennu wheel from bennu `main` (the existing bennu build, branch source).
   Fetches sceptre-phenix-images into a directory the phēnix container also
   sees at the same path (the images Makefile passes `$(PWD)` paths through
   `docker exec phenix`; default `/phenix/burger/images`, checked first), writes a copy of `scripts/bennu.sh` whose
   release downloads are replaced by the local files (copied into the image
   through an extra overlay), and runs its `bennu` target through the phēnix
   container, after `phenix image delete bennu` (its Makefile refuses when the
   image config exists). The result is installed as
   `/phenix/images/bennu-main-<sha7>.qc2`; `bennu.qc2` becomes a symlink to it.
   A real `bennu.qc2` file is first moved to `bennu-<date>.qc2`, so rollback is
   repointing the symlink (printed at the end). If any release asset has no
   local build (helics, libzmq), the release download stays and the summary
   says so.

None of this can run on the development machine: every layer is tested with
`FakeHost`, and `--dry-run` prints every host command so the first real run
can be checked step by step.

## Out of scope

- Rebuilding minirouter or the Windows images.
- Updating minimega from source (the compose service decides where its image
  comes from).
- Choosing a different phēnix image tag than the compose file names.

## Tests

`FakeHost`-driven unit tests per layer: dirty/non-main refusal, compose label
parsing and the not-compose skip, running-experiment refusal and `--force`,
dry-run output, symlink/backup handling, branch-source sync manifest, the
`prs`/`branch` exclusivity errors, and a golden render of `integrate`.
