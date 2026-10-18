# Coverage

What each open PR is tested by, and what is not covered.

| PR | Covered by | Not covered |
|---|---|---|
| #155 | `harden`: cc validators, pcap, tcpdump, mm, mgmt_tap, mirror | kafka (no broker); art, ssh |
| #158 | `metadata` and `harden`; `metadata` renames the mgmt VLAN to `mgmt` | |
| #190 shared metadata base | `metadata`, under the #164-#177 models | |
| #164 disruption | `metadata` (baseline); dos model offline | dos and physical live (no Kali, no OPC script) |
| #165 iperf | `iperf` switchboard (burger installs iperf3); model offline in `metadata` | Windows client |
| #166 rtds | `provider` switchboard (lifecycle on a PyPower provider); `rtds` switchboard on your rig; model offline in `metadata` | RTDS data checks off waterway/soap |
| #177 providerdata, #169 collector | `provider` switchboard; `rtds` switchboard on your rig; models offline in `metadata` | |
| #167 pcap, #168 tcpdump, #172 mm, #173 pipe, #174 hoststats, #175 vmstats, #176 qos | `metadata` live | pcap `create_merged_pcap`/`convertToJSON` live |
| #170 ssh | `ssh` switchboard over `mgmt_tap`; model offline in `metadata` | |
| #171 erspan | `metadata` advisory run (local-only, TEST-NET peer) | a real receiver |
| #191 cc filter helper | `metadata`; `iperf` (its kill and delete cleanup) | |
| #194 empty cc output | `cc-empty`: a validator on a command that prints nothing | |
| #183 cc parsing by header line | `cc-parse`: stderr before stdout, `/` and `:` in stdout, plus the health checks | |
| #157 cc header columns | `cc-header`: the health checks through its parser | minimega older than 3.3.0 does not exercise it; conflicts with #183 |
| #192 connect_elastic, #195 ExperimentBase | | no switchboard yet |
| #181, #182 ot-sim, #184 ignition | | soap drops Ignition, so nothing polls DNP3 |
| bennu #59 | `bennu` and `pybennu` switchboards (deb and pybennu wheel); `rtds` switchboard on your rig; `./burger bennu-check bennu` | siren with a LabJack |
| #159, #161, #163, #179, #193 | CI and docs only; nothing to run | |
