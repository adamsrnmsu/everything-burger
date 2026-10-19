# Switchboards

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
| `bench` | upstream `main` | #59 deb and pybennu wheel (carries #55) | soap: `field-device-bench` times a throwaway 800-tag field device on rtu-1 and fails above 2 s per write cycle; the report row shows the measured time |
| `opendss` | bennu #64 | #64 pybennu wheel | soap: `opendss-batched-write` runs the OpenDSS solver in-process on the provider VM and sends one write for two loads; fails if either is still enabled in OpenDSS. Fails on stock main (only the last device is applied); WARN when `opendssdirect` is missing |
| `ssh` | #170 | stock `bennu.qc2` | ssh to rtu-1 over `mgmt_tap`, after the waterway health checks; needs `BURGER_VM_PASSWORD` |
| `fifo` | upstream `main` | #66 deb | one 1000-tag batched WRITE from rtu-1 to waterway's simulink provider must ACK, and `provider.log` must show no FIFO error. **Fails on stock bennu** by design (drop the `bennu:` block to see it) |

Models no route can run are validated offline by `phenix-scorch-validate`
(items with `routes: []` are validate-only).

Every switchboard file in full: [reference](reference/switchboards.md).
