# Routes

`routes/<name>.yaml` says which topology a switchboard runs on and what items
can ask of it.

| Route | Topology | Notes |
|---|---|---|
| `waterway` | topologies repo, `waterway/` | the default rig; RTUs `rtu-1`.. |
| `soap` | topologies repo, `soap/` | runs without `ignition`, `netmonitor` and `attacker`, so only `bennu.qc2` is needed |
| `site` | `BURGER_SITE_TOPOLOGY` and `BURGER_SITE_SCENARIO` | your own rig; never rewritten or copied |

Topology asset paths `/phenix/topologies/<dir>` in the waterway and soap
documents are rewritten to `<phenix dir>/topologies/burger-<dir>`, so your own
copy is not touched.
