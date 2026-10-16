# everything-burger-with-extra-sauce

Live smoke test for open `sceptre-phenix-apps` and `sceptre-bennu` pull
requests. It answers one question: *with my PRs merged, does waterway still
come up and does SCORCH still run?*

Each **round** merges a set of PR heads onto upstream `main`, installs the
result into phēnix, stands up a copy of the waterway topology with a SCORCH
pipeline, and checks the results. Rounds exist because some PRs conflict
(#155 against six of the #160 drafts) and because a failure has to point at
a PR set.

| Round | Apps | bennu | What it runs |
|---|---|---|---|
| `baseline` | upstream `main` | stock `bennu.qc2` | health checks only. If this fails, the rig is broken, not a PR |
| `bennu` | upstream `main` | #58 (stack #54–#58) | health checks on the PR build of bennu |
| `metadata` | #158, #164–#177 | #58 | disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos (+ advisory erspan); all 14 models validated offline; lowercase mgmt VLAN for #158 |
| `harden` | #155, #158 | #58 | cc validators, pcap, tcpdump, mm, mgmt_tap, mirror app into a `monitor` VM |
| `rtds` | #158, #164, #166, #169, #177 | #58 pybennu wheel | rtds, providerdata, collector and a baseline disruption on **your RTDS rig's** topology, not waterway |

Every round also gets a `health` run: a SCORCH `cc` component whose validators
check, inside the VMs, that `bennu-field-deviced`, the Simulink provider and
solver are running, that each RTU can query the provider, that the installed
bennu is the version the round built, and (with the Windows nodes) that
something is polling Modbus on each RTU. In `harden` it also checks that
the monitor VM sees mirrored Modbus traffic. In `rtds` it only checks that the
PR build of pybennu installed on the provider.

## Run book

Run on the phēnix host. It needs `git`, `docker`, Python 3.10+ with PyYAML
(`apt install python3-yaml`), the phēnix and minimega containers, and the
waterway images in `/phenix/images` (`bennu.qc2`, `minirouter.qc2`, and the
five licensed Windows images unless you pass `--linux-only`).

```bash
./burger preflight            # host checks; add --linux-only without Windows images
./burger prs                  # my open PRs, the round that carries each, stale syncs
./burger round baseline       # sync, build, install, up, scorch, verify, down
./burger round bennu
./burger round metadata
./burger round harden
./burger install baseline     # put upstream main back into phēnix when done
```

The phēnix container's entrypoint reinstalls upstream phenix-apps on every
boot, so re-run `./burger install <round>` after any phenix container restart
(`verify` flags the mismatch).

`scorch` (and `round`) retries the health run until it passes or `--settle`
seconds (default 900) run out, counted from the end of the first attempt so VM
boot time is not billed to it. The cap is hard: a retry starts only if an
attempt as long as the last one would end in time. Other runs get one attempt,
so no `--settle` value rescues a hung component.

`round` stops at the first failing step and leaves the experiment up for
debugging; after `verify` it deletes the experiment unless you pass `--keep`.
To debug, run the steps by hand:

```bash
./burger sync metadata        # fetch PR heads, merge onto main, write manifest.json
./burger build metadata       # apps tarball, phenix-app-mirror, bennu.deb
./burger install metadata     # pip install into the phenix container
./burger up metadata          # render topology + scenario, create and start ebwes-metadata
./burger scorch metadata      # run 0 (health) retries until healthy, then the rest once
./burger scorch metadata --run 1
./burger verify metadata      # report: work/rounds/metadata/report.md
./burger down metadata
./burger bennu-check bennu    # bennu's own docker harness: the #56 and #57 coverage
```

### The rtds round

It runs on your RTDS setup instead of waterway: the provider VM with pybennu
and NTP, and optionally Elasticsearch and the RSCAD automation server. Point it
at your topology and scenario through the environment; nothing site-specific
goes into git:

```bash
export BURGER_RTDS_TOPOLOGY=/path/to/site-topology.yaml   # required
export BURGER_RTDS_SCENARIO=/path/to/site-scenario.yaml   # required
export BURGER_RTDS_PROVIDER=power-provider                 # provider VM hostname
export BURGER_RTDS_ES_VERIFY=true BURGER_RTDS_ES_SERVER=http://es-host:9200 BURGER_RTDS_ES_INDEX=rtds-clean
export BURGER_RTDS_RSCAD=true BURGER_RTDS_RSCAD_URL=http://rscad-host:8000
./burger round rtds
```

Elasticsearch and RSCAD are off by default. The scenario keeps its own apps and
`assetDir`; any `scorch` app in it is replaced by the round's pipeline. The
provider gets the PR build of pybennu (pip, `--no-deps`) before
`sceptre-start.sh` runs. `preflight` checks the waterway images, so ignore its
image rows for this round.

After you push to a PR, `./burger prs` marks the rounds that carry it STALE;
re-run `sync` and the rest of the round.

## What verify checks

1. phēnix has the round's merged apps commit installed (`install` and `up`
   refuse a build that does not match the last `sync`).
