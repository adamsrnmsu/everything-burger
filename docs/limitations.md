# Limitations and troubleshooting

- soap runs without `ignition`, `netmonitor` and `attacker` (the soap route's
  `drop_nodes`), so only `bennu.qc2` is needed; an item can keep one with
  `requires.keep_nodes`. Without Ignition nothing polls DNP3 on the RTUs, so a
  traffic check on soap would see nothing. soap's `mirror` app goes with
  `netmonitor`.
- The `opendss` switchboard does not run OpenDSS through the apps or the provider:
  the apps' `Simulator` enum has no OpenDSS and soap runs PyPower. The
  `opendss-batched-write` item builds the solver in-process on the provider VM
  with its sockets stubbed out, so the provider REP socket is not exercised. It
  needs `opendssdirect` on the VM; the pybennu wheel installs with `--no-deps`, so
  without it the row is WARN, not pass. To see the bug, drop the `bennu:` block
  (stock `bennu.qc2`): the row FAILs naming the load that did not change.
- The `ssh` switchboard reaches rtu-1 through `mgmt_tap`'s tap on the host, so it
  only works when the phēnix container shares the host's network.

- burger works around these rig, image and upstream problems:
  - On the rig tested, bennu VMs never registered with cc: no `miniccc` binary
    at `/opt/minimega/bin/miniccc` and no `virtio_console` loaded. That is
    likely an image built without `phenix image inject-miniexe`, not a defect
    in `bennu.qc2` itself. `up` injects the minimega container's own `miniccc`
    either way, so the agent always matches the server.
  - phenix-apps `scorch/cc/cc.py` crashes (`'NoneType' object has no attribute
    'encode'`) when a command with a validator prints nothing; every health
    command with a validator prints something. Reported as
    [sceptre-phenix-apps#186](https://github.com/sandialabs/sceptre-phenix-apps/issues/186).
  - The vrouter app reports success when the minirouter CLI cannot reach its
    control socket, leaving router interfaces down. Once the VMs run, `up`
    re-sends each router's interface addresses over cc (busybox-safe `ip addr
    add`, UUIDs from minimega's `vm info`). Reported as
    [sceptre-phenix-images#42](https://github.com/sandialabs/sceptre-phenix-images/issues/42).
- Behind a TLS-intercepting proxy, give burger a [site file](proxy.md): it
  carries the site CA, proxy and mirrors into every docker step, including
  `bennu-check`.
- SCORCH templating eats `${...}` in cc commands and validators; keep it out of
  any `cc` component, including the ones in `components/*.yaml`.

- Written against phēnix and minimega source; the first live run is its real test.
- `--linux-only` drops SCADA, OPC, HMI and historian, so nothing polls the RTUs
  and the Modbus and mirror checks are skipped.
- `--linux-only` in `harden` still adds the monitor VM and mirror app, but
  with no SCADA traffic its tcpdump captures nothing, so those checks are skipped.
- The erspan advisory run adds an ERSPAN port to the host's `phenix` OVS bridge
  until its cleanup stage runs.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'burger'` on a Mac.** macOS can mark the
  venv's `.pth` files hidden, and Python then skips them. Run
  `chflags nohidden .venv/lib/python3*/site-packages/*.pth`. The phēnix host
  (Linux) is not affected.
- **A switchboard fails before anything is built.** That is on purpose: unknown
  items, an item on a route it does not support, a missing PR an item needs,
  unset environment variables, and typos in item or switchboard fields are all
  reported at once, naming the file and field. `./burger check <switchboard>`
  shows the same errors offline.
- **`run` stopped and left the experiment up.** `run` stops at the first failing
  step so you can debug; `./burger down <switchboard>` removes it. The report
  is in `work/<switchboard>/report.md` and the log in
  `work/<switchboard>/burger.log`.
- **phēnix runs upstream apps again after a restart.** The phēnix container
  reinstalls upstream phenix-apps on every boot: re-run
  `./burger install <switchboard>` (`verify` flags the mismatch).
