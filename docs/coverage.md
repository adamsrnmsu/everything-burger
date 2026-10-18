# Coverage

What each open PR is tested by, and what is not covered.

| PR | Covered by | Not covered |
|---|---|---|
| #155 | `harden`: cc validators, pcap, tcpdump, mm, mgmt_tap, mirror | kafka (no broker); art, ssh |
| #158 | `metadata` and `harden`; `metadata` renames the mgmt VLAN to `mgmt` | |
| #164 disruption | `metadata` (baseline); dos model offline | dos and physical live (no Kali, no OPC script) |
| #190 shared base | `metadata` (merged under #164-#177); `phenix-scorch-validate` on every board | run/loop `replace` keys (no board uses them) |
| #165 iperf | `iperf` switchboard (with #191) (burger installs iperf3); model offline in `metadata` | Windows client |
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; model offline in `metadata` | RTDS data checks off waterway/soap |
| #177 providerdata, #169 collector | `provider` switchboard; `rtds` switchboard on your rig; models offline in `metadata` | |
| #167 pcap | `metadata` live: per-VM `filter` (rtu-1 filtered, rtu-2 not) and the filter reset, seen in mm's bridge capture | `create_merged_pcap`/`convertToJSON` live |
| #168 tcpdump | `metadata` live with `iface: any` and `convertToJSON`; any error line (failed PID-file kill or conversion) fails | |
| #172 mm | `metadata` live: bridge capture with `convert`, a filtered VM capture stopped by interface and converted | mm's own filter reset (no capture starts after its VM capture); `convert_timeout` expiry |
| #173 pipe, #174 hoststats, #175 vmstats, #176 qos | `metadata` live | vmstats' no-`-t` fallback (no VyOS router on any route) |
| #191 cc filter helper | `metadata` and `iperf`: iperf and disruption cleanup go through it | |
| #192 connect_elastic, #193 docstring | unit tests only | Elasticsearch failure live |
| #170 ssh | `ssh` switchboard over `mgmt_tap`; model offline in `metadata` | |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| bennu #55 | every switchboard but `baseline`: field devices and the Simulink provider run the PR build | |
| bennu #56, #59 | `pybennu` switchboard (soap's provider and siren run the PR pybennu); `rtds` switchboard on your rig; `./burger bennu-check bennu` | siren with a LabJack |
| bennu #57 | `./burger bennu-check bennu` (no switchboard runs gobennu) | in an experiment |
| #159, #161, #163, #179 | CI and docs only; nothing to run | |
