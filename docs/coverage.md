# Coverage

Two views: what keeps each app and SCORCH component tested on upstream `main`
whatever is open, and what tests each open PR. "Offline" means
`phenix-scorch-validate` on the rendered scenario, no VMs.

## Persistent coverage on main

The `main` board runs every item below that works on upstream `main` in one
waterway run; boards in brackets add a route or setup `main` cannot hold. An item
that still needs an open PR runs on `integrate` (and its PR's board) and joins
`main` when that PR merges.

| App or component | Items | Boards | Not covered |
|---|---|---|---|
| sceptre app | every board: bennu field devices, simulink provider, Modbus polling; `apps/mgmt-vlan-alias` (lowercase MGMT VLAN, #158) | `main`, all | |
| mirror app | `apps/mirror` into a `monitor` VM | `main` (`harden`, `base`) | |
| mgmt_tap app | `apps/mgmt_tap` against the topology's lowercase `mgmt` VLAN (#199) | `main` | |
| helics app | `apps/helics` (root broker lookup); offline `helics-no-broker` must be rejected | `main` (`base`) | |
| tap (phēnix core) | used by the soap boards | (`base-soap`, `provider`, `elastic`) | |
| cc | health on every board; `cc-empty-output`, `cc-output-parsing` | `main` (`cc-empty`) | |
| disruption | `disruption` (baseline hold); offline `disruption-dos` | `main` | |
| pcap | `pcap` per-VM filters (rtu-1, rtu-2) and JSON; offline `pcap-alias` | `main` (`harden`) | |
| tcpdump | `tcpdump` on the `monitor` VM, Modbus + GRE | `main` (`harden`) | JSON conversion waits for #168 |
| mm | bridge + per-VM captures, conversion | `main` (`harden`) | |
| pipe | `pipe`; offline `pipe-bad-name` must be rejected | `main` (`harden`) | |
| hoststats | `hoststats` | `main` | |
| vmstats | `vmstats` (explicit list); default list on `base`; offline `vmstats-bad-name` | `main` (`base`) | |
| qos | `qos` (delay on rtu-2); offline `qos-bad-rate` | `main` | |
| erspan | `erspan` (advisory) | `main` | |
| collector | `collector` with a disruption | (`base-soap`, `provider`) | |
| providerdata | `providerdata`; `elastic-empty` against an empty Elasticsearch | (`provider`, `elastic`) | |
| iperf | `iperf` 3.15, `iperf-322` | `main` | |
| ssh | `ssh` over `mgmt_tap`, root's password set by `ssh-password` | (`ssh`) | |
| rtds | `rtds` (lifecycle on soap; full on a site RTDS rig) | (`provider`, `rtds`) until #166 merges | live RTDS hardware |
| otsim, ignition, caldera, protonuke, scale, wind_turbine, wireguard apps | none | | everything (beads under burger-d90) |
| snort | `snort` on rtu-1's ICS interface with `snort-conf`'s config: must alert on Modbus and write stats (jammy's snort debs injected) | `main` | not yet run live; the guest interface name `IF0` is an assumption the first run checks |
| art, caldera, ettercap, kafka, opcexport, trafficgen components | none | | everything (ettercap: the jammy debs are blocked by this machine's AV; beads under burger-d90) |

## Open PRs

| PR | Covered by | Not covered |
|---|---|---|
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; `base-soap` (with #195); model offline in `metadata` | RTDS data checks off waterway/soap; `provider_start_wait` (only runs when the provider is down at configure, and `power-solver` starts it first) |
| #168 tcpdump | `metadata` live with `iface: any` and `convert_to_json: true` with a `.jsonl` check; the old `convertToJSON` key through the model (`tcpdump-alias`, offline); any error line (failed PID-file kill or conversion) fails | The `.jsonl` check **fails until the rename is pushed**: the pushed head ignores `convert_to_json` and logs an unknown-key warning |
| #202 snort | nothing live yet: the snort items run on `main` only, against main's component (burger-d90.23) | the interface check and stderr capture |
| minimega #1684 | `./burger mm-check <board>` on a running waterway or soap experiment: minimega.py generated from the PR head (pyapigen fed the running minimega's `-cli`), then from the phēnix container `cc_filtered("name=rtu-1")` must set the filter, clear it on exit, and clear it when the block raises | whether a cc command inside the block reaches only the filtered VM |
| #179 | docs only (AGENTS.md); nothing to run | |
| #132 | on hold as a draft at mgaliar's request; no board | |
| bennu #59 | `pybennu` switchboard (soap's provider and siren run the PR pybennu); `rtds` switchboard on your rig; `./burger bennu-check bennu`; siren's per-device level (the advisory check wants DEBUG lines in the siren log, since siren.json sets the device to debug) | siren with a LabJack; whether the device logs at DEBUG before failing without one (unverified live) |
| bennu #64 | `opendss` switchboard: one `write()` that turns two loads off, read back from OpenDSS itself. Still valid after the review change that moved the per-device update into the tag loop (a device named in two tags is pushed twice, same final state) | a bad tag late in a batch: earlier devices are already pushed |
| bennu #67 | `mtu` switchboard: a capture of soap's provider must hold UDP on the publish port and no IP fragments (`max_fragments: 0`; `publish-fragments`). Python publisher only: waterway's 15-point list (260 B) never fills a chunk. Stock bennu fails only when a publish fills a chunk | the C++ publisher on a long point list; non-default `BENNU_PUBLISH_MTU` values (burger-db0) |

Merged and rolled off every board: bennu #55, #56, #57, #58 and #66; apps #155, #157,
#158, #159, #161, #164, #165, #167, #169, #170, #171, #172, #173, #174, #175, #176, #177, #183, #190,
#191, #192, #194, #195 and #199. Their items stay on `main` (or the board named
above) as regression checks.

`integrate` merges every open apps PR with code (#166, #168, #202)
over upstream `main` and runs `main`'s items plus the ones those PRs need. It does
not replace the per-PR boards, which say which PR a failure belongs to. PRs no
longer edit CHANGELOG.md, so burger merges them itself and the hand-built
`integration` branch is retired. The board is named `integrate` because
`ebwes-integration` is over phenix's 15-character experiment name limit.
