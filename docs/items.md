# Items

An item is one YAML file: `components/` (SCORCH components), `apps/` (phēnix
apps added to the scenario) or `bennu/` (in-VM checks and bennu config). The
fields, with comments, are in the template `burger new` copies. The ones
that matter: `about` (what it does and what a pass means), `routes` (where it
runs), `stages`, `holds`, `background`, `advisory` (failures do not fail the
board), `config` (SCORCH metadata exactly as phenix-apps expects), `requires`
and `verify`. `{route.<key>}` in an item reads the route file (`rtus`,
`provider`, `ics_port`). A `waterway:`, `soap:` or `site:` block overrides
`config` or `verify` for that route.

`tests/test_all.py` checks every item and switchboard: each has an `about`, every
routed item is on in some board, every `python:` function exists, and a typo in a
check key fails at load.

Every item file in full: [reference](reference/items.md).

A `python` check may return `None` instead of pass/fail; the row is then `WARN`.
`opendss-batched-write` does this when the VM lacks `opendssdirect`.

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
