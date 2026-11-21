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
| helics app | `apps/helics` (root broker lookup); `helics-otsim`: from ot-sim's `helics/federate` annotation (rtu-1's federate on a broker at rtu-2, so `10.1.1.102:24000`) helics writes a sub-broker on rtu-2 under the root, the root's script counts it (`--subbrokers 1 -f1`), and the federate gets the wait-for-broker script (otsim runs first); offline `helics-no-broker` must be rejected | `main` (`base`, `helics` on the arm64 rig) | annotation brokers without a port or as `hostname\|iface` (#223 unit tests; ot-sim always writes `ip:24000` and items cannot annotate nodes); a federate on the root directly; a running `helics_broker` (not in `bennu.qc2`) |
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
| otsim | `otsim`: the configs it injects for an fd-server (rtu-1, Modbus bus + breaker, HELICS I/O to a sub-broker on rtu-2, `10.1.1.102:24000`), a fep (rtu-2) and an fd-client (provider); `otsim-fep-client` (advisory) pins the fd-client behind a fep getting a Modbus client to the fep's DNP3-only port | `main` (`helics` on the arm64 rig) | ot-sim itself (not in `bennu.qc2`), DNP3 fd-servers, node-red, logic, API TLS, ot-sim's own broker scripts |
| ignition, caldera, protonuke, scale, wind_turbine, wireguard apps | none | | everything (beads under burger-d90) |
| snort | `snort` on rtu-1's ICS interface with `snort-conf`'s config: must alert on Modbus and write stats (jammy's snort debs injected) | `main` | not yet run live; the guest interface name `IF0` is an assumption the first run checks |
| art, caldera, ettercap, kafka, opcexport, trafficgen components | none | | everything (ettercap: the jammy debs are blocked by this machine's AV; beads under burger-d90) |

## Open PRs

| PR | Covered by | Not covered |
|---|---|---|
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; `base-soap` (with #195); | RTDS data checks off waterway/soap; `provider_start_wait` (only runs when the provider is down at configure, and `power-solver` starts it first) |
| minimega #1684, #1686 | the `mm-bindings` probe (`./burger probe <board>`, or named in a board's `probes:` so `verify` runs it) on a running waterway or soap experiment, from the phēnix container against the minimega.py it imports: `with minimega.connect(namespace=exp) as mm` lists the VM and closes the client, `close()` twice, `copy.copy`, `pickle.dumps` raises, and `cc_filtered("name=rtu-1")` sets the filter, clears it on exit and clears it when the block raises. On a board with its own minimega, the `minimega.py in phēnix` verify row proves it is that build's | whether a cc command inside the block reaches only the filtered VM; the generated bindings are no longer built here (the phēnix image build is that test) |
| minimega #1699, #1800, #1707 | the `mm-capture` probe, through the minimega container in the experiment's namespace, files read back from its `MM_FILEPATH` (`BURGER_MMCAP_SECONDS`, default 10, per capture): a VM capture is classic pcap with frames; a `bridge ... vlans=<first VLAN>` capture holds only frames tagged with it; `capture pcap namespace ... format=pcapng` writes pcapng with frames; `capture pcap delete id` stops one capture and lists the other; `snaplen=70000` (namespace), `vms=` and `format=` on a VM capture are refused; after the deletes `bridge mirrors` lists none for the namespace. Captures and `burger-mmcap-*` files are removed in a `finally` | traffic: the probe makes none, so a quiet experiment fails the frame counts; multi-host namespace merge (one host here) |
| phēnix PR 576 | the `workflow-vars` probe (skipped unless the board has `phenix:`): on a temp directory it writes in the phēnix container and removes after, `phenix workflow render` fills `-e NAME=value`; reads `--env-file`, where `-e` beats the file and the file beats the shell; reads `<dir>/.env` (per the docs, as `workflow apply` does); gives `${NAME:default}` its default; keeps `$${NAME}` as the text `${NAME}`; exits non-zero naming a variable with no value and no default; and `config create -e` stores the substituted value (`config get`), then the config is deleted | `workflow apply` and the server-side variables (included topology files, REST `variables`); `config apply`/`diff`; the unused `-e` error and line-break errors |
| #222 otsim fep clients | `integrate`: `otsim-fep-client` is required there (advisory on `main` until #222 merges): the provider, an fd-client behind rtu-2's fep, must get a DNP3 client to the fep's outstation | a fep with `downstream: modbus` and a fep behind a fep (#222's unit tests only) |
| #223 helics sub-broker key | `integrate` (merged with the other open PRs; the ot-sim sub-broker path it touches stays green) | its fix itself: the KeyError forms need node annotations no item can add (#223's unit tests) |
| #179 | docs only (AGENTS.md); nothing to run | |
| #132 | on hold as a draft at mgaliar's request; no board | |
| bennu #79 | `fdleak` switchboard on the arm64 rig: `modbus-churn` opens and closes 1500 Modbus TCP connections to rtu-1 and fails on a socket fd leak, a server that stops answering, or a log flood. Fails on stock bennu | a leak under a real SCADA poller; other protocols (reviewed in the PR, nothing to run) |
| bennu #81 | `robust` switchboard on the arm64 rig: `modbus-badframes` (truncated request and MBAP lengths 0, 4000, 65535 get no response, same pid, normal read answers), `iec104-masters` (reverse poll survives a second client and a failover) and `dnp3-accept` (outstation answers after an `accept()` EMFILE), the last two on a second field device on rtu-2 with bennu's example configs | GOOSE (the module is not built); the Modbus overflow itself (checked by reading the code and by the process surviving, not by a crash on stock bennu) |
| bennu #80 | `logctl` switchboard on the arm64 rig: `logctl` runs `log status/on/off/level/tail` against rtu-1's live field device as its user, and checks idempotent `on` (one fd, no duplicated lines), tail, the level revert, the rotation cap, `off` twice and socket modes 0700/0600. Fails on stock bennu | `bennu-field-device` as a second process (`--pid`/`--name` choice), a stale socket after `kill -9`, the daemon's per-instance output files (uncapped by design) |
| bennu #78 | `arm64deb` switchboard on the arm64 rig: the built deb must already say `Architecture: arm64` with a `_arm64.deb` name (no burger relabel) and the waterway items pass on the installed deb | the amd64 deb name; the PR adds no arm64 CI leg (arm64 is not built by default) |
| bennu `#82` | `watcher` switchboard on the arm64 rig: `watcher-quiet` fails when `watcher-stderr.log` on either RTU grows over 2 KB/min across 60 s; the Field-device logs flood flag must clear. Fails on stock bennu | the watcher's other output (stdout log, firmware re-hash every 10 ms) |
| bennu #84, #85, #86 | `gt`, `gt-es8` and `gt-stock` switchboards on the arm64 rig: `groundtruth` runs `groundtruth_monitor` on the provider VM against a real Elasticsearch 8.19: client construction, console script, UTC index and `@timestamp`, the alert rate limit, a bounded queue and a bulk retry across an outage | the OpenSearch client path, ES 7, a monitor on its own host, the `type` key in bulk actions (harmless), the per-message filter re-parse |

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
