# Where files go

| What | Where |
|---|---|
| Clones | `work/repos/{apps,bennu,topologies}` (only `refs/burger/*` and worktrees are added) |
| Merged trees, builds, results | `work/<switchboard>/{apps,bennu,<topology>,out,results}`, plus `manifest.json`, `state.json`, `report.md` and `burger.log` |
| Topology assets in phēnix | `<phenix dir>/topologies/burger-<topology>`. The `site` route copies nothing: its scenario's paths are used as they are |
| Rendered configs, bennu build | `/phenix/burger/<switchboard>/` |
| Apps install | pip into the phēnix container; `phenix-app-mirror` into `/usr/local/bin`; marker `/tmp/burger/installed.json` |
| bennu in the VMs | `/root/burger-bennu.deb` or the pybennu wheel (plus the switchboard's `bennu.pip` wheels), installed by `/etc/phenix/startup/00-burger-bennu.sh` before phēnix's startup scripts and `sceptre-start.sh`; log `/var/log/burger-bennu.log` |
| miniccc in the VMs | `/root/burger-miniccc`, installed and started by `/etc/phenix/startup/01-burger-miniccc.sh` on the bennu nodes and `monitor` (not Windows); log `/var/log/burger-miniccc.log` |
| Packages in the VMs | an item's `debs`, downloaded for jammy at `build` into `out/debs/`, injected to `/root/burger-debs/` and installed by `/etc/phenix/startup/02-burger-debs.sh`; log `/var/log/burger-debs.log` |
| SCORCH output | `/phenix/images/ebwes-<switchboard>/files/scorch/run-N/`, copied to `work/<switchboard>/results/` |
