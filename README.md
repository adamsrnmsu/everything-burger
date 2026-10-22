# everything-burger-with-extra-sauce

Live smoke test for open `sceptre-phenix-apps` and `sceptre-bennu` pull
requests. It answers one question: *with my PRs merged, does waterway still
come up and does SCORCH still run?*

A **switchboard** (`switchboards/<name>.yaml`) names a route, the PRs to merge
onto upstream `main`, and the **items** to switch on. Items are small YAML
files (`components/`, `apps/`, `bennu/`), each with its SCORCH config and the
**checks** that say what a pass means. burger merges the PR heads, installs
the result into phēnix, stands up a copy of the route's topology with a
SCORCH pipeline, and checks the results. Switchboards exist because some PRs
conflict (#155 against six of the #160 drafts) and because a failure has to
point at a PR set.

`./burger list` shows every switchboard and item with its `about` text.

**Documentation: <https://adamsrnmsu.github.io/everything-burger/>** (the run
book, switchboards, items, checks, routes, configuration, coverage, and a
reference generated from every item and switchboard file).

## Install

On the phēnix host (Python 3.10+, `git`, `docker`, the phēnix and minimega
containers, and the waterway images in `/phenix/images`: `bennu.qc2`,
`minirouter.qc2`, and the five licensed Windows images unless you pass
`--linux-only`):

```bash
python3 -m venv .venv && .venv/bin/pip install -e .      # add '.[dev]' for the tests
```

`bennu.qc2` and `minirouter.qc2` are published by
[sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images);
pull them into `/phenix/images` with
`oras pull ghcr.io/sandialabs/sceptre-phenix-images/bennu.qc2:latest` (the same
for `minirouter.qc2`). The Windows images are not public: without them, pass
`--linux-only` to `preflight`, `run` and the step commands.

`./burger` is a shim that runs `.venv/bin/burger`, and says what to do when the
venv is missing. It works from any directory: `work/` and the item folders are
found in the checkout burger was installed from.

To work on burger itself, install the git hooks once with
[prek](https://prek.j178.dev) (`uv tool install prek`):
`prek install --hook-type pre-commit --hook-type commit-msg`. They run ruff,
vulture, codespell, shellcheck, file hygiene checks and a Conventional Commits
check on the message; `prek run --all-files` runs them by hand. The tests stay
out of the hooks (they need `BURGER_TOPOLOGIES`); run `make test` before pushing.

## Quick start

```bash
./burger preflight            # host checks; add --linux-only without Windows images
./burger list                 # switchboards and items
./burger check metadata       # resolve and render offline; no phēnix needed
./burger run baseline         # sync, build, install, up, scorch, verify, down
./burger update --dry-run     # bring a stale host up to main (burger, containers, apps, bennu.qc2)
./burger run baseline --linux-only   # the same without the Windows images
```

`make` with no target lists shortcuts for the same commands: `make install`,
`make list`, `make dry-run BOARD=metadata`, `make run BOARD=baseline`, `make docs`,
`make check` and `make test`. Run them from the checkout. `make dry-run` and
`make test` read topologies from `work/repos/topologies` when it is checked out.

## Docs

The site is built from `docs/` with MkDocs Material and published to GitHub
Pages on every push to `main`. Preview it locally:

```bash
.venv/bin/pip install -e '.[docs]' && .venv/bin/mkdocs serve
```
