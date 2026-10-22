import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from loguru import logger

from burger.bennu import bennu_startup, build_bennu
from burger.checks.status import collect_status, evaluate
from burger.compose import compose
from burger.config import BurgerError, Settings
from burger.iperf import build_iperf
from burger.render import DEBS_STARTUP, MINICCC_STARTUP, render
from burger.site import site_image
from burger.switchboard import Resolved
from burger.vcs import board_dir, load_manifest


def _built_key(man: dict) -> dict:
    return {"apps": man["apps"]["head"], "bennu": (man.get("bennu") or {}).get("sha")}


def _require_built(res: Resolved, settings: Settings, man: dict) -> Path:
    out = board_dir(settings, res.board.name) / "out"
    marker = out / "built.json"
    built = json.loads(marker.read_text()) if marker.is_file() else None
    if built != _built_key(man):
        raise BurgerError(
            f"switchboard '{res.board.name}' build does not match its sync; "
            f"run burger build {res.board.name}"
        )
    return out


def build(res: Resolved, settings: Settings, host) -> None:
    name = res.board.name
    rd, man = board_dir(settings, name), load_manifest(settings, name)
    out = rd / "out"
    out.mkdir(exist_ok=True)
    uid = f"{os.getuid()}:{os.getgid()}"

    # Python apps: a clean tree of the merged commit, installed into phēnix later.
    with (out / "apps-python.tar").open("wb") as f:
        host.run(["git", "-C", rd / "apps", "archive", "HEAD", "src/python"], stdout=f)

    # Go mirror app: static binary.
    go_image = site_image(host, "golang:1.24")
    host.run(
        [
            "docker", "run", "--rm", "-u", uid,
            "-v", f"{rd / 'apps' / 'src' / 'go'}:/src", "-v", f"{out}:/out",
            "-w", "/src", "-e", "CGO_ENABLED=0", "-e", "GOCACHE=/tmp/gocache",
            "-e", "GOPATH=/tmp/gopath", "-e", "GOFLAGS=-buildvcs=false", go_image,
            "go", "build", "-o", "/out/phenix-app-mirror", "./cmd/phenix-app-mirror",
        ]
    )  # fmt: skip

    packages = res.requires.debs_packages
    if packages:
        # jammy: phēnix's default image release, which bennu.qc2 is built on.
        shutil.rmtree(out / "debs", ignore_errors=True)
        (out / "debs").mkdir()
        apt_image = site_image(host, "ubuntu:22.04")
        host.run(
            [
                "docker", "run", "--rm", "-v", f"{out / 'debs'}:/out", "-w", "/out", apt_image,
                "bash", "-ec",
                f"apt-get update -qq && apt-get download -q {shlex.join(packages)} && "
                f"chown {uid} /out/*.deb",
            ]
        )  # fmt: skip

    if res.requires.iperf_static:
        build_iperf(host, settings, res.requires.iperf_static, out, uid)

    if man["bennu"]:
        spec = res.board.bennu or {}
        build_bennu(host, rd / "bennu", out, man, spec.get("install", ["deb"]), spec.get("pip", []))
    (rd / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    (out / "built.json").write_text(json.dumps(_built_key(man)))
    logger.info("switchboard '{}' built into {}", name, out)


def install(res: Resolved, settings: Settings, host) -> None:
    name = res.board.name
    man = load_manifest(settings, name)
    out = _require_built(res, settings, man)
    stage = f"/tmp/burger/{name}"
    host.ctr("rm", "-rf", stage)
    host.put(out / "apps-python.tar", f"{stage}/apps-python.tar")
    host.ctr("tar", "-xf", f"{stage}/apps-python.tar", "-C", stage)
    host.ctr(
        "python3", "-m", "pip", "install", "--quiet", "--break-system-packages", "--no-cache-dir",
        "--force-reinstall", "--no-deps", "--no-build-isolation", f"{stage}/src/python",
    )  # fmt: skip
    # --no-deps keeps the container's other packages alone; prove the deps are there.
    # --no-build-isolation: the container has setuptools but no route to an index.
    host.ctr("python3", "-c", "import phenix_apps.apps.scorch.app, phenix_apps.apps.sceptre")
    host.put(out / "phenix-app-mirror", "/usr/local/bin/phenix-app-mirror")
    host.ctr("chown", "0:0", "/usr/local/bin/phenix-app-mirror")  # docker cp keeps the host uid
    host.ctr("chmod", "0755", "/usr/local/bin/phenix-app-mirror")
    marker = json.dumps({"switchboard": name, "apps": man["apps"]["head"]})
    host.ctr("sh", "-c", f"echo {shlex.quote(marker)} > /tmp/burger/installed.json")
    where = f"container {settings.ctr}" if settings.ctr else "this host"
    logger.info(
        "installed apps {} (switchboard '{}') into {}", man["apps"]["head"][:10], name, where
    )


def up(res: Resolved, settings: Settings, host, timeout: int) -> None:
    name, exp = res.board.name, res.board.exp
    rd, man = board_dir(settings, name), load_manifest(settings, name)
    stage_dir = f"{settings.phenix_dir}/burger/{name}"
    out = _require_built(res, settings, man)
    if man.get("bennu"):
        (out / "00-burger-bennu.sh").write_text(bennu_startup(man))
    if res.requires.debs_packages:
        (out / "02-burger-debs.sh").write_text(DEBS_STARTUP)
    if settings.mm_ctr:
        copied = host.run(
            [
                "docker",
                "cp",
                f"{settings.mm_ctr}:/opt/minimega/bin/miniccc",
                out / "burger-miniccc",
            ],
            check=False,
        )
        if copied.returncode:
            raise BurgerError(
                f"cannot copy miniccc out of container '{settings.mm_ctr}'; set "
                "BURGER_MINIMEGA_CONTAINER to the minimega container ('' skips the"
                " miniccc injection)"
            )
        (out / "01-burger-miniccc.sh").write_text(MINICCC_STARTUP)
    topo_dir = rd / res.route.topology_dir if res.route.topology_dir else None
    topo, scen = render(res, man, topo_dir, stage_dir, settings)
    (out / "topology.json").write_text(json.dumps(topo, indent=2))
    (out / "scenario.json").write_text(json.dumps(scen, indent=2))
    (rd / "state.json").write_text(json.dumps({"linux_only": res.linux_only}))

    down(res, host)
    if topo_dir:  # the site route's assets are already where its scenario says
        topo_name = res.route.topology_dir
        host.put(topo_dir, f"{settings.phenix_dir}/topologies/burger-{topo_name}")
    host.put(out, stage_dir)
    host.phenix("config", "create", f"{stage_dir}/topology.json", f"{stage_dir}/scenario.json")
    host.phenix("experiment", "create", exp, "-t", exp, "-s", exp)
    host.phenix("experiment", "start", exp)
    wait_running(host, exp, timeout)
    fix_routers(host, settings, exp, topo)
    logger.info("experiment {} is up; next: burger scorch {}", exp, name)


def wait_running(host, exp: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while True:
        bad = not_running(host, exp)
        if not bad:
            return
        if time.time() > deadline:
            raise BurgerError(f"{exp} not running after {timeout}s: {bad}")
        time.sleep(10)


def not_running(host, exp: str) -> str:
    info = host.api(f"/experiments/{exp}")
    if not info:
        return "experiment not found"
    if not info.get("running"):
        return "experiment not running"
    vms = (host.api(f"/experiments/{exp}/vms") or {}).get("vms") or []
    if not vms:
        return "no VMs listed"
    down_vms = [
        f"{v.get('name')}={v.get('state')}"
        for v in vms
        if not v.get("dnb") and (v.get("state") or "").lower() != "running"
    ]
    return ", ".join(down_vms)


def is_router(node: dict) -> bool:
    # Topologies mark routers inconsistently; the image name is what matches in practice.
    drives = (node.get("hardware") or {}).get("drives") or [{}]
    return (
        node.get("type") == "Router"
        or (node.get("hardware") or {}).get("os_type") == "minirouter"
        or "minirouter" in str(drives[0].get("image", ""))
    )


def vm_uuids(host, exp: str, want: set, timeout: int = 60) -> dict:
    """name -> uuid from minimega (phēnix's API has no uuid), retried until
    every wanted VM is listed or the timeout passes."""
    deadline = time.time() + timeout
    while True:
        out = host.mm(
            exp, ".csv true .headers false .annotate false .columns name,uuid vm info"
        ).stdout
        uuids = dict(ln.strip().split(",", 1) for ln in out.splitlines() if ln.count(",") == 1)
        if want <= set(uuids) or time.time() > deadline:
            return uuids
        time.sleep(5)


def fix_routers(host, settings: Settings, exp: str, topo: dict) -> None:
    """Re-deliver each router's interface config over cc. The vrouter app can
    report success while the minirouter CLI fails (control socket missing),
    leaving every interface down. cc queues per filter until the client
    registers; each command ends with a show, so its response proves the state."""
    if not settings.mm_ctr:
        return
    routers = [n for n in topo["spec"]["nodes"] if is_router(n)]
    if not routers:
        return
    uuids = vm_uuids(host, exp, {n["general"]["hostname"] for n in routers})
    for node in routers:
        name = node["general"]["hostname"]
        uuid = uuids.get(name)
        if not uuid:
            logger.warning(
                "router '{}': not in minimega's VM list; skipping its interface fix", name
            )
            continue
        if host.mm(exp, "cc", "filter", f"uuid={uuid}").returncode:
            logger.warning(
                "router '{}': minimega in '{}' unreachable; skipping its interface fix",
                name,
                settings.mm_ctr,
            )
            continue
        for i, iface in enumerate(node["network"]["interfaces"]):  # minimega attaches NICs in order
            if not iface.get("address"):
                continue
            # busybox ip: no `addr replace`; `add` of a present address errors harmlessly.
            cmd = (
                f"ip link set eth{i} up; "
                f"ip addr add {iface['address']}/{iface['mask']} dev eth{i} 2>&1; "
                f"echo 1 > /proc/sys/net/ipv4/ip_forward; "
                f"ip -4 addr show dev eth{i}"
            )
            host.mm(exp, "cc", "exec", "/bin/sh", "-c", f'"{cmd}"')  # quoted: one argument to sh -c
        host.mm(exp, "clear", "cc", "filter")
        logger.info("router '{}': interface config queued over cc (vrouter workaround)", name)


def down(res: Resolved, host) -> None:
    exp = res.board.exp
    info = host.api(f"/experiments/{exp}")
    if info:
        if info.get("running"):
            host.phenix("experiment", "stop", exp)
        host.phenix("experiment", "delete", exp)
    host.phenix("config", "delete", f"scenario/{exp}", f"topology/{exp}", check=False, capture=True)


def collect(res: Resolved, settings: Settings, host, i: int, since: str = "") -> dict:
    """Status files of run i, keyed by (component, stage); `since` drops older files."""
    dst = board_dir(settings, res.board.name) / "results" / f"run-{i}"
    src = f"{settings.phenix_dir}/images/{res.board.exp}/files/scorch/run-{i}"
    if not host.fetch(src, dst):
        return {}
    return collect_status(dst, since)


def cc_registered(host, exp: str) -> set:
    """UUIDs of the cc clients registered in the experiment's namespace."""
    out = host.mm(exp, ".csv true .headers false .annotate false .columns uuid cc clients").stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def wait_for_cc(host, settings: Settings, exp: str, timeout: int = 300, clock=time) -> None:
    """Block until every VM of the run has registered with cc, or the timeout passes.
    A cc command sent to a client that has not registered yet can be lost (once
    commands are not re-delivered), so health must not start before this."""
    if not settings.mm_ctr:
        return
    vms = (host.api(f"/experiments/{exp}/vms") or {}).get("vms") or []
    want = {v["name"] for v in vms if v.get("name") and not v.get("dnb")}
    if not want:
        return
    deadline = clock.time() + timeout
    while True:
        uuids = vm_uuids(host, exp, want, timeout=-1)  # one look, no retry of its own
        registered = cc_registered(host, exp)
        missing = sorted(n for n in want if uuids.get(n) not in registered)
        if not missing:
            return
        if clock.time() > deadline:
            logger.warning("cc clients not registered after {} s: {}", timeout, ", ".join(missing))
            return
        clock.sleep(5)


def _kill_scorch(host) -> None:
    """The timed-out docker exec leaves the scorch run going in the container."""
    host.ctr("pkill", "-f", "experiment scorch", check=False)
    host.ctr("pkill", "-f", "phenix-scorch-component-", check=False)


def _text(x) -> str:
    return x.decode(errors="replace") if isinstance(x, bytes) else x or ""


def scorch(
    res: Resolved,
    settings: Settings,
    host,
    run: int | None,
    settle: int,
    clock=time,
    first_cap: int = 600,
) -> None:
    name, exp = res.board.name, res.board.exp
    state_path = board_dir(settings, name) / "state.json"
    if not state_path.is_file():
        raise BurgerError(f"switchboard '{name}' is not up; run burger up {name}")
    if json.loads(state_path.read_text())["linux_only"] != res.linux_only:
        raise BurgerError(f"switchboard '{name}' was brought up with a different --linux-only")
    components, runs = compose(res, load_manifest(settings, name))
    background = {c["name"] for c in components if c.get("background")}
    expect = {i.name: i.expect_error for i in res.items if i.expect_error}
    if run is not None and not 0 <= run < len(runs):
        raise BurgerError(f"switchboard '{name}' has runs 0-{len(runs) - 1}")
    results_dir = board_dir(settings, name) / "results"
    for i in range(len(runs)) if run is None else [run]:
        # Health waits on sceptre startup (field devices sleep 60 s, Windows
        # boots slowly), so retry it; every other run gets one shot.
        health = runs[i].get("name") == "health"
        deadline = None  # set when the first attempt ends: VM boot time is not billed to --settle
        if health:
            wait_for_cc(host, settings, exp, clock=clock)
            logger.info(
                "run {} (health): retrying until healthy, for at most {} s after the first "
                "attempt (--settle); the first attempt is capped at {} s",
                i,
                settle,
                first_cap,
            )
        busy = attempt = 0
        while True:
            since = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            began = clock.time()
            # The first health attempt has no settle clock yet, so it gets its own cap.
            capped = health and deadline is None
            timed_out = False
            try:
                res_run = host.phenix(
                    "experiment", "scorch", exp, "-r", str(i), check=False, capture=True,
                    **({"timeout": first_cap} if capped else {}),
                )  # fmt: skip
                output = _text(res_run.stdout) + _text(res_run.stderr)
                stderr = _text(res_run.stderr)
            except subprocess.TimeoutExpired as e:
                timed_out = True
                output = _text(e.stdout) + _text(e.stderr)
                stderr = ""
                logger.warning(
                    "run {} (health): first attempt still going after {} s; killing SCORCH",
                    i,
                    first_cap,
                )
                _kill_scorch(host)
            # A crashed attempt can still be winding down phēnix-side: wait it
            # out without spending the attempt (or the deadline) on it.
            if "already running" in stderr:
                busy += 1
                if busy > 20:
                    raise BurgerError(f"SCORCH on {exp} still busy after 10 min")
                logger.info("SCORCH is still finishing a previous run; retrying in 30 s")
                clock.sleep(30)
                continue
            attempt += 1
            log = results_dir / f"run-{i}-attempt-{attempt}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(output)
            if timed_out:
                failed = [("", "", "", "FAIL", f"first attempt exceeded {first_cap} s")]
            else:
                results = collect(res, settings, host, i, since)
                rows = evaluate(runs[i], results, background, expect)
                failed = [r for r in rows if r[3] == "FAIL"]
            if not failed:
                logger.success("run {} ({}) passed on attempt {}", i, runs[i].get("name"), attempt)
                break
            if deadline is None:
                deadline = clock.time() + (settle if health else 0)
            # Hard cap: retry only if another attempt like this one ends before the deadline.
            now = clock.time()
            length = now - began
            if now + 60 + length > deadline:
                if health:
                    logger.warning(
                        "run {} (health) still failing at the --settle cap: {}", i, failed[0][4]
                    )
                logger.warning("run {} attempt {} failed; SCORCH output:\n{}", i, attempt, output)
                break
            left = max(0, deadline - now)
            logger.info(
                "run {} ({}) attempt {} not healthy yet: {}; settle {} s left, ~{} more "
                "attempt(s) fit; retrying in 60 s (output: {})",
                i,
                runs[i].get("name"),
                attempt,
                failed[0][4],
                int(left),
                int(left // (60 + length)),
                log,
            )
            clock.sleep(60)
    logger.success("scorch done; next: burger verify {}", name)
