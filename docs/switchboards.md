# Switchboards

`switchboards/<name>.yaml` (the name must keep `ebwes-<name>` within 15
characters):

| Key | Meaning |
|---|---|
| `about` | one line for `burger list` |
| `route` | `waterway`, `soap` or `site`: the topology the items run on |
| `apps.prs` | sceptre-phenix-apps PRs merged in order; `sync` fails and names the files when one conflicts with those before it. Integers only |
| `apps.branch` | instead of `prs`: use this branch as the apps tree, with no merges. `remote: fork` (default; the `gh` user's fork, from `gh api user`) or `upstream`. `contains: [159, 161]` lists the PRs the branch holds, in merge order; items' `requires.apps_prs` are checked against it, and `burger prs` shows the board as carrying them. `sync` fetches the branch into `refs/burger/branch/<remote>/<branch>` and records its sha in the manifest. `burger prs` marks the board stale when the branch has a new head since sync; it cannot tell whether the branch still holds its PRs' current heads, so rebuild it when a PR moves |
| `bennu` | `pr`, or `branch` (+ `remote: upstream` by default, or `fork`) to build bennu from a branch such as `main` without a PR; one of the two, optionally `install: [deb, pybennu]` and `pip` (dependencies the PR adds that `bennu.qc2` lacks: `build` turns them into wheels with `pip wheel --no-deps` and the VMs install them with pybennu, since they have no package index) |
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
| `metadata` | #165–#172, #174–#177, #191 | stock `bennu.qc2` | disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos (+ advisory erspan); lowercase mgmt VLAN (#158, merged); offline: deprecated-key aliases and the configs that must be rejected (`vmstats-bad-name`, `qos-bad-rate`) |
| `harden` | #155 | stock `bennu.qc2` | cc validators, pcap, tcpdump, mm (bridge capture with a spaced filter), pipe, mgmt_tap, mirror app into a `monitor` VM; offline: `pipe-bad-name` must be rejected |
| `rtds` | #166, #169, #177 | #59 pybennu wheel | rtds, providerdata, collector and a baseline disruption on **your rig's** topology (`site` route), not waterway |
| `provider` | #166, #169, #177 | stock `bennu.qc2` | providerdata, rtds (lifecycle only) and collector with a baseline disruption, on **soap**'s pybennu PyPower provider; health: the solver runs |
| `iperf` | #165, #191 | stock `bennu.qc2` | iperf from rtu-2 to rtu-1 on waterway, after the waterway health checks; burger installs iperf3 into both |
| `iperf-322` | #165, #191 | stock `bennu.qc2` | the `iperf` board on iperf3 **3.22** (the jammy debs carry 3.9): burger builds a static binary from esnet/iperf tag 3.22 once, in a site-layered `ubuntu:22.04` container (so the proxy and CA apply), caches it as `work/cache/iperf3-3.22`, and injects it into both RTUs as `/usr/local/bin/iperf3-3.22`; `iperf_paths.linux` and `iperf_version: "3.22"` point at it (`requires.iperf_static`). The first `burger build` needs network; later ones reuse the cache (delete the file to rebuild) |
| `bench` | upstream `main` | #59 deb and pybennu wheel (carries #55) | soap: `field-device-bench` times a throwaway 800-tag field device on rtu-1 and fails above 2 s per write cycle; the report row shows the measured time |
| `opendss` | bennu #64 | #64 pybennu wheel | soap: `opendss-batched-write` runs the OpenDSS solver in-process on the provider VM and sends one write for two loads; fails if either is still enabled in OpenDSS. Fails on stock main (only the last device is applied); WARN when `opendssdirect` is missing |
| `ssh` | #170 | stock `bennu.qc2` | ssh to rtu-1 over `mgmt_tap`, after the waterway health checks; needs `BURGER_VM_PASSWORD` |
| `fifo` | upstream `main` | #66 deb | one 1000-tag batched WRITE from rtu-1 to waterway's simulink provider must ACK, and `provider.log` must show no FIFO error. **Fails on stock bennu** by design (drop the `bennu:` block to see it) |
| `cc-empty` | #194 | stock `bennu.qc2` | a cc validator on a command that prints nothing (`cc-empty-output`) and #183's stderr/`/`/`:` case (`cc-output-parsing`, merged: a regression check on main), after the waterway health checks; `cc-empty-output` crashes on main (#186) |
| `elastic` | #177 (+ #192 on main) | stock `bennu.qc2` | soap: `es-stub` serves an empty Elasticsearch reply from the provider VM over `mgmt_tap`; `elastic-empty` (providerdata) must fail with #192's "returned no cluster info" (`expect_error`) |
| `base` | #195 | stock `bennu.qc2` | ExperimentBase on waterway: cc health, vmstats' default VM list, pcap, tcpdump, mm, hoststats, disruption, `mgmt_tap`, `mirror`, and the `helics` app (root broker `rtu-1\|IF0`; checks its injected start script; `helics-no-broker` must be rejected, offline) |
| `mtu` | upstream `main` | #67 deb and pybennu wheel | soap: `publish-fragments` captures the PyPower provider and fails on any IP fragment; `disruption` only holds the run open. Meant to fail on stock bennu when a publish fills a chunk |
| `base-soap` | #166, #169, #177, #195 | stock `bennu.qc2` | ExperimentBase on soap: rtds, providerdata, collector (annotations, node lookups), disruption and vmstats' default VM list |
| `integrate` | `integration` branch: the twenty PRs of order A (#159 … #195, see the board header) | stock `bennu.qc2` | the items of `metadata`, `harden`, `iperf`, `base` and `cc-empty` in one run on waterway, against the branch that holds every open PR merged in order. Pre-merge check for order A; rebuild and re-sync the branch when a PR head moves or `main` advances ([notes](integration-notes.md)) |

Models no route can run are validated offline by `phenix-scorch-validate`
(items with `routes: []` are validate-only).

Every switchboard file in full: [reference](reference/switchboards.md).
