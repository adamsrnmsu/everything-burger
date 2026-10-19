# Configuration

| Variable | Default | Meaning |
|---|---|---|
| `BURGER_PHENIX_CONTAINER` | `phenix` | container running phēnix; empty for a native install |
| `BURGER_API` | `http://localhost:3000/api/v1` | phēnix REST API |
| `BURGER_TOKEN` | unset | JWT, when phēnix auth is on |
| `BURGER_WORK` | `work/` in the current directory | clones, worktrees, builds, results |
| `BURGER_APPS_REPO`, `BURGER_BENNU_REPO`, `BURGER_TOPOLOGIES_REPO` | `work/repos/*` | use an existing clone; burger only adds `refs/burger/*` and worktrees |
| `BURGER_TOPOLOGIES` | unset | topologies clone for `check` and the tests, so they need no `sync` |
| `BURGER_MINIMEGA_CONTAINER` | `minimega` | container burger copies `miniccc` from and sends router cc commands through; empty disables both |
| `BURGER_PHENIX_DIR` | `/phenix` | phēnix's base directory (images, configs, SCORCH output) |
| `BURGER_SITE_TOPOLOGY`, `BURGER_SITE_SCENARIO` | unset | your topology and scenario, for the `site` route |
| `BURGER_SITE_PROVIDER` | `power-provider` | provider VM hostname on the `site` route |
| `BURGER_RTDS_*` | see [the site route](run-book.md#the-site-route-your-own-rig) | `ES_VERIFY`, `ES_SERVER`, `ES_INDEX`, `RSCAD`, `RSCAD_URL`: values the rtds items read |
| `BURGER_SITE_CONFIG` | `~/.config/burger/site.yaml` if it exists | the [site file](proxy.md): proxy, CA certificates and mirrors for docker steps and git |
| `BURGER_BENCH_TAGS`, `BURGER_BENCH_CYCLE_MS`, `BURGER_BENCH_SECS`, `BURGER_BENCH_MAX_CYCLE_S` | `800`, `100`, `20`, `2.0` | the `bench` switchboard: output tags, field-device scan cycle (ms), measuring window (s) and the pass limit (s per write cycle) |
| `BURGER_VM_PASSWORD` | unset | root password of the `bennu.qc2` image, for the `ssh` switchboard only. It ends up in the rendered `scenario.json`, so keep it a lab-only value |

Fixed: upstream repos are `sandialabs/*`, PRs merge onto `main`, and the
topologies (waterway, soap) come from the topologies repo's `main`.
