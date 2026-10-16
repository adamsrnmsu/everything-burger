# Live coverage for #165 iperf, #166 rtds, #169 collector, #170 ssh, #177 providerdata

Status: approved 2026-10-16 (three new rounds; existing rounds untouched). Bead: burger-1c9.

## Goal

Run the five #160 components that so far only pass the offline model check,
on public topologies, so each gets a live configure/start/stop/cleanup pass.

## Where each runs

| PR | Round | Why there |
|---|---|---|
| #177 providerdata | new `provider` (soap) | soap's `power-provider` is a pybennu PyPower provider: `pybennu-power-solver` with `/etc/sceptre/config.ini`, which providerdata drives. waterway's provider is the C++ Simulink one. |
| #166 rtds | new `provider` (soap) | Lifecycle only: RSCAD and Elasticsearch off, `export_config: false` (no `rtds_config.yaml` on a PyPower provider). The RTDS data checks still need the site rig (`rtds` round). |
| #169 collector | new `provider` (soap) | Its expected shape: a baseline disruption plus providerdata/rtds in one run. |
| #165 iperf | new `iperf` (waterway) | Server rtu-1, client rtu-2 on the ICS interface. burger installs iperf3 first (`bennu.qc2` lacks it). |
| #170 ssh | new `ssh` (waterway) | Adds `mgmt_tap`, so the phēnix host can reach rtu-1 at 172.16.1.101. Root login is allowed by phēnix's default image setup. |

## Changes to burger

1. **Topology selection.** Round key `topology` (default `waterway`). `sync`
   extracts that directory from the topologies repo; `render` reads its
   topology and scenario files (waterway: `waterway-*.yaml`, soap:
   `topology.yaml`/`scenario.yaml`). Assets go to
   `<phenix dir>/topologies/burger-<topology>`, and `/phenix/topologies/<topology>`
   is rewritten in **both** documents (soap's topology injects reference it).
   `BURGER_ASSETS` is dropped in favour of that per-topology path.
2. **Round-level health.** Optional `health:` list of
   `{hostname, start: [command, ...]}` used instead of the built-in waterway
   health. soap: `pgrep -f pybennu-power-solver` on `power-provider` (no
   validator, so empty output is safe). Keeps the `--settle` gate in front of
   the provider components.
3. **cc nodes.** Round key `cc_nodes` (the VMs that get burger's miniccc), so
   soap can name `power-provider rtu-1..4 siren` without a bennu PR. Falls back
   to `bennu.nodes`, then the waterway default.
4. **Packages into VMs.** Round key `debs: {packages: [...], nodes: [...]}`.
   `build` downloads the packages for jammy (phēnix's default image release,
   which `bennu.qc2` uses) with `apt-get download` in `ubuntu:22.04`; `up`
   injects them and `/etc/phenix/startup/02-burger-debs.sh` installs them with
   one `dpkg -i`. The iperf round uses `iperf3 libiperf0 libsctp1` on rtu-1, rtu-2.

## Rounds

- `provider` (new): topology soap, all nodes kept (the rig needs
  `ignition.qc2` and `ubuntu-soaptools.qc2`), apps PRs `158 164 166 169 177`,
  stock bennu. Run: configure `[rtds, providerdata]`, start
  `[rtds, providerdata, baseline]`, stop `[providerdata, rtds, collector]`
  (rtds stop stops the provider, so providerdata exports first), cleanup
  `[rtds]`. `validate: true`.
- `iperf` (new): waterway, apps PR `165` only. Run: configure/start/stop/
  cleanup `iperf`, with a 30 s baseline disruption held after iperf starts
  (`run_duration` 20, so the clients finish inside the hold).
- `ssh` (new): waterway plus `mgmt_tap`, apps PR `170` only, `ip:
  172.16.1.101`, `user: root`, `password: ${BURGER_VM_PASSWORD}` (required:
  the round refuses to render without it), `cmds: [uptime, dpkg-query -W bennu]`.
- All three set `validate: true`: the validator skips model blocks whose component is not installed, so
  `rounds/models.yaml` checks each round's own components.
- `baseline`, `bennu`, `metadata` and `harden` do not change.

## Risks, stated in the README

- soap keeps its `mirror` app; a mirror post-start failure is silent and can
  orphan a GRE port (sceptre-phenix-apps#187, #188).
- ssh needs the phēnix container to reach the mgmt tap (host networking), and
  the password ends up in the rendered `scenario.json`.
- `preflight` checks waterway images only; soap's are listed in the README.

## Verification

`py_compile`, the test suite with soap rendered by `test_render_every_round`,
the `${` and poller guards. Nothing here runs live; the live rounds are the
real test.
