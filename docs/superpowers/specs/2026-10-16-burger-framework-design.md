# burger as a test framework: switchboards, component files, checks

Date: 2026-10-16. Status: design, reviewed; awaiting the user's approval.

## Intent

burger started as a single 1,100-line script that smoke-tests open
`sceptre-phenix-apps` and `sceptre-bennu` PRs on a live phēnix host. It works,
but every round is a hand-built YAML file mixing PR picks, SCORCH wiring,
topology tweaks and checks, and adding a component means editing several of
them plus the script.

burger becomes the base for all future SCEPTRE testing. The goal is that
testing a SCORCH component, a phēnix app, or a piece of bennu means:

1. Write (or have an AI write) **one file** for it: its configuration, the
   routes it supports, and how to verify it worked.
2. Turn it **on** in a **switchboard** and pick the **route** (waterway or
   soap) and the **PRs** to test.
3. `burger run <switchboard>` and read a **pass/fail table**, then a
   **report** with paths to every output.

The per-item files double as documentation: someone new reads one to learn
how the component is set up and what it produces, runs it, and looks at the
outputs. They are generic on purpose, so they can later move into
`sceptre-phenix-apps` next to each component without being rewritten.

## Goals

- One file per testable item (SCORCH component, phēnix app, bennu piece),
  holding config, supported routes, requirements and verification.
- A folder of saved switchboards, picked by name: PRs, route, items on/off.
- Verification that looks inside outputs (example: pcap files exist **and**
  contain Modbus packets), using a small built-in library of check types plus
  a Python escape hatch.
- Minimal burden on the tester: defaults everywhere, clear errors that name
  the file and field, `burger new` templates, `burger list`.
- Python package using loguru (logging), rich (tables, progress), pyfiglet
  plus ASCII art (banner); ruff and vulture clean.
- Every existing round and check carries over; nothing burger does today is
  lost.

## Non-goals

- Running anywhere but a phēnix host (same as today).
- Replacing `phenix-scorch-validate` or SCORCH itself; burger drives them.
- A web UI. The terminal table and a markdown report are the outputs.
- Upstreaming the component files into phenix-apps now. Only keep the format
  movable.

## Anti-goals (failure even if it "works")

- A tester has to touch Python, or more than the item file plus one
  switchboard line, to add a routine component test.
- A run fails halfway because of something knowable up front (component not
  supported on the route, unknown item name, unset required env var, a
  background component with no stop). These must fail before anything is
  built or started.
- A passing table that hides a failed check, or a check that passes because
  its output was never found.
- Losing an existing workaround (miniccc injection, router readdress, cc
  empty-stdout rule, `${...}` ban in cc strings) and rediscovering it live.

## Constraints

- Runs on the phēnix host: git, docker, the phēnix and minimega containers,
  Python 3.10+. New Python deps (loguru, rich, pyfiglet, PyYAML) install into
  a venv or via `uv`; the host has network today (git fetch, docker pull).
- SCORCH templating eats `${...}`; no such string may reach a `cc` command or
  validator. A cc command with a validator must print something
  (sceptre-phenix-apps#186).
- Experiment names stay within 15 characters (`ebwes-<name>`).
- The topologies come from the `sceptre-phenix-topologies` repo's `main`;
  their asset paths are rewritten to `burger-<topology>` so the user's own
  copy is never touched.
- Site values (RTDS rig paths, passwords) come from the environment, never
  from git.

## Concepts

### Routes

A route is a base topology and scenario:

- `waterway`: C++ field devices, Simulink provider, Windows SCADA side
  (`--linux-only` drops it).
- `soap`: pybennu PyPower provider, C++ DNP3 RTUs, siren; Ignition,
  netmonitor and attacker are optional (dropped unless an item needs them).
- `site`: the user's own topology and scenario from env vars (today's rtds
  round). Decided by Claude, not the user; it keeps the RTDS rig working.

