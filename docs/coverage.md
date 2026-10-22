# Coverage

What each open `sceptre-phenix-apps` PR (and the bennu PRs the boards carry) is
tested by, and what is not covered. "Offline" means `phenix-scorch-validate` on
the rendered scenario, no VMs.

| PR | Covered by | Not covered |
|---|---|---|
| #132 | none; to be closed | |
| #155 | `harden`: cc validators, pcap, tcpdump, mm, mgmt_tap, mirror; mm's bridge capture filter `tcp port 502` (one quoted minimega argument: the start stage fails without the quoting, and the capture must hold Modbus) | kafka (no broker); art, ssh; the `Token`/`Args`/`FilterExpr` allowlists, `extract_run_name` and the filter/VM-name rejections (unit tests only: they stop the stage, not the experiment, so no board can pass on them) |
| #158 | `metadata` and `harden`; `metadata` renames the mgmt VLAN to `mgmt` | |
| #159, #161, #163, #179 | CI and docs only; nothing to run | |
| #164 disruption | `metadata` (baseline); dos model offline | dos and physical live (no Kali, no OPC script) |
| #165 iperf | `iperf` switchboard (with #191; burger installs iperf3; the client runs with `add_client_args: "--omit 2"`); model offline in `metadata` | Windows client; the PID files, kill-by-PID and `iperf_version` matching of the local head (3904686 is not pushed); rejected `add_*_args` (a rejection cannot be a pass) |
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; `base-soap` (with #195); model offline in `metadata` | RTDS data checks off waterway/soap; `provider_start_wait` (only runs when the provider is down at configure, and `power-solver` starts it first) |
| #167 pcap | `metadata` live: per-VM `filter` (rtu-1 filtered, rtu-2 not) and the filter reset, seen in mm's bridge capture; `convert_to_json: true` with a `.jsonl` check; the old `convertToJSON` key through the model (`pcap-alias`, offline) | `create_merged_pcap` live. The `.jsonl` check **fails until the renamed key is pushed**: upstream head 9495e16 still reads `convertToJSON` (local 5900184 is not pushed); the alias is not run live (one pcap component per board) |
| #168 tcpdump | `metadata` live with `iface: any` and `convert_to_json: true` with a `.jsonl` check; the old `convertToJSON` key through the model (`tcpdump-alias`, offline); any error line (failed PID-file kill or conversion) fails | The `.jsonl` check **fails until the rename is pushed**: the pushed head ignores `convert_to_json` and logs an unknown-key warning |
| #169 collector | `provider` switchboard; `rtds` switchboard on your rig; `base-soap` (with #195); model offline in `metadata` | the ordering rule (iperf listed after collector raises in `Collector.__init__`, not in the model, so neither `phenix-scorch-validate` nor `expect_invalid` can see it; unit tests only) |
| #170 ssh | `ssh` switchboard over `mgmt_tap`; model offline in `metadata` | |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| #172 mm | `metadata` live: bridge capture with `convert`, a filtered VM capture stopped by interface and converted | mm's own filter reset (no capture starts after its VM capture); `convert_timeout` expiry |
| #173 pipe | `metadata` live: the component writes into a pipe at start and deletes it at cleanup | a reader receiving backslashes intact; `via` programs; cleanup refusing with an open writer (nothing reads or writes the pipe) |
| #174 hoststats | `metadata` live; `base` (with #195, under the shared `mm`) | `vm_list` holding only this experiment's VMs (needs two experiments on one host) |
| #175 vmstats | `metadata` live; `base` and `base-soap` run vmstats with no `vms` (with #195); `vmstats-bad-name` (`expect_invalid`, offline): a VM name with `#` must be rejected | the default VM list now includes `os_type` vyos and vyatta and vmstats' no-`-t` fallback: **no route has a VyOS router**, so neither is run (a live check needs a booted VyOS image with miniccc, then a default-list run: see the VyOS steps in the apps bead); `\` in a VM name (the same pattern; only `#` is tested) and a down VM during stop |
| #176 qos | `metadata` live with the bare-number `delay: 20` (20 ms; **needs the pushed head**: the old pattern wants a unit, so the offline validator also flags it until then); `qos-bad-rate` (`expect_invalid`, offline): `1.0005 mbit` must be rejected | fractional rates (`1.5 mbit` becomes `1500 kbit`) and `.5s` delays live: not in the pushed head (af5a219) |
| #177 providerdata | `provider` switchboard; `rtds` switchboard on your rig; `elastic` (with #192); `base-soap` (with #195); model offline in `metadata` | |
| #191 cc filter helper | `metadata` and `iperf`: iperf and disruption cleanup go through it | |
| #192 connect_elastic | `elastic` switchboard: a fake Elasticsearch on the provider VM answers `{}`, the one reply that reaches the changed line; the stage must fail with "returned no cluster info" (on main it exits with no status file) | an unreachable server: the client raises before the changed line with or without #192, so it is not a test. Never run live: soap has not run `mgmt_tap` before |
| #194 empty cc output | `cc-empty`: a validator on a command that prints nothing. Still needed: upstream main's `mm_exec_wait` sets stdout and stderr to `None` | |
| #195 ExperimentBase | `base` (waterway): cc health on every node's `os_type`, vmstats' default VM list, pcap/tcpdump/mm/hoststats/disruption on the shared `mm`, `mgmt_tap`, `mirror`, and `helics` resolving its root broker; `base-soap`: collector's annotations and lookups, rtds, providerdata. The sceptre app runs in every board | iperf and the disruption models (conflict with #165, #164); ot-sim, wind turbine, caldera and ignition apps; null networks stops the experiment before SCORCH, so only #195's unit tests cover it. The missing helics broker `missinghost\|eth0` is `helics-no-broker` (`expect_invalid` on `base`: the app's pre-start stage run with `--dry-run` on the rendered topology) |
| bennu #55 | every switchboard but `baseline`: field devices and the Simulink provider run the PR build | |
| bennu #56, #59 | `pybennu` switchboard (soap's provider and siren run the PR pybennu); `rtds` switchboard on your rig; `./burger bennu-check bennu`; siren's per-device level (the advisory check wants DEBUG lines in the siren log, since siren.json sets the device to debug) | siren with a LabJack; whether the device logs at DEBUG before failing without one (unverified live) |
| bennu #64 | `opendss` switchboard: one `write()` that turns two loads off, read back from OpenDSS itself. Still valid after the review change that moved the per-device update into the tag loop (a device named in two tags is pushed twice, same final state) | a bad tag late in a batch: earlier devices are already pushed |
| bennu #67 | `mtu` switchboard: a capture of soap's provider must hold UDP on the publish port and no IP fragments (`max_fragments: 0`; `publish-fragments`). Python publisher only: waterway's 15-point list (260 B) never fills a chunk. Stock bennu fails only when a publish fills a chunk | the C++ publisher on a long point list; non-default `BENNU_PUBLISH_MTU` values (burger-db0) |
| bennu #57 | `./burger bennu-check bennu` (no switchboard runs gobennu) | in an experiment |

Merged upstream and dropped from every board: #190 (2026-10-20), #183 and #157
(2026-10-21, the cc parsing PRs; `cc-output-parsing` stays on `cc-empty` as a
regression check on main).

The tests #191, #192, #194 and #195 add now live in their own files
(`common/tests/test_utils_cc_filter.py`, `test_utils_elastic.py`,
`test_utils_cc_output.py`, `test_utils_host_port.py`), and #195's collector
stub no longer needs #169. In a scratch merge over upstream `main`, #191 #192
#194 #195 #169 merged cleanly and the suite passed. The boards still sync
against the **old** heads, where all four append to `test_utils.py` and
conflict, so they keep one board each until the branches are pushed; after that
one board carrying #191 #192 #194 #195 together (plus #169 and #177 for soap)
will sync. #195 still conflicts with #164, #165 and #175.
