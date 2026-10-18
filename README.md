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

## Install

On the phēnix host (Python 3.10+, `git`, `docker`, the phēnix and minimega
containers, and the waterway images in `/phenix/images`: `bennu.qc2`,
`minirouter.qc2`, and the five licensed Windows images unless you pass
`--linux-only`):

```bash
python3 -m venv .venv && .venv/bin/pip install -e .      # add '.[dev]' for the tests
```

`./burger` is a shim that runs `.venv/bin/burger`, and says what to do when the
venv is missing. Run it from the checkout: `work/` and the item folders are
found relative to the current directory.

To work on burger itself, install the git hooks once with
[prek](https://prek.j178.dev) (`uv tool install prek`):
`prek install --hook-type pre-commit --hook-type commit-msg`. They run ruff,
vulture, codespell, shellcheck, file hygiene checks and a Conventional Commits
check on the message; `prek run --all-files` runs them by hand. The tests stay
out of the hooks (they need `BURGER_TOPOLOGIES`); run `make test` before pushing.

## Run book

```bash
./burger preflight            # host checks; add --linux-only without Windows images
./burger list                 # switchboards and items
./burger prs                  # my open PRs, the switchboard that carries each, stale syncs
./burger check metadata       # resolve and render offline; no phēnix needed
./burger run baseline         # sync, build, install, up, scorch, verify, down
./burger run bennu
./burger run pybennu
./burger run metadata
./burger run harden
./burger install baseline     # put upstream main back into phēnix when done
```

The phēnix container's entrypoint reinstalls upstream phenix-apps on every
boot, so re-run `./burger install <switchboard>` after any phenix container
restart (`verify` flags the mismatch).

`scorch` (and `run`) retries the health run until it passes or `--settle`
seconds (default 900) run out, counted from the end of the first attempt so VM
boot time is not billed to it. The cap is hard: a retry starts only if an
attempt as long as the last one would end in time. Other runs get one attempt,
so no `--settle` value rescues a hung component.

`run` stops at the first failing step and leaves the experiment up for
debugging; after `verify` it deletes the experiment unless you pass `--keep`.
To debug, run the steps by hand:

```bash
./burger sync metadata        # fetch PR heads, merge onto main, write manifest.json
./burger build metadata       # apps tarball, phenix-app-mirror, bennu.deb
./burger install metadata     # pip install into the phenix container
./burger up metadata          # render topology + scenario, create and start ebwes-metadata
./burger scorch metadata      # run 0 (health) retries until healthy, then the rest once
./burger scorch metadata --run 1
./burger verify metadata      # report: work/metadata/report.md
./burger down metadata
./burger bennu-check bennu    # bennu's own docker harness: the #56 and #57 coverage
```

Every switchboard also gets a `health` run: a SCORCH `cc` component whose
checks (the `vm` checks of the `bennu/*` items) run inside the VMs: that
`bennu-field-deviced`, the Simulink provider and solver are running, that each
RTU can query the provider, that the installed bennu is the version the
switchboard built, and (with the Windows nodes) that something is polling
Modbus on each RTU. In `harden` it also checks that the monitor VM sees
mirrored Modbus traffic.

### The site route (your own rig)

The `rtds` switchboard runs on your RTDS setup instead of waterway: the
provider VM with pybennu and NTP, and optionally Elasticsearch and the RSCAD
automation server. Point it at your topology and scenario through the
environment; nothing site-specific goes into git:

```bash
export BURGER_SITE_TOPOLOGY=/path/to/site-topology.yaml   # required
export BURGER_SITE_SCENARIO=/path/to/site-scenario.yaml   # required
export BURGER_SITE_PROVIDER=power-provider                 # provider VM hostname
export BURGER_RTDS_ES_VERIFY=true BURGER_RTDS_ES_SERVER=http://es-host:9200 BURGER_RTDS_ES_INDEX=rtds-clean
export BURGER_RTDS_RSCAD=true BURGER_RTDS_RSCAD_URL=http://rscad-host:8000
./burger run rtds
```

Elasticsearch and RSCAD are off by default. The scenario keeps its own apps and
`assetDir`; any `scorch` app in it is replaced by the switchboard's pipeline.
The provider gets the PR build of pybennu (pip, `--no-deps`) before
`sceptre-start.sh` runs. `preflight` checks the waterway images, so ignore its
image rows for this switchboard.

After you push to a PR, `./burger prs` marks the switchboards that carry it
STALE; re-run `sync` and the rest.

## Switchboards

`switchboards/<name>.yaml` (the name must keep `ebwes-<name>` within 15
characters):

| Key | Meaning |
|---|---|
| `about` | one line for `burger list` |
| `route` | `waterway`, `soap` or `site`: the topology the items run on |
| `apps.prs` | sceptre-phenix-apps PRs merged in order; `sync` fails and names the files when one conflicts with those before it |
| `bennu` | `pr`, optionally `install: [deb, pybennu]` and `pip` (dependencies the PR adds that `bennu.qc2` lacks: `build` turns them into wheels with `pip wheel --no-deps` and the VMs install them with pybennu, since they have no package index) |
| `items` | `<item>: on` switches an item on (`apps/x`, `bennu/x` for the other kinds) |
| `overrides` | per-item changes to `config` or `verify` |
| `linux_only` | same as `--linux-only` |
| `runs` | explicit run layout; by default burger builds `health` plus one run per item from the item's `stages` |

A component whose `start` polls until phēnix stops it (hoststats) needs
`background: true` in its item file and a stop entry in the run, or `start`
never returns and the run hangs; `check` refuses a switchboard that breaks
this.

| Switchboard | Apps | bennu | What it runs |
|---|---|---|---|
| `baseline` | upstream `main` | stock `bennu.qc2` | health checks only. If this fails, the rig is broken, not a PR |
| `bennu` | upstream `main` | #59 (main with #54–#58, plus loguru) deb and pybennu wheel | health checks on the PR build of bennu |
| `pybennu` | upstream `main` | #59 deb and pybennu wheel | soap without Ignition: `pybennu-power-solver` runs and logs through loguru, each C++ RTU queries it; siren's log file (advisory) |
| `metadata` | #158, #164–#177 | #58 | disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos (+ advisory erspan); lowercase mgmt VLAN for #158 |
| `harden` | #155, #158 | #58 | cc validators, pcap, tcpdump, mm, mgmt_tap, mirror app into a `monitor` VM |
| `rtds` | #158, #164, #166, #169, #177 | #59 pybennu wheel | rtds, providerdata, collector and a baseline disruption on **your rig's** topology (`site` route), not waterway |
| `provider` | #158, #164, #166, #169, #177 | stock `bennu.qc2` | providerdata, rtds (lifecycle only) and collector with a baseline disruption, on **soap**'s pybennu PyPower provider; health: the solver runs |
| `iperf` | #165 | stock `bennu.qc2` | iperf from rtu-2 to rtu-1 on waterway, after the waterway health checks; burger installs iperf3 into both |
| `ssh` | #170 | stock `bennu.qc2` | ssh to rtu-1 over `mgmt_tap`, after the waterway health checks; needs `BURGER_VM_PASSWORD` |

Models no route can run are validated offline by `phenix-scorch-validate`
(items with `routes: []` are validate-only).

## Items

An item is one YAML file: `components/` (SCORCH components), `apps/` (phēnix
apps added to the scenario) or `bennu/` (in-VM checks and bennu config). The
fields, with comments, are in the template `burger new` copies. The ones
that matter: `about` (what it does and what a pass means), `routes` (where it
runs), `stages`, `holds`, `background`, `advisory` (failures do not fail the
board), `config` (SCORCH metadata exactly as phenix-apps expects), `requires`
and `verify`. `{route.<key>}` in an item reads the route file (`rtus`,
`provider`, `ics_port`). A `waterway:`, `soap:` or `site:` block overrides
`config` or `verify` for that route.

## Checks

`verify` is a list of checks, each reading the SCORCH output copied back to
`work/<switchboard>/results/run-N/<component>/`. A check whose input is
missing fails with "no output found at …"; it never passes.

| Check | Passes when | Example |
|---|---|---|
| `status` | the component's status files have no error `return`; the background `stop` entry carries the verdict ([sceptre-phenix#457](https://github.com/sandialabs/sceptre-phenix/issues/457)) | `- status: ok` |
| `files` | a glob matches at least `min` files, each at least `min_bytes` | `- files: {glob: "*.pcap", min: 1}` |
| `pcap` | pcap/pcapng files parse and hold at least `min_packets` matching a TCP/UDP port (or protocol) | `- pcap: {glob: "*.pcap", tcp_port: 502, min_packets: 10}` |
| `json` | a JSON file's field exists, equals or matches | `- json: {glob: "*.json", field: "status", equals: "ok"}` |
| `csv` | a CSV has at least `min_rows` rows, or the named `columns` | `- csv: {glob: "*.csv", min_rows: 1}` |
| `log` | a log file contains or lacks a pattern | `- log: {glob: "*.log", lacks: "ERROR"}` |
| `vm` | a command run inside a VM over cc exits 0 | `- vm: {host: "{route.rtus}", run: "pgrep -f bennu-field-deviced"}` |
| `python` | a function in the item's folder returns pass/fail plus a message | `- python: check_totals` |

Every check reports a one-line reason and the paths it read. Items marked `advisory` report `advisory-FAIL` and do not fail the board.
A check with `windows: true` (traffic that the Windows SCADA/OPC pollers make) is `skipped` under `--linux-only`.

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

## What verify checks

1. phēnix has the switchboard's merged apps commit installed (`install` and `up`
   refuse a build that does not match the last `sync`).
