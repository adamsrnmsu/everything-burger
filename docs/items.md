# Items

An item is one YAML file: `components/` (SCORCH components), `apps/` (phēnix
apps added to the scenario) or `bennu/` (in-VM checks and bennu config). The
fields, with comments, are in the template `burger new` copies. The ones
that matter: `about` (what it does and what a pass means), `routes` (where it
runs), `stages`, `holds`, `background`, `advisory` (failures do not fail the
board), `expect_error` (a regex the component's failure must match: that is the pass; any other outcome, `sys.exit` included, fails), `expect_invalid` (a regex: the config must be *rejected*, see below), `config` (SCORCH metadata exactly as phenix-apps expects), `requires`
and `verify`. `{route.<key>}` in an item reads the route file (`rtus`,
`provider`, `ics_port`). A `waterway:`, `soap:` or `site:` block overrides
`config` or `verify` for that route.

`tests/test_all.py` checks every item and switchboard: each has an `about`, every
routed item is on in some board, every `python:` function exists, and a typo in a
check key fails at load.

Every item file in full: [reference](reference/items.md).

A `python` check may return `None` instead of pass/fail; the row is then `WARN`.
`opendss-batched-write` does this when the VM lacks `opendssdirect`.

`fifo` (`components/fifo.yaml` and `.py`) is a live check for bennu #66: a `cc`
component on the first RTU sends the simulink provider one batched `WRITE` of
`BURGER_FIFO_TAGS` (default 1000, keep it between 257 and 4096) made-up tags
with `bennu-probe` (its `--tag` takes the whole comma-separated batch), and a
python check passes only if the provider ACKs. A vm check also fails on a FIFO
error in `/etc/sceptre/log/provider.log`. Stock bennu's 64 KiB pipe fills at
the 257th record, so this item is meant to FAIL there.

## Configs that must be rejected

Some bad configs fail validation or stop the experiment before SCORCH runs, so
no board can run them and `expect_error` cannot see them. Give a validate-only
item (`routes: []`, kind `scorch` or `app`) `expect_invalid: <regex>`: it passes
when the config is rejected with output matching the regex, and fails if it is
accepted or rejected for another reason.

- **scorch**: `phenix-scorch-validate` must report the component `invalid`.
- **app**: the app's `configure` and `pre-start` stages run with `--dry-run` on
  the rendered topology; one must exit non-zero.

`./burger check <board>` judges them offline on the board's synced apps tree
(`./burger sync <board>` first) with `uv run --with work/<board>/apps/src/python`;
it prints `skipped` when the tree is not synced or `uv` is missing, and exits 1
on a FAIL. `./burger verify` judges the scorch ones from the validator inside
phēnix it already runs (its models.yaml carries them, and a rejection they were
expected to get no longer fails the board) and the app ones with `uv`, as `check`
does. Examples: `vmstats-bad-name`, `qos-bad-rate`, `helics-no-broker`.

## Adding a test

```bash
./burger new scorch my-test   # or: app, bennu; copies a commented template to components/my-test.yaml
$EDITOR components/my-test.yaml
./burger check metadata       # offline: resolves and renders the switchboard
```

Then add `my-test: on` under `items:` in a switchboard, and run
`./burger check <switchboard>` again. `check` writes the rendered
`topology.json` and `scenario.json` to `work/<switchboard>/check/`; set
`BURGER_TOPOLOGIES` to a local clone of sceptre-phenix-topologies so it does
not need `sync`.
