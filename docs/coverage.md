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
| tcpdump | `tcpdump` on the `monitor` VM, Modbus + GRE, JSON conversion with a `.jsonl` check (#168); offline `tcpdump-alias` (the old `convertToJSON` key) | `main` (`harden`) | |
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
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; `base-soap` (with #195); | RTDS data checks off waterway/soap; `provider_start_wait` (only runs when the provider is down at configure, and `power-solver` starts it first) |
| minimega #1684 | `./burger mm-check <board>` on a running waterway or soap experiment: minimega.py generated from the PR head (pyapigen fed the running minimega's `-cli`), then from the phēnix container `cc_filtered("name=rtu-1")` must set the filter, clear it on exit, and clear it when the block raises | whether a cc command inside the block reaches only the filtered VM |
| #179 | docs only (AGENTS.md); nothing to run | |
| #132 | on hold as a draft at mgaliar's request; no board | |
| bennu #79 | `fdleak` switchboard on the arm64 rig: `modbus-churn` opens and closes 1500 Modbus TCP connections to rtu-1 and fails on a socket fd leak, a server that stops answering, or a log flood. Fails on stock bennu | a leak under a real SCADA poller; other protocols (reviewed in the PR, nothing to run) |
| bennu #81 | `robust` switchboard on the arm64 rig: `modbus-badframes` (truncated request and MBAP lengths 0, 4000, 65535 get no response, same pid, normal read answers), `iec104-masters` (reverse poll survives a second client and a failover) and `dnp3-accept` (outstation answers after an `accept()` EMFILE), the last two on a second field device on rtu-2 with bennu's example configs | GOOSE (the module is not built); the Modbus overflow itself (checked by reading the code and by the process surviving, not by a crash on stock bennu) |
| bennu #80 | `logctl` switchboard on the arm64 rig: `logctl` runs `log status/on/off/level/tail` against rtu-1's live field device as its user, and checks idempotent `on` (one fd, no duplicated lines), tail, the level revert, the rotation cap, `off` twice and socket modes 0700/0600. Fails on stock bennu | `bennu-field-device` as a second process (`--pid`/`--name` choice), a stale socket after `kill -9`, the daemon's per-instance output files (uncapped by design) |
| bennu #78 | `arm64deb` switchboard on the arm64 rig: the built deb must already say `Architecture: arm64` with a `_arm64.deb` name (no burger relabel) and the waterway items pass on the installed deb | the amd64 deb name; the PR adds no arm64 CI leg (arm64 is not built by default) |
| bennu `fix-watcher-log-flood` | `watcher` switchboard on the arm64 rig: `watcher-quiet` fails when `watcher-stderr.log` on either RTU grows over 2 KB/min across 60 s; the Field-device logs flood flag must clear. Fails on stock bennu | the watcher's other output (stdout log, firmware re-hash every 10 ms) |

Merged and rolled off every board: bennu #55, #56, #57, #58 and #66; apps #155, #157,
#158, #159, #161, #164, #165, #167, #169, #170, #171, #172, #173, #174, #175, #176, #177, #183, #190,
#191, #192, #194, #195 and #199, plus #168, #202, #207 and #208 (apps) and bennu #59, #64 and #67,
which no longer pin a PR. Their items stay on `main` (or the board named above) as regression
checks; the bennu ones build bennu `main` on `bennu`, `pybennu`, `bench`, `rtds`, `opendss` and `mtu`
(`pybennu`, `rtds`: siren's per-device level and pybennu; `opendss`: one write that turns two loads
off; `mtu`: no IP fragments on soap's provider publishes), and `art` and `caldera` keep their own
boards.

`integrate` merges the open apps PRs it needs (#166, #205)
over upstream `main` and runs `main`'s items plus the ones those PRs need. It does
not replace the per-PR boards, which say which PR a failure belongs to. PRs no
longer edit CHANGELOG.md, so burger merges them itself and the hand-built
`integration` branch is retired. The board is named `integrate` because
`ebwes-integration` is over phenix's 15-character experiment name limit.
