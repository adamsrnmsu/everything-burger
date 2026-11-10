# What verify checks

1. phēnix has the switchboard's merged apps commit installed (`install` and `up`
   refuse a build that does not match the last `sync`).
2. The experiment and every VM are running (REST API).
3. `phenix-scorch-validate` accepts the switchboard's scenario and every item
   config, including the validate-only ones. Skipped when the installed code
   has no validator (switchboards without #160).
4. Every item's `verify` checks pass against its output; a component listed in
   a stage that left no status file crashed.

5. Field-device logs (below): a `field-device logs` row, `ok`, `WARN` or `FAIL`.

The report names the exact SHAs of `main`, each PR, bennu and the topologies
repo, from `work/<switchboard>/manifest.json`.

## Field-device logs

After every `verify`, and when `run` fails after `up` (before teardown), burger
collects the field-device logs of every VM in the route's `rtus` over cc: the
stdout and stderr files of each running `bennu-field-device(d)` (resolved through
`/proc/<pid>/fd/1` and `/2`), the watcher logs in `/etc/sceptre/log/`, and the
`<proto>-server.log` files in the device's cwd. Each file is cut to its last
2 MB on the VM (at most 40 files per host, growth sampled over 3 s), so a 6 GB
flood log cannot stall burger. They land in
`work/<switchboard>/results/fdlogs/<host>/`, with the VM's `_manifest.json` and a
`summary.json` of the scan (also stored as `fdlogs` in `manifest.json`).

The scan counts crash-class lines (`terminate called`, `Aborted`, `Segmentation
fault`, `core dumped`, `double free`, `AddressSanitizer`, `ThreadSanitizer`,
`panic`) and error-class lines (`Too many open files`, `E: `, `error`, `failed`),
collapses repeats (digits ignored) with a count and first/last timestamp, and
flags a flood: a file growing faster than `flood_bytes_per_min`, or one line
repeated more than `repeat` times. `report.md` gets a "Field-device logs" table
with one row per host: files, sizes, counts, flood and the top 3 distinct lines.

The row is `WARN` for any finding and `FAIL` only as `fail_on` says (`crash`:
crash-class lines; `error`: also error-class lines; `none`: never). Boards that
install a bennu deb from a PR or branch default to `crash`; override with
`fdlogs: {fail_on: none}` in the switchboard. A host that cannot be reached gives
a `WARN`, never a lost verdict.
