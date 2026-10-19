# Run book

```bash
./burger preflight            # host checks; add --linux-only without Windows images
./burger list                 # switchboards and items
./burger prs                  # my open PRs, the switchboard that carries each, stale syncs
./burger check metadata       # resolve and render offline; no phēnix needed
./burger run baseline         # sync, build, install, up, scorch, verify, down
./burger run bennu
./burger run pybennu
./burger run metadata
./burger run harden
./burger run opendss          # bennu #64 batched OpenDSS write; see Limitations
./burger install baseline     # put upstream main back into phēnix when done
```

The phēnix container's entrypoint reinstalls upstream phenix-apps on every
boot, so re-run `./burger install <switchboard>` after any phenix container
restart (`verify` flags the mismatch).

`scorch` (and `run`) retries the health run until it passes or `--settle`
seconds (default 900) run out, counted from the end of the first attempt so VM
boot time is not billed to it. The cap is hard: a retry starts only if an
attempt as long as the last one would end in time. Other runs get one attempt,
so no `--settle` value rescues a hung component.

`run` stops at the first failing step and leaves the experiment up for
debugging; after `verify` it deletes the experiment unless you pass `--keep`.
To debug, run the steps by hand:

```bash
./burger sync metadata        # fetch PR heads, merge onto main, write manifest.json
./burger build metadata       # apps tarball, phenix-app-mirror, bennu.deb
./burger install metadata     # pip install into the phenix container
./burger up metadata          # render topology + scenario, create and start ebwes-metadata
./burger scorch metadata      # run 0 (health) retries until healthy, then the rest once
./burger scorch metadata --run 1
./burger verify metadata      # report: work/metadata/report.md
./burger down metadata
./burger bennu-check bennu    # bennu's own docker harness: the #56 and #57 coverage
```

Every switchboard also gets a `health` run: a SCORCH `cc` component whose
checks (the `vm` checks of the `bennu/*` items) run inside the VMs: that
`bennu-field-deviced`, the Simulink provider and solver are running, that each
RTU can query the provider, that the installed bennu is the version the
switchboard built, and (with the Windows nodes) that something is polling
Modbus on each RTU. In `harden` it also checks that the monitor VM sees
mirrored Modbus traffic.

## The site route (your own rig)

The `rtds` switchboard runs on your RTDS setup instead of waterway: the
provider VM with pybennu and NTP, and optionally Elasticsearch and the RSCAD
automation server. Point it at your topology and scenario through the
environment; nothing site-specific goes into git:

```bash
export BURGER_SITE_TOPOLOGY=/path/to/site-topology.yaml   # required
export BURGER_SITE_SCENARIO=/path/to/site-scenario.yaml   # required
export BURGER_SITE_PROVIDER=power-provider                 # provider VM hostname
export BURGER_RTDS_ES_VERIFY=true BURGER_RTDS_ES_SERVER=http://es-host:9200 BURGER_RTDS_ES_INDEX=rtds-clean
export BURGER_RTDS_RSCAD=true BURGER_RTDS_RSCAD_URL=http://rscad-host:8000
./burger run rtds
```

Elasticsearch and RSCAD are off by default. The scenario keeps its own apps and
`assetDir`; any `scorch` app in it is replaced by the switchboard's pipeline.
The provider gets the PR build of pybennu (pip, `--no-deps`) before
`sceptre-start.sh` runs. `preflight` checks the waterway images, so ignore its
image rows for this switchboard.

After you push to a PR, `./burger prs` marks the switchboards that carry it
STALE; re-run `sync` and the rest.
