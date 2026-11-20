# Run book

```bash
./burger preflight            # host checks, WARNs on leftover ebwes-* taps / OVS ports; add --linux-only without Windows images
./burger list                 # switchboards and items
./burger prs                  # my open PRs, the switchboard that carries each, stale syncs
./burger check main       # resolve and render offline; no phēnix needed
./burger run baseline         # sync, build, install, up, scorch, verify, down
./burger run bennu
./burger run pybennu
./burger run metadata
./burger run harden
./burger run bench           # field-device write cycle at 800 tags; see Limitations
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

The settle clock starts only after the first attempt, so the first health
attempt has its own cap, `--first-cap` (default 600 s). When it expires burger
kills the SCORCH run (`pkill -f 'experiment scorch'` and any leftover
`phenix-scorch-component-*` processes in the phēnix container), counts it as a
failed attempt and the retry loop takes over. Before the health run, burger
waits for every VM's cc client to register, so a command is not lost to a
client that is still booting. Each attempt's SCORCH output goes to
`work/<switchboard>/results/run-<i>-attempt-<n>.log`; when the run finally
fails, the console names the failing check and that log in one line. On a
terminal the attempts and retries show as one live status line.

`run` stops at the first failing step and leaves the experiment up for
debugging; after `verify` it deletes the experiment unless you pass `--keep`.
To debug, run the steps by hand:

```bash
./burger sync metadata        # fetch PR heads, merge onto main, write manifest.json
./burger build metadata       # apps tarball, phenix-app-mirror, bennu.deb
./burger install metadata     # pip install into the phenix container
./burger up metadata          # render topology + scenario, create and start ebwes-main
./burger scorch metadata      # run 0 (health) retries until healthy, then the rest once
./burger scorch metadata --run 1
./burger verify metadata      # report: work/main/report.md; RTU logs: results/fdlogs/<host>/
./burger down metadata
./burger bennu-check bennu    # bennu's own docker harness on the board's bennu PR
./burger probe main mm-bindings # host-side probes (none named: the board's own) while ebwes-main is up (--keep)
```

One board runs one command at a time: `run` and every step command take a lock
on the board (a file in the temp directory, so it holds on a VM mount too), and a
second command on the same board fails at once with
`switchboard '<name>' is busy: pid <n>: burger <args>`. Other boards are free.

Every switchboard also gets a `health` run: a SCORCH `cc` component whose
checks (the `vm` checks of the `bennu/*` items) run inside the VMs: that
`bennu-field-deviced`, the Simulink provider and solver are running, that each
RTU can query the provider, that the installed bennu is the version the
switchboard built, and (with the Windows nodes) that something is polling
Modbus on each RTU. In `harden` it also checks that the monitor VM sees
mirrored Modbus traffic.

## Run a board on a minimega PR or branch

Any board can run on a proposed minimega, to see that phēnix still works with it:

```bash
./burger run main --minimega pr:1782              # a sandia-minimega/minimega PR
./burger run main --minimega branch:my-fix        # a branch on your fork
./burger run main --minimega branch:dev@upstream  # a branch on upstream
```

A board can also carry it as a `minimega:` key ([switchboards](switchboards.md)).
When both are set the flag wins, and burger warns with the value it overrides:
`--minimega pr:1782 overrides switchboard 'x' minimega branch:foo (fork)`.
Only `sync` (and so `run`) takes the flag: it writes the choice to
`manifest.json`, and the later steps read it there.

- `build` runs `docker build -f docker/Dockerfile` on the checkout
  (`work/<switchboard>/minimega`) and tags it `burger/minimega:<sha12>`
  (`<sha12>-<layer hash>` on a route with a `minimega_layer`); an existing tag is
  reused. That Dockerfile runs minimega's own check, build and
  test scripts, so a change that breaks minimega fails here, before any VM starts.
- `install` recreates the compose `minimega` service on that image, through the
  override file `work/minimega/compose.override.yml`, then installs the image's
  `minimega.py` into phēnix. Recreating minimega kills every VM, so `install`
  refuses while any experiment runs. With the same commit already running (the
  re-test loop `sync`, `build`, `install`) it leaves the container alone.
  minimega must run under docker compose, and `BURGER_MINIMEGA_CONTAINER` must
  name its container.
- `up` copies `miniccc` out of the swapped container, so the VMs get the matching
  agent.
- `verify` adds a `minimega container` row (the running container is the commit
  in the manifest) and a `minimega.py in phēnix` row (phēnix imports that
  container's file). On a board without its own minimega the first row fails when
  the container is still a swapped one.
- `down` puts stock minimega and its `minimega.py` back once the experiment is
  deleted. It skips that, with a warning, while another experiment still runs;
  the `down` of the last one restores. A failing run and `--keep` leave the
  experiment up, so minimega stays swapped until `./burger down <switchboard>`;
  the summary says so. When `run` dies in `install`, `up` or `scorch`, a warning
  says so instead, if the minimega or phēnix container carries burger's label.
  `./burger install <a board without minimega>` and
  `./burger update --only containers` also put stock minimega and its
  `minimega.py` back.

The build happens on the phēnix host from a pushed ref: push the branch to your
fork first, or point `BURGER_MINIMEGA_REPO` at a clone on that host.

## Run a board on a phenix PR or branch

The same as a minimega PR, for sceptre-phenix:

```bash
./burger run arm64 --phenix pr:576               # a sandialabs/sceptre-phenix PR
./burger run main --phenix branch:my-fix         # a branch on your fork
./burger run main --phenix branch:dev@upstream   # a branch on upstream
```

A board can carry it as a `phenix:` key ([switchboards](switchboards.md)); the flag wins
and burger warns with the value it overrides. `sync` and `run` take the flag.

- `sync` fetches it into `work/<switchboard>/phenix`; the manifest records `phenix`.
- `build` runs `docker build` on its `docker/Dockerfile` as `docker/Dockerfile.burger`
  (the route's `phenix_sed` applied first, `PHENIX_COMMIT` set, the first `FROM`, the
  minimega stage, pointed at the board's minimega image so the bindings come from it) and tags
  it `burger/phenix:<sha12>-<minimega hash>`; an existing tag is reused.
- `install` recreates the compose `phenix` service on that image through its own override file
  `work/phenix/compose.override.yml` (minimega's is `work/minimega/compose.override.yml`; the
  two never overwrite each other), labelled `burger.phenix` and `burger.phenix.image`. It does
  this first: a recreated phēnix has neither burger's apps nor the board's `minimega.py`, so
  `install` then installs the apps, then swaps minimega. A board without `phenix:` puts stock
  back here, which also undoes a swap a killed run left. Like minimega it refuses while an
  experiment runs. phēnix must run under docker compose and `BURGER_PHENIX_CONTAINER` must
  name its container.
- `up` ensures the same before minimega. If it had to recreate phēnix (a `down` since
  `install`), the apps are gone: run `install` again.
- `verify` adds a `phenix container` row (the running container is the manifest's image).
- `down` puts stock phēnix back once no other experiment runs.

## Bring a stale host up to main

```bash
./burger update --dry-run     # every command, nothing changed (probes still run)
./burger update               # all four layers
./burger update --only apps,bennu
```

Layers run in this order; `--only` takes a comma list. Each prints what it
found before and after, and the run ends with a table.

1. **burger**: refuses a dirty checkout or a branch other than `main`, then
   `git pull --ff-only` and `pip install -e .`. If HEAD moved it re-executes
   `./burger update --only <the rest>` so the new code runs the remaining layers.
2. **containers**: the phēnix and minimega containers, through the compose labels
   on each (`docker compose pull <service>`, then `up -d <service>`), then waits for
   `phenix version` and `phenix mm version`. A container compose did not create gets
   the commands to run by hand and is skipped; a service built from a local context
   is reported, not rebuilt. Image ids are printed before and after.
3. **apps**: upstream phenix-apps `main` through the normal sync, build and install,
   using a built-in board named `main`. It runs after the containers because the phēnix
   entrypoint reinstalls upstream on every boot.
4. **bennu**: builds `bennu.deb` and the pybennu wheel from bennu `main`, then runs
   what the `bennu` target of [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images)
   runs, through the phēnix container: `phenix image delete bennu` if the config
   exists, `image create` with a copy of `scripts/bennu.sh` that installs those two
   files from an extra overlay (`overlays/burger-bennu`) instead of downloading
   them, `image build`, and `miniccc` injection. helics and libzmq are not built
   by burger, so they still come from the latest sceptre-bennu release.
   The result is `/phenix/images/bennu-main-<sha7>.qc2`; a real `bennu.qc2` is first
   moved to `bennu-<YYYYMMDD>.qc2`, then `bennu.qc2` becomes a symlink to the new
   file. The printed rollback is one `ln -sfn` that points it back.
   The images checkout lives in `/phenix/burger/images` (`BURGER_IMAGES_DIR`); create
   that directory first on a path the container sees identically, burger clones
   into it. Burger refuses to run if `bennu.qc2` or `bennu.tar` is already in that
   directory (a failed earlier build); it never deletes them for you.

The containers and bennu layers refuse while any phēnix experiment is running;
`--force` skips that check. Existing experiments hold their disks on the old
`bennu.qc2`, so stop them first.

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

## Boards with no live evidence yet

`scripts/run-pending.sh` runs `opendss`, `mtu` and `ssh` (add `--iperf-322` for the
fourth). It runs `preflight` first, prompts for `BURGER_VM_PASSWORD` if unset, brings a
failed board down and continues, then prints pass/fail and each `work/<board>/report.md`.
