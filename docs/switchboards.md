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
| `metadata` | #166, #168 (open; the rest merged) | stock `bennu.qc2` | disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos (+ advisory erspan); lowercase mgmt VLAN (#158, merged); offline: deprecated-key aliases and the configs that must be rejected (`vmstats-bad-name`, `qos-bad-rate`) |
| `harden` | upstream `main` (#155 merged) | stock `bennu.qc2` | cc validators, pcap, tcpdump, mm (bridge capture with a spaced filter), pipe, mgmt_tap, mirror app into a `monitor` VM; offline: `pipe-bad-name` must be rejected |
| `rtds` | #166 (#169, #177 merged) | #59 pybennu wheel | rtds, providerdata, collector and a baseline disruption on **your rig's** topology (`site` route), not waterway |
| `provider` | #166 (#169, #177 merged) | stock `bennu.qc2` | providerdata, rtds (lifecycle only) and collector with a baseline disruption, on **soap**'s pybennu PyPower provider; health: the solver runs |
| `iperf` | upstream `main` (#165 merged) | stock `bennu.qc2` | iperf 3.15 from rtu-2 to rtu-1 on waterway, after the waterway health checks: a static build injected into both, as on `iperf-322` (the jammy debs carry 3.9, which has no server `--idle-timeout` and refuses client `--pidfile`) |
| `iperf-322` | upstream `main` (#165 merged) | stock `bennu.qc2` | the `iperf` board on iperf3 **3.22**, the high end of #165's range: burger builds a static binary from esnet/iperf tag 3.22 once, in a site-layered `ubuntu:22.04` container (so the proxy and CA apply), caches it as `work/cache/iperf3-3.22`, and injects it into both RTUs as `/usr/local/bin/iperf3-3.22`; `iperf_paths.linux` and `iperf_version: "3.22"` point at it (`requires.iperf_static`). The first `burger build` needs network; later ones reuse the cache (delete the file to rebuild) |
| `bench` | upstream `main` | #59 deb and pybennu wheel (carries #55) | soap: `field-device-bench` times a throwaway 800-tag field device on rtu-1 and fails above 2 s per write cycle; the report row shows the measured time |
| `opendss` | bennu #64 | #64 pybennu wheel | soap: `opendss-batched-write` runs the OpenDSS solver in-process on the provider VM and sends one write for two loads; fails if either is still enabled in OpenDSS. Fails on stock main (only the last device is applied); WARN when `opendssdirect` is missing |
| `ssh` | upstream `main` (#170 merged) | stock `bennu.qc2` | ssh to rtu-1 over `mgmt_tap`, after the waterway health checks; needs `BURGER_VM_PASSWORD`, any lab-only value: `ssh-password` sets root's password to it at configure |
| `fifo` | upstream `main` | bennu `main` deb (#66 merged) | one 1000-tag batched WRITE from rtu-1 to waterway's simulink provider must ACK, and `provider.log` must show no FIFO error. Fails on a bennu older than #66 |
| `cc-empty` | upstream `main` (#194 merged) | stock `bennu.qc2` | a cc validator on a command that prints nothing (`cc-empty-output`) and #183's stderr/`/`/`:` case (`cc-output-parsing`, merged: a regression check on main), after the waterway health checks; `cc-empty-output` crashes on main (#186) |
| `elastic` | upstream `main` (#177, #192 merged) | stock `bennu.qc2` | soap: `es-stub` serves an empty Elasticsearch reply from the provider VM over `mgmt_tap`; `elastic-empty` (providerdata) must fail with #192's "returned no cluster info" (`expect_error`) |
| `base` | upstream `main` (#195 merged) | stock `bennu.qc2` | ExperimentBase on waterway: cc health, vmstats' default VM list, pcap, tcpdump, mm, hoststats, disruption, `mgmt_tap`, `mirror`, and the `helics` app (root broker `rtu-1\|IF0`; checks its injected start script; `helics-no-broker` must be rejected, offline) |
| `mtu` | upstream `main` | #67 deb and pybennu wheel | soap: `publish-fragments` captures the PyPower provider and fails on any IP fragment; `disruption` only holds the run open. Meant to fail on stock bennu when a publish fills a chunk |
| `base-soap` | #166 (#169, #177, #195 merged) | stock `bennu.qc2` | ExperimentBase on soap: rtds, providerdata, collector (annotations, node lookups), disruption and vmstats' default VM list |
| `integrate` | #166, #168, #202: every open apps PR with code, merged by burger | stock `bennu.qc2` | `main`'s items plus the ones that still need an open PR (tcpdump-alias and #168's JSON conversion), in one run on waterway. If it passes, the open PRs can merge in any order; drop each from `prs` as it merges |
| `main` | upstream `main`, no PRs | stock `bennu.qc2` | the persistent check: every item burger covers that runs on main (disruption, pcap, tcpdump, mm, pipe, hoststats, vmstats, qos, erspan, iperf, snort, cc, mgmt_tap, mirror, helics, and the offline rejections) in one run on waterway. Run it after merges to see the state of main; items join it when their PR merges |

Models no route can run are validated offline by `phenix-scorch-validate`
(items with `routes: []` are validate-only).

Every switchboard file in full: [reference](reference/switchboards.md).