2. The experiment and every VM are running (REST API).
3. `phenix-scorch-validate` accepts the round's scenario and
   `rounds/models.yaml`, which holds one block for each #160 component,
   including the ones waterway cannot run. Skipped when the installed code has
   no validator (rounds without #160).
4. Every component listed in every stage of every run left a status file whose
   `return` is not an error. A missing file means the component crashed.
   `ERROR` lines in a passing stage are reported as `WARN`. Runs named
   `extras*` are advisory.

The report names the exact SHAs of `main`, each PR, bennu and the topologies
repo, from `work/rounds/<round>/manifest.json`.

## Coverage

| PR | Covered by | Not covered |
|---|---|---|
| #155 | `harden`: cc validators, pcap, tcpdump, mm, mgmt_tap, mirror | kafka (no broker); art, ssh |
| #158 | `metadata` and `harden`; `metadata` renames the mgmt VLAN to `mgmt` | |
| #164 disruption | `metadata` (baseline); dos model offline | dos and physical live (no Kali, no OPC script) |
| #165 iperf | offline model only | live: `iperf3` is not in `bennu.qc2`; Windows client |
| #166 rtds, #177 providerdata, #169 collector | `rtds` round on your rig; models offline in `metadata` | |
| #167 pcap, #168 tcpdump, #172 mm, #173 pipe, #174 hoststats, #175 vmstats, #176 qos | `metadata` live | pcap `create_merged_pcap`/`convertToJSON` live |
| #170 ssh | offline model only | live: no host route to the VMs without mgmt_tap |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| bennu #55 | every round but `baseline`: field devices and the Simulink provider run the PR build | |
| bennu #56 | `rtds` round (the provider runs the PR pybennu); `./burger bennu-check bennu` | |
| bennu #57 | `./burger bennu-check bennu` (no round runs gobennu) | in an experiment |
| #159, #161, #163, #179 | CI and docs only; nothing to run | |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BURGER_PHENIX_CONTAINER` | `phenix` | container running phēnix; empty for a native install |
| `BURGER_API` | `http://localhost:3000/api/v1` | phēnix REST API |
| `BURGER_TOKEN` | unset | JWT, when phēnix auth is on |
| `BURGER_WORK` | `work/` next to `burger` | clones, worktrees, builds, results |
| `BURGER_APPS_REPO`, `BURGER_BENNU_REPO`, `BURGER_TOPOLOGIES_REPO` | `work/repos/*` | use an existing clone; burger only adds `refs/burger/*` and worktrees |
| `BURGER_MINIMEGA_CONTAINER` | `minimega` | container burger copies `miniccc` from and sends router cc commands through; empty disables both |
| `BURGER_PHENIX_DIR` | `/phenix` | phēnix's base directory (images, configs, SCORCH output) |
| `BURGER_ASSETS` | `<phenix dir>/topologies/burger-waterway` | where the waterway copy goes |
| `BURGER_RTDS_*` | see [the rtds round](#the-rtds-round) | site values for `rtds` |

Fixed: upstream repos are `sandialabs/*`, PRs merge onto `main`, and waterway
comes from the topologies repo's `main`.

### Where files go

| What | Where |
|---|---|
| Clones | `work/repos/{apps,bennu,topologies}` (only `refs/burger/*` and worktrees are added) |
| Merged trees, builds, results | `work/rounds/<round>/{apps,bennu,waterway,out,results}`, plus `manifest.json` and `report.md` |
| waterway assets in phēnix | `$BURGER_ASSETS` (`/phenix/topologies/burger-waterway`); the scenario's `assetDir` and inject paths are rewritten to it, so your own `/phenix/topologies/waterway` is not touched. The `rtds` round copies nothing: its scenario's paths are used as they are |
| Rendered configs, bennu build | `/phenix/burger/<round>/` |
| Apps install | pip into the phēnix container; `phenix-app-mirror` into `/usr/local/bin`; marker `/tmp/burger/installed.json` |
| bennu in the VMs | `/root/burger-bennu.deb` or the pybennu wheel, installed by `/etc/phenix/startup/00-burger-bennu.sh` before phēnix's startup scripts and `sceptre-start.sh`; log `/var/log/burger-bennu.log` |
| miniccc in the VMs | `/root/burger-miniccc`, installed and started by `/etc/phenix/startup/01-burger-miniccc.sh` on the bennu nodes and `monitor` (not Windows); log `/var/log/burger-miniccc.log` |
| SCORCH output | `/phenix/images/ebwes-<round>/files/scorch/run-N/`, copied to `work/rounds/<round>/results/` |

## Adding a round or a PR

A round is `rounds/<name>.yaml` (the name must keep `ebwes-<name>` within 15
characters): `apps.prs` merged in order, optional `bennu.pr`, and optional
`scorch.components` and `scorch.runs` appended after the health run. A
`base: {topology, scenario}` replaces waterway, `bennu.install` picks `deb`
and/or `pybennu`, and `bennu.nodes` the VMs that get it. `${VAR}` and
`${VAR:-default}` read the environment. A component whose `start` polls until
phēnix stops it (hoststats) needs `background: true` and an entry in the run's
`stop`, or `start` never returns and the run hangs; `render` refuses a round
that breaks this. `sync`
fails and names the files when a PR conflicts with the ones before it.

## Limitations

- burger works around three image/upstream defects; each deserves an upstream report:
  - `bennu.qc2` ships `miniccc.service` without the binary and never loads
    `virtio_console`, so its VMs never register with cc. `up` injects the
    minimega container's own `miniccc`.
  - phenix-apps `scorch/cc/cc.py` crashes (`'NoneType' object has no attribute
    'encode'`) when a command with a validator prints nothing; every health
    command with a validator prints something. Upstream fix:
    `(results["stdout"] or "").encode()`.
  - The vrouter app reports success when the minirouter CLI cannot reach its
    control socket, leaving router interfaces down. Once the VMs run, `up`
    re-sends each router's interface addresses over cc (busybox-safe `ip addr
    add`, UUIDs from minimega's `vm info`).
- `./burger bennu-check` does not work behind a TLS-intercepting proxy: bennu's
  `run.sh` builds its own unpatched Docker context. Run it on an unproxied host
  until sceptre-bennu's `docker/test/Dockerfile` can take site CAs; until then
  the #56/#57 test-suite coverage is outstanding on proxied hosts.
- SCORCH templating eats `${...}` in cc commands and validators; keep it out of
  any `cc` component, including the ones in `rounds/*.yaml`.

- Written against phēnix and minimega source; the first live run is its real test.
- `--linux-only` drops SCADA, OPC, HMI and historian, so nothing polls the RTUs
  and the Modbus and mirror checks are skipped.
- `--linux-only` in `harden` still adds the monitor VM and mirror app, but
  with no SCADA traffic its tcpdump captures nothing and nothing flags it.
- The erspan advisory run adds an ERSPAN port to the host's `phenix` OVS bridge
  until its cleanup stage runs.

## Tests

```bash
BURGER_WATERWAY=../sceptre-phenix-topologies/waterway python3 -m pytest tests
```
