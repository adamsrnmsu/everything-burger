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
| pcap | `pcap` (rtu-1, interface 0) | `main` (`harden`) | per-VM filters and JSON wait for #167 |
| tcpdump | `tcpdump` on the `monitor` VM, Modbus + GRE | `main` (`harden`) | JSON conversion waits for #168 |
| mm | bridge + per-VM captures, conversion | `main` (`harden`) | |
| pipe | `pipe`; offline `pipe-bad-name` must be rejected | `main` (`harden`) | |
| hoststats | `hoststats` | `main` | |
| vmstats | `vmstats` (explicit list); default list on `base`; offline `vmstats-bad-name` | `main` (`base`) | |
| qos | `qos` (delay on rtu-2); offline `qos-bad-rate` | `main` | |
| erspan | `erspan` (advisory) | `main` | |
| collector | `collector` with a disruption | (`base-soap`, `provider`) | |
| providerdata | `providerdata`; `elastic-empty` against an empty Elasticsearch | (`provider`, `elastic`) | |
| iperf | `iperf` 3.15, `iperf-322` | `integrate` until #165 merges | |
| ssh | `ssh` over `mgmt_tap` | `ssh` until #170 merges | |
| rtds | `rtds` (lifecycle on soap; full on a site RTDS rig) | (`provider`, `rtds`) until #166 merges | live RTDS hardware |
| otsim, ignition, caldera, protonuke, scale, wind_turbine, wireguard apps | none | | everything (beads under burger-d90) |
| art, caldera, ettercap, kafka, opcexport, snort, trafficgen components | none | | everything (snort and ettercap next) |

## Open PRs

| PR | Covered by | Not covered |
|---|---|---|
| #165 iperf | `iperf` switchboard (with #191; a static iperf3 3.15, as jammy's 3.9 cannot run the component; the client runs with `add_client_args: "--omit 2"`); `iperf-322`: the same on a static iperf3 3.22 (the range #165 claims is 3.15-3.22); both check the client JSON's `start.version` names the version they inject; model offline in `metadata` | Windows client; the PID files, kill-by-PID and `iperf_version` matching of the local head (3904686 is not pushed); rejected `add_*_args` (a rejection cannot be a pass) |
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; `base-soap` (with #195); model offline in `metadata` | RTDS data checks off waterway/soap; `provider_start_wait` (only runs when the provider is down at configure, and `power-solver` starts it first) |
| #167 pcap | `metadata` live: per-VM `filter` (rtu-1 filtered, rtu-2 not) and the filter reset, seen in mm's bridge capture; `convert_to_json: true` with a `.jsonl` check; the old `convertToJSON` key through the model (`pcap-alias`, offline) | `create_merged_pcap` live; the alias is not run live (one pcap component per board) |
| #168 tcpdump | `metadata` live with `iface: any` and `convert_to_json: true` with a `.jsonl` check; the old `convertToJSON` key through the model (`tcpdump-alias`, offline); any error line (failed PID-file kill or conversion) fails | The `.jsonl` check **fails until the rename is pushed**: the pushed head ignores `convert_to_json` and logs an unknown-key warning |
| #170 ssh | `ssh` switchboard over `mgmt_tap`; model offline in `metadata` | |
| #179 | docs only (AGENTS.md); nothing to run | |
| #132 | on hold as a draft at mgaliar's request; no board | |
| bennu #59 | `pybennu` switchboard (soap's provider and siren run the PR pybennu); `rtds` switchboard on your rig; `./burger bennu-check bennu`; siren's per-device level (the advisory check wants DEBUG lines in the siren log, since siren.json sets the device to debug) | siren with a LabJack; whether the device logs at DEBUG before failing without one (unverified live) |
| bennu #64 | `opendss` switchboard: one `write()` that turns two loads off, read back from OpenDSS itself. Still valid after the review change that moved the per-device update into the tag loop (a device named in two tags is pushed twice, same final state) | a bad tag late in a batch: earlier devices are already pushed |
| bennu #67 | `mtu` switchboard: a capture of soap's provider must hold UDP on the publish port and no IP fragments (`max_fragments: 0`; `publish-fragments`). Python publisher only: waterway's 15-point list (260 B) never fills a chunk. Stock bennu fails only when a publish fills a chunk | the C++ publisher on a long point list; non-default `BENNU_PUBLISH_MTU` values (burger-db0) |

Merged and rolled off every board: bennu #55, #56, #57, #58 and #66; apps #155, #157,
#158, #159, #161, #164, #169, #171, #172, #173, #174, #175, #176, #177, #183, #190,
#191, #192, #194, #195 and #199. Their items stay on `main` (or the board named
above) as regression checks.

`integrate` merges every open apps PR with code (#165, #166, #167, #168, #170)
over upstream `main` and runs `main`'s items plus the ones those PRs need. It does
not replace the per-PR boards, which say which PR a failure belongs to. PRs no
longer edit CHANGELOG.md, so burger merges them itself and the hand-built
`integration` branch is retired. The board is named `integrate` because
`ebwes-integration` is over phenix's 15-character experiment name limit.
