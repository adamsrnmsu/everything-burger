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

Every round also gets a `health` run: a SCORCH `cc` component whose validators
check, inside the VMs, that `bennu-field-deviced`, the Simulink provider and
solver are running, that each RTU can query the provider, that the installed
bennu is the version the round built, and (with the Windows nodes) that
something is polling Modbus on each RTU. In `harden` it also checks that
the monitor VM sees mirrored Modbus traffic.

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
| #166 rtds, #177 providerdata, #169 collector | offline model only (schema check, no rig needed) | live: out of scope; needs a separate RTDS / power-solver setup with Elasticsearch |
| #167 pcap, #168 tcpdump, #172 mm, #173 pipe, #174 hoststats, #175 vmstats, #176 qos | `metadata` live | pcap `create_merged_pcap`/`convertToJSON` live |
| #170 ssh | offline model only | live: no host route to the VMs without mgmt_tap |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| bennu #55 | every round but `baseline`: field devices and the Simulink provider run the PR build | |
| bennu #56, #57 | `./burger bennu-check bennu` (waterway runs neither pybennu nor gobennu) | in an experiment |
| #159, #161, #163, #179 | CI and docs only; nothing to run | |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BURGER_PHENIX_CONTAINER` | `phenix` | container running phēnix; empty for a native install |
| `BURGER_API` | `http://localhost:3000/api/v1` | phēnix REST API |
| `BURGER_TOKEN` | unset | JWT, when phēnix auth is on |
| `BURGER_WORK` | `work/` next to `burger` | clones, worktrees, builds, results |
| `BURGER_APPS_REPO`, `BURGER_BENNU_REPO`, `BURGER_TOPOLOGIES_REPO` | `work/repos/*` | use an existing clone; burger only adds `refs/burger/*` and worktrees |

The experiment is `ebwes-<round>`. Its assets go to
`/phenix/topologies/burger-waterway` and its rendered configs and bennu build
to `/phenix/burger/<round>`, so your own `/phenix/topologies/waterway` is not
touched. The PR build of bennu is injected as a deb plus
`/etc/phenix/startup/00-burger-bennu.sh`, which installs it before phēnix's
startup scripts and `sceptre-start.sh` run.

## Adding a round or a PR

A round is `rounds/<name>.yaml` (the name must keep `ebwes-<name>` within 15
characters): `apps.prs` merged in order, optional `bennu.pr`, and optional
`scorch.components` and `scorch.runs` appended after the health run. `sync`
fails and names the files when a PR conflicts with the ones before it.

## Limitations

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