2. The experiment and every VM are running (REST API).
3. `phenix-scorch-validate` accepts the switchboard's scenario and every item
   config, including the validate-only ones. Skipped when the installed code
   has no validator (switchboards without #160).
4. Every item's `verify` checks pass against its output; a component listed in
   a stage that left no status file crashed.

The report names the exact SHAs of `main`, each PR, bennu and the topologies
repo, from `work/<switchboard>/manifest.json`.

## Routes

`routes/<name>.yaml` says which topology a switchboard runs on and what items
can ask of it.

| Route | Topology | Notes |
|---|---|---|
| `waterway` | topologies repo, `waterway/` | the default rig; RTUs `rtu-1`.. |
| `soap` | topologies repo, `soap/` | needs `ignition.qc2` and `ubuntu-soaptools.qc2` |
| `site` | `BURGER_SITE_TOPOLOGY` and `BURGER_SITE_SCENARIO` | your own rig; never rewritten or copied |

Topology asset paths `/phenix/topologies/<dir>` in the waterway and soap
documents are rewritten to `<phenix dir>/topologies/burger-<dir>`, so your own
copy is not touched.

## Coverage

| PR | Covered by | Not covered |
|---|---|---|
| #155 | `harden`: cc validators, pcap, tcpdump, mm, mgmt_tap, mirror | kafka (no broker); art, ssh |
| #158 | `metadata` and `harden`; `metadata` renames the mgmt VLAN to `mgmt` | |
| #164 disruption | `metadata` (baseline); dos model offline | dos and physical live (no Kali, no OPC script) |
| #165 iperf | `iperf` switchboard (burger installs iperf3); model offline in `metadata` | Windows client |
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; model offline in `metadata` | RTDS data checks off waterway/soap |
| #177 providerdata, #169 collector | `provider` switchboard; `rtds` switchboard on your rig; models offline in `metadata` | |
| #167 pcap, #168 tcpdump, #172 mm, #173 pipe, #174 hoststats, #175 vmstats, #176 qos | `metadata` live | pcap `create_merged_pcap`/`convertToJSON` live |
| #170 ssh | `ssh` switchboard over `mgmt_tap`; model offline in `metadata` | |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| bennu #55 | every switchboard but `baseline`: field devices and the Simulink provider run the PR build | |
| bennu #56, #59 | `pybennu` switchboard (soap's provider and siren run the PR pybennu); `rtds` switchboard on your rig; `./burger bennu-check bennu` | siren with a LabJack |
| bennu #57 | `./burger bennu-check bennu` (no switchboard runs gobennu) | in an experiment |
| #159, #161, #163, #179 | CI and docs only; nothing to run | |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BURGER_PHENIX_CONTAINER` | `phenix` | container running phēnix; empty for a native install |
| `BURGER_API` | `http://localhost:3000/api/v1` | phēnix REST API |
| `BURGER_TOKEN` | unset | JWT, when phēnix auth is on |
| `BURGER_WORK` | `work/` in the current directory | clones, worktrees, builds, results |
| `BURGER_APPS_REPO`, `BURGER_BENNU_REPO`, `BURGER_TOPOLOGIES_REPO` | `work/repos/*` | use an existing clone; burger only adds `refs/burger/*` and worktrees |
| `BURGER_TOPOLOGIES` | unset | topologies clone for `check` and the tests, so they need no `sync` |
| `BURGER_MINIMEGA_CONTAINER` | `minimega` | container burger copies `miniccc` from and sends router cc commands through; empty disables both |
| `BURGER_PHENIX_DIR` | `/phenix` | phēnix's base directory (images, configs, SCORCH output) |
| `BURGER_SITE_TOPOLOGY`, `BURGER_SITE_SCENARIO` | unset | your topology and scenario, for the `site` route |
| `BURGER_SITE_PROVIDER` | `power-provider` | provider VM hostname on the `site` route |
| `BURGER_RTDS_*` | see [the site route](#the-site-route-your-own-rig) | `ES_VERIFY`, `ES_SERVER`, `ES_INDEX`, `RSCAD`, `RSCAD_URL`: values the rtds items read |
| `BURGER_VM_PASSWORD` | unset | root password of the `bennu.qc2` image, for the `ssh` switchboard only. It ends up in the rendered `scenario.json`, so keep it a lab-only value |

Fixed: upstream repos are `sandialabs/*`, PRs merge onto `main`, and the
topologies (waterway, soap) come from the topologies repo's `main`.

### Where files go

| What | Where |
|---|---|
| Clones | `work/repos/{apps,bennu,topologies}` (only `refs/burger/*` and worktrees are added) |
| Merged trees, builds, results | `work/<switchboard>/{apps,bennu,<topology>,out,results}`, plus `manifest.json`, `state.json`, `report.md` and `burger.log` |
| Topology assets in phēnix | `<phenix dir>/topologies/burger-<topology>`. The `site` route copies nothing: its scenario's paths are used as they are |
| Rendered configs, bennu build | `/phenix/burger/<switchboard>/` |
| Apps install | pip into the phēnix container; `phenix-app-mirror` into `/usr/local/bin`; marker `/tmp/burger/installed.json` |
| bennu in the VMs | `/root/burger-bennu.deb` or the pybennu wheel (plus the switchboard's `bennu.pip` wheels), installed by `/etc/phenix/startup/00-burger-bennu.sh` before phēnix's startup scripts and `sceptre-start.sh`; log `/var/log/burger-bennu.log` |
| miniccc in the VMs | `/root/burger-miniccc`, installed and started by `/etc/phenix/startup/01-burger-miniccc.sh` on the bennu nodes and `monitor` (not Windows); log `/var/log/burger-miniccc.log` |
| Packages in the VMs | an item's `debs`, downloaded for jammy at `build` into `out/debs/`, injected to `/root/burger-debs/` and installed by `/etc/phenix/startup/02-burger-debs.sh`; log `/var/log/burger-debs.log` |
| SCORCH output | `/phenix/images/ebwes-<switchboard>/files/scorch/run-N/`, copied to `work/<switchboard>/results/` |

## Limitations

- The `provider` switchboard keeps all of soap, so the rig needs `ignition.qc2`
  (licensed; not in sceptre-phenix-images) and `ubuntu-soaptools.qc2`
  (`make ubuntu-soaptools`) besides `bennu.qc2`; `preflight` only checks the
  waterway images. soap's own `mirror` app stays in its scenario, so a mirror
  post-start failure there is silent and can leave a GRE port behind
  ([sceptre-phenix-apps#187](https://github.com/sandialabs/sceptre-phenix-apps/issues/187),
  [#188](https://github.com/sandialabs/sceptre-phenix-apps/issues/188)).
- The `ssh` switchboard reaches rtu-1 through `mgmt_tap`'s tap on the host, so it
  only works when the phēnix container shares the host's network.

- burger works around these rig, image and upstream problems:
  - On the rig tested, bennu VMs never registered with cc: no `miniccc` binary
    at `/opt/minimega/bin/miniccc` and no `virtio_console` loaded. That is
    likely an image built without `phenix image inject-miniexe`, not a defect
    in `bennu.qc2` itself. `up` injects the minimega container's own `miniccc`
    either way, so the agent always matches the server.
  - phenix-apps `scorch/cc/cc.py` crashes (`'NoneType' object has no attribute
    'encode'`) when a command with a validator prints nothing; every health
    command with a validator prints something. Reported as
    [sceptre-phenix-apps#186](https://github.com/sandialabs/sceptre-phenix-apps/issues/186).
  - The vrouter app reports success when the minirouter CLI cannot reach its
    control socket, leaving router interfaces down. Once the VMs run, `up`
    re-sends each router's interface addresses over cc (busybox-safe `ip addr
    add`, UUIDs from minimega's `vm info`). Reported as
    [sceptre-phenix-images#42](https://github.com/sandialabs/sceptre-phenix-images/issues/42).
- `./burger bennu-check` does not work behind a TLS-intercepting proxy: bennu's
  `run.sh` builds its own unpatched Docker context. Run it on an unproxied host
  until sceptre-bennu's `docker/test/Dockerfile` can take site CAs; until then
  the #56/#57 test-suite coverage is outstanding on proxied hosts.
- SCORCH templating eats `${...}` in cc commands and validators; keep it out of
  any `cc` component, including the ones in `components/*.yaml`.

- Written against phēnix and minimega source; the first live run is its real test.
- `--linux-only` drops SCADA, OPC, HMI and historian, so nothing polls the RTUs
  and the Modbus and mirror checks are skipped.
- `--linux-only` in `harden` still adds the monitor VM and mirror app, but
  with no SCADA traffic its tcpdump captures nothing, so those checks are skipped.
- The erspan advisory run adds an ERSPAN port to the host's `phenix` OVS bridge
  until its cleanup stage runs.

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
BURGER_TOPOLOGIES=../sceptre-phenix-topologies make test    # tests skip without topologies
make check                                                  # ruff and vulture
```
