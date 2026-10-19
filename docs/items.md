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

Every item file in full: [reference](reference/items.md).

`fifo` (`components/fifo.yaml` and `.py`) is a live check for bennu #66: a `cc`
component on the first RTU sends the simulink provider one batched `WRITE` of
`BURGER_FIFO_TAGS` (default 1000, keep it between 257 and 4096) made-up tags
with `bennu-probe` (its `--tag` takes the whole comma-separated batch), and a
python check passes only if the provider ACKs. A vm check also fails on a FIFO
error in `/etc/sceptre/log/provider.log`. Stock bennu's 64 KiB pipe fills at
the 257th record, so this item is meant to FAIL there.

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
