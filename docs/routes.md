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

Topology asset paths `/phenix/topologies/<dir>` in the waterway and soap
documents are rewritten to `<phenix dir>/topologies/burger-<dir>`, so your own
copy is not touched.