Route facts burger already encodes (bennu nodes, RTU names, the Modbus port,
the provider host) move into a small per-route file, so items can refer to
`{route.rtus}` style values instead of hard-coding hostnames.

### Item files

Three kinds, one schema: `components/` (SCORCH components), `apps/` (phēnix
scenario apps such as `mirror`, `mgmt_tap`), `bennu/` (bennu behaviors such as
`field-device`, `simulink-provider`, `pybennu-provider`, `siren`).

Illustrative shape (field names are the builder's to finalize):

```yaml
# components/pcap.yaml
name: pcap
kind: scorch          # scorch | app | bennu
type: pcap            # the SCORCH component type
about: >
  Captures packets on a VM interface for the length of the run and leaves
  one .pcap per VM in the run's pcap/ folder.
routes: [waterway, soap]
stages: [start, stop]          # where it sits in the SCORCH run
config:                        # SCORCH metadata exactly as phenix-apps expects
  vms: [{hostname: "{route.rtus[0]}", interface: 0}]
requires: {}                   # debs, extra apps, monitor VM, env vars, PRs
verify:
  - status: ok                 # implied for every SCORCH item
  - files: {glob: "pcap/*.pcap", min: 1}
  - pcap: {glob: "pcap/*.pcap", tcp_port: "{route.ics_port}", min_packets: 10}
waterway: {}                   # per-route overrides of config/verify
soap: {}
```

- **stages / ordering**: burger composes the SCORCH run from the enabled
  items. Items declare their stages and two flags today's rounds already need:
  `holds: true` (blocks `start` for its duration, like a baseline disruption;
  ordered last in `start`) and `background: true` (polls until stopped; must
  have a `stop` entry, as today). A switchboard may give explicit `runs` for
  the rare case auto-composition gets wrong.
- **advisory: true** puts the item in an `extras-*` run whose failures do not
  fail the switchboard (today: erspan, siren's log file).
- **requires** gathers what today lives on rounds: jammy debs into named VMs
  (iperf), extra phēnix apps (ssh needs `mgmt_tap`), the monitor VM (mirror),
  env vars (`BURGER_VM_PASSWORD`), dropped/kept soap nodes, and the apps PRs
  the component needs (a #160 component on a switchboard without its PR fails
  up front).
- **bennu items** carry in-VM checks instead of SCORCH config: they become
  the `health` cc component, exactly as today's waterway checks and the
  `pybennu` round's checks.
- **phēnix app items** carry scenario app config (and hosts); they verify via
  `vm`/`health`-style checks and the experiment state.

### Checks

Built-in check types, each reading the SCORCH output burger copies back to
`work/<switchboard>/results/run-N/<component>/`:

| Check | Passes when |
|---|---|
| `status` | the component's status files have no error `return` (today's rule, including the background `stop`-carries-the-verdict case) |
| `files` | a glob matches at least `min` files, optionally each at least `min_bytes` |
| `pcap` | pcap/pcapng files parse and contain at least `min_packets` matching a TCP/UDP port (or GRE); parsed in Python, no tools on the host |
| `json` | a JSON file's field exists / equals / matches |
| `csv` | a CSV has at least N rows, or named columns |
| `log` | a log file contains or lacks a pattern (today's ERROR-lines warning becomes this) |
| `vm` | a command run inside a VM over cc exits 0 (today's health checks) |
| `python` | a function in the item's folder returns pass/fail plus a message |

A check whose input is missing fails with "no output found at …", never
passes. Every check reports a one-line reason and the paths it read.

`phenix-scorch-validate` against all item configs stays as a pre-flight check
when the installed apps have it (today's `validate`).

### Switchboards

```yaml
# switchboards/metadata.yaml
about: "#160 metadata models on waterway"
route: waterway
apps: {prs: [158, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177]}
bennu: {pr: 59, install: [deb], pip: []}
items:
  disruption: on
  pcap: on
  tcpdump: on
  erspan: on        # advisory in its own file
  bennu/field-device: on
  bennu/simulink-provider: on
overrides:
  pcap: {config: {vms: [{hostname: rtu-2, interface: 0}]}}   # optional
```

Items not listed are off. Overrides are rare and merge onto the item file.
PR sets that conflict live in separate switchboards, as rounds do today.

### Commands

- `burger run <switchboard>`: sync, build, install, up, scorch, verify, down
  (today's `round`, which stays as an alias).
- The individual steps stay: `sync`, `build`, `install`, `up`, `scorch`,
  `verify`, `down`, plus `preflight`, `prs`, `bennu-check`.
- `burger list`: switchboards, and items with their routes and kinds.
- `burger new component|app|bennu <name>`: a commented template.
- `burger check <switchboard>`: render and validate offline (no phēnix), the
  thing an AI or CI runs after writing an item file.

### Output

- On start: pyfiglet "BURGER" plus ASCII art of a burger-boy mascot holding a
  burger, inspired by the user's screenshot. Original art, without the
  trademarked "BIG BOY" lettering (Claude's call; the user can ask for it).
  Suppressed when stdout is not a terminal or with `--quiet`.
- During a run: rich progress per step; loguru logs to stderr and to
  `work/<switchboard>/burger.log`.
- At the end: a rich pass/fail table (item, check, result, one-line reason),
  then `work/<switchboard>/report.md` with the same table, the SHAs from the
  manifest, and the path to every output the checks read.

## Carrying over what exists

Every current round becomes a switchboard, and every check in it becomes an
item check:

| Round | Switchboard | Items |
|---|---|---|
| `baseline` | `baseline` | bennu field-device, simulink-provider (stock bennu) |
| `bennu` | `bennu` | the waterway bennu items on #59 |
| `pybennu` | `pybennu` | soap: pybennu-provider, field-device (DNP3), siren (advisory) |
| `metadata` | `metadata` | disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos, erspan (advisory); offline model validation |
| `harden` | `harden` | cc validators, pcap, tcpdump, mm, apps mgmt_tap and mirror (monitor VM) |
| `rtds` | `rtds` | site route: rtds, providerdata, collector, disruption, pybennu-provider |
| `provider` | `provider` | soap: rtds (lifecycle), providerdata, collector, disruption |
| `iperf` | `iperf` | iperf (requires iperf3 debs on two RTUs) |
| `ssh` | `ssh` | ssh (requires mgmt_tap, `BURGER_VM_PASSWORD`) |

Behavior that must survive unchanged: PR fetch into `refs/burger/*` and merge
with conflict reporting; the union merge for CHANGELOG; `prs` staleness; bennu
deb and pybennu wheel builds, extra wheels (`pip`), and injection via
`/etc/phenix/startup/00-*`; miniccc injection; jammy debs; router readdress
over cc; `--settle` hard cap and health retries; asset path rewriting;
`--linux-only`; `--keep`; `drop_nodes`; the `${...}` and empty-stdout cc rules;
the 15-character experiment name check; the tests in `tests/test_burger.py`
(ported, not dropped). The `work/` layout may change paths but `report.md`
and `manifest.json` keep their content.

## Package and tooling

- `src/burger/` package with a `burger` console script; the repo-root
  `./burger` keeps working as a thin shim, so the README run book still works.
- Dependencies: loguru, rich, pyfiglet, PyYAML. Dev: ruff, vulture, pytest.
  `make check` (ruff check, ruff format --check, vulture) and `make test`.
- Item and switchboard files are validated on load with errors that name the
  file and field.

## Calls made after the builder review

These settle questions a builder raised. They are Claude's calls unless
marked otherwise.

- **Offline tier first.** Everything up to "start the experiment" (loading,
  validating, merging items, rendering topology and scenario, composing runs)
  is pure Python with no docker, phēnix, git or network, behind a thin seam
  for subprocess, docker and the phēnix REST API. `burger check` and the test
  suite run that tier in CI.
- **Acceptance.** For each old round, the new switchboard renders the same
  topology and the same non-SCORCH scenario apps as the old script (golden
  files generated from the old script before it is retired). The SCORCH
  pipeline must be functionally equivalent: same components, same configs,
  stage membership and `holds`/`background` rules kept, but not necessarily
  the same order. The user does the live run of each switchboard on the
  phēnix host.
- **Item layout and the `python` check.** An item is `<kind>/<name>.yaml`,
  with an optional sibling `<kind>/<name>.py`. A `python` check names a
  function in that file. It is called in-process as `fn(ctx)` and returns
  `(ok, message)`. `ctx` carries the item's output dir, the route values, the
  manifest and the switchboard. This is trusted repo code.
- **Output layout.** SCORCH output is copied as `collect` does today:
  `results/run-N/<component>/loop-*/`. Check globs are relative to the
  component's `loop-*` directories, so `pcap/*.pcap` means
  `run-N/<component>/loop-*/pcap/*.pcap`. The builder confirms each existing
  component's real file names against one live run's output, and corrects the
  item files to match.
- **Health.** All enabled items' `vm` checks merge into one `health` cc
  component, with `start` commands grouped per hostname. The install and
  version checks for the bennu build come first, as today. A `vm` check passes
  on exit 0. An optional `expect: <regex>` is compiled into the command as a
  `grep`, not as a cc validator, so no user command can hit the cc
  empty-stdout crash. Burger's own install and version checks keep their
  validators and always print. Any `${` in a `vm` command is rejected at load
  time.
- **Run order.** Within a stage, items run in switchboard listing order,
  with `holds` items last in `start`. `stop` uses the same order. No `after:`
  field; the explicit `runs` override covers the rest.
- **models.yaml.** Every item config is validated with
  `phenix-scorch-validate` when the installed apps have it. Models no route
  can run (dos and physical disruption, kafka) become items with
  `routes: []`, meaning validate-only. A switchboard may enable a
  validate-only item. `burger list` marks them, and they never enter the
  pipeline.
- **site route.** Its route values (provider host and the like) come from
  `BURGER_SITE_*` env vars, alongside the topology and scenario paths. An item
  runs on `site` only if it lists `site` in `routes`. Hostnames are checked at
  render time. Render is offline and happens before build, so this still
  fails before anything is built.
- **PRs.** `requires: {apps_prs: [...]}` is a plain number check against the
  switchboard's `apps.prs`. bennu `includes` goes away, since #54–#58 are
  merged.
- **Switchboard schema.** `about`, `route`, `apps`, `bennu`, `items`,
  `overrides`, an optional `runs`, and `linux_only`. Today's round-level
  `monitor`, `apps_extra`, `debs`, `drop_nodes`, `cc_nodes`, `mgmt_vlan` and
  `health` come from items' `requires` and the route file. `mgmt_vlan` becomes
  an override on the harden/metadata items that need it.
- **Migration.** The step commands take a switchboard name. `work/<name>`
  keeps its name, and `round <name>` aliases `run <name>`. The old script and
  `rounds/` stay runnable until every switchboard passes golden parity, then
  both are deleted (the user's decision), along with the golden-file
  generator's dependency on them (the golden files stay as fixtures).
- **Install.** pip works on the phēnix host (the user's answer): burger
  installs into a venv with `pip install -e .`; the `./burger` shim uses
  that venv.

## Left to the builder

- Module layout inside `src/burger/`, data classes vs a schema library, and
  the exact YAML field names, as long as the shapes above hold.
- How `{route.…}` values are expressed and substituted.
- The auto-composition algorithm for SCORCH runs, within the stage, `holds`,
  `background` and `advisory` rules.
- The pcap parser implementation (stdlib `struct` is enough for pcap; pcapng
  if SCORCH or minimega produce it).
- The mascot art itself.
- Migration order, as long as each step keeps the existing rounds runnable
  until its switchboard replaces it.
