# What verify checks

1. phēnix has the switchboard's merged apps commit installed (`install` and `up`
   refuse a build that does not match the last `sync`).
2. The experiment and every VM are running (REST API).
3. `phenix-scorch-validate` accepts the switchboard's scenario and every item
   config, including the validate-only ones. Skipped when the installed code
   has no validator (switchboards without #160).
4. Every item's `verify` checks pass against its output; a component listed in
   a stage that left no status file crashed.

The report names the exact SHAs of `main`, each PR, bennu and the topologies
repo, from `work/<switchboard>/manifest.json`.
