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

`modbus-churn` (`components/modbus-churn.yaml` and `.py`) is a live check for
bennu #79: a `cc` command on the first RTU opens, reads one register from and
closes `BURGER_CHURN_CONNS` (default 1500, above the 1024 fd soft limit) Modbus
TCP connections to its own field device, stopping at the first failure or after
`BURGER_CHURN_CAP_S` (120) seconds so a broken server cannot fill the disk. It
passes only if the field device's socket fds grew by at most
`BURGER_CHURN_SLACK` (10), it still answers a read, and its stderr log grew by
less than `BURGER_CHURN_MAX_LOG_BYTES` (5000000). Stock bennu leaks one fd per
connection and fails. The `fdleak` board runs it on the arm64 range.

`modbus-badframes` (`components/modbus-badframes.yaml` and `.py`) is a live check for
bennu #81 on the first RTU's field device: a valid read followed by the same read cut
after the function code (half-close), and one connection each with an MBAP length of
0, 4000 and 65535. It passes only if none of them got a Modbus response, the field
device kept its pid, a normal read still answers, and its stderr log grew by less than
`BURGER_BADFRAMES_MAX_LOG_BYTES` (1000000). Stock bennu answers the truncated request.

`iec104-masters` and `dnp3-accept` (also for #81) cover protocols the RTU images do not
serve. Each starts a second `bennu-field-device` on `rtu-2` with a trimmed copy of bennu's
own `data/configs/ep/iec60870-5-104-server.xml` or `dnp3-server.xml`, on a spare port of
127.0.0.1 (25404, 20999), so the route's traffic is untouched. `iec104-masters`: master A
must keep receiving the 1 s reverse poll while B connects and after B closes, and C must
receive after A closes. `dnp3-accept`: the outstation's fd limit is lowered with
`prlimit` to the lowest free fd so `accept()` fails with EMFILE, then restored; a link
status request must be answered afterwards. `prlimit` on another process needs root or
CAP_SYS_RESOURCE: the cc exec runs as root on the RTU image, and the item fails naming
that if it does not. Stock bennu fails both. The `robust` board runs all three.

`logctl` (`components/logctl.yaml` and `.py`) is a live check for bennu #80 on the first RTU's
field device. The cc command runs as root and drops to the user the process runs as, then drives
`<exe> log --pid N ...`: socket directory 0700 and socket 0600 owned by that user; `status`;
`on --file /tmp/burger-fd.log` three times (the 2nd and 3rd must say "already logging to", the
process must hold exactly one fd on the file, and no line of the file may occur more often than in
the default output); `tail --level debug` for 5 s must receive lines; `level debug --for 20s` must
show debug and revert after the timeout; `on --max-size 20K --keep 2` at debug must keep the rotated
files under about 3x the cap (the wait for output is capped at 50 s); `off` twice (the second
"already off") and the level restored. Every command but `tail` must print the
`pid N (name): logging to <dest>, level L` line. It never runs the binary unless the process has a
socket in `/tmp/bennu-<uid>/`, so stock bennu fails with "no log control socket". Files in `/tmp`
are deleted at the end. The `logctl` board runs it.

`bennu/deb-arm64` is a manifest check for bennu #78 (no VM command). On an arm64 route burger
builds the deb natively and records what cpack wrote: `deb_file`, `deb_arch` and `relabel_needed`
(true when `Architecture` was not `arm64` and burger had to relabel it, the workaround for the
hard-coded `amd64`). It passes only if `relabel_needed` is false and the file name ends
`_arm64.deb`. The `arm64deb` board runs it with the waterway items.

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
it prints `skipped` when the tree is not synced or (scorch items) `uv` is missing,
and exits 1 on a FAIL. Without `uv` on the host the app items run the `phenix-app-*` commands already installed
in phēnix instead, but only when `burger install` put this board's apps there
(same commit); otherwise they are `skipped` with that reason. `./burger verify` judges the scorch ones from the validator inside
phēnix it already runs (its models.yaml carries them, and a rejection they were
expected to get no longer fails the board) and the app ones as `check` does. Examples: `vmstats-bad-name`, `qos-bad-rate`, `helics-no-broker`, `pipe-bad-name`.

## Adding a test

```bash
./burger new scorch my-test   # or: app, bennu; copies a commented template to components/my-test.yaml
$EDITOR components/my-test.yaml
./burger check main       # offline: resolves and renders the switchboard
```

Then add `my-test: on` under `items:` in a switchboard, and run
`./burger check <switchboard>` again. `check` writes the rendered
`topology.json` and `scenario.json` to `work/<switchboard>/check/`; set
`BURGER_TOPOLOGIES` to a local clone of sceptre-phenix-topologies so it does
not need `sync`.
