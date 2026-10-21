# Install

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
venv is missing. Run it from the checkout: `work/` and the item folders are
found relative to the current directory.

`make` with no target lists the shortcuts: `install`, `list`, `dry-run` and `run`
(both take `BOARD=<switchboard>`, default `baseline`), `docs`, `check`, `test` and
`format`. When `BURGER_TOPOLOGIES` is unset they use `work/repos/topologies` if
that clone exists.

To work on burger itself, install the git hooks once with
[prek](https://prek.j178.dev) (`uv tool install prek`):
`prek install --hook-type pre-commit --hook-type commit-msg`. They run ruff,
vulture, codespell, shellcheck, file hygiene checks and a Conventional Commits
check on the message; `prek run --all-files` runs them by hand. The tests stay
out of the hooks (they need `BURGER_TOPOLOGIES`); run `make test` before pushing.

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
BURGER_TOPOLOGIES=../sceptre-phenix-topologies make test    # tests skip without topologies
make check                                                  # ruff and vulture
```
