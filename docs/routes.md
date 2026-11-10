# Routes

`routes/<name>.yaml` says which topology a switchboard runs on and what items
can ask of it.

| Route | Topology | Notes |
|---|---|---|
| `waterway` | topologies repo, `waterway/` | the default rig; RTUs `rtu-1`.. |
| `soap` | topologies repo, `soap/` | runs without `ignition`, `netmonitor` and `attacker`, so only `bennu.qc2` is needed |
| `mitm` | topologies repo, `mitm/` | lean waterway derivative with its own `kali`; the `ettercap` board runs here. Needs the topologies PR that adds `mitm/` merged to main (sync archives main) |
| `site` | `BURGER_SITE_TOPOLOGY` and `BURGER_SITE_SCENARIO` | your own rig; never rewritten or copied |
| `waterway-arm64` | `BURGER_ARM64_RANGE` (default `/Users/ryanadams/git/phenix-waterway-range`), `topologies/waterway-arm64/` | Apple Silicon rig; never rewritten or copied; images are the route's `images` list, so use `burger preflight --route waterway-arm64 --linux-only`; board `arm64` |
| `mitm-arm64` | `BURGER_ARM64_RANGE`, `topologies/mitm-arm64/` | the `mitm` route on the arm64 range (kali-arm64 attacker; the Malcolm `blue` node is dropped); run a board on it with `burger check ettercap --route mitm-arm64` |

## arm64 routes

A route with an `image_map` is an arm64 range. Nodes burger injects (attacker,
monitor, trafficgen, caldera) get the mapped image (`kali.qc2` to
`kali-arm64.qc2`, and so on) and the hardware block the range's topologies use
(`cpu: host`, `machine: virt`, AAVMF firmware, xhci USB, `inject_partition: 2`).
Every board command takes `--route <name>` to run the board on a route other
than its own (the item must list the route in `routes:`); pass it on each step
you run by hand. `goart` comes from `assets/art/goart-arm64` on these routes
(`scripts/build-goart.sh arm64` builds it). A bennu build on an arm64 route is compiled
natively in a `linux/arm64` container (needs an arm64 docker host, such as the Lima VM). A deb
whose `Architecture` is not already `arm64` is relabelled (cpack hard-codes `amd64` until bennu #78
merges; the manifest records `relabel_needed`) and every arm64 deb is repacked without its
`Depends` line, which lists -dev packages the RTU images lack; the `fdleak` board runs one.

Topology asset paths `/phenix/topologies/<dir>` in the waterway and soap
documents are rewritten to `<phenix dir>/topologies/burger-<dir>`, so your own
copy is not touched.
