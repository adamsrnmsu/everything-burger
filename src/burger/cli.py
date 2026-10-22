import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from burger import __version__, experiment, invalid, update, vcs, verify
from burger.bennu import bennu_check
from burger.checks import Row
from burger.compose import compose
from burger.config import REPOS, BurgerError, Settings
from burger.items import all_items, load_item
from burger.render import render
from burger.routes import load_route
from burger.shell import Host
from burger.site import load_site, probe, redact
from burger.switchboard import Resolved, all_switchboards, load_switchboard, resolve
from burger.ui import banner, results_table, setup_logging
from burger.verify import models_yaml

TEMPLATES = Path(__file__).parent / "templates"
KIND_DIR = {"scorch": "components", "app": "apps", "bennu": "bennu"}
MEGA_BRIDGE = "mega_bridge"  # minimega's OVS bridge; its VM taps are mega_tap*
PUBLIC_PROBE = "https://pypi.org/simple/"  # public: a proxy that re-signs only some TLS skips it
NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
# The old preflight's read probe: a read through the whole backing chain.
READ_PROBE = (
    'f=$1; [ -f "$f" ] || exit 2; files=$f; '
    "if command -v qemu-img >/dev/null; then "
    'files=$(qemu-img info -U --backing-chain "$f" | sed -n "s/^image: //p"); fi; '
    '[ -n "$files" ] || files=$f; '
    'cd "$(dirname "$f")" && for x in $files; do '
    'dd if="$x" of=/dev/null bs=64k count=1 status=none || exit 4; done'
)
out = Console()


def _board(s: Settings, name: str, linux_only: bool = False, log: bool = True) -> Resolved:
    """Load and resolve a switchboard by a name that must be one of the existing ones."""
    if name not in all_switchboards(s):
        raise BurgerError(f"no switchboard '{name}' (known: {', '.join(all_switchboards(s))})")
    if log:
        setup_logging(vcs.board_dir(s, name), _VERBOSE)
    return resolve(load_switchboard(name, s), s, linux_only)


def _state_linux_only(s: Settings, name: str) -> bool:
    path = vcs.board_dir(s, name) / "state.json"
    return bool(json.loads(path.read_text()).get("linux_only")) if path.is_file() else False


def _one(args, s, host, name: str) -> int:
    """One pipeline step on one switchboard; the exit code."""
    cmd = args.cmd
    if cmd in ("scorch", "verify", "down"):  # the experiment was brought up with its own flag
        lo = _state_linux_only(s, name)
    else:
        lo = getattr(args, "linux_only", False)
    res = _board(s, name, lo)
    if cmd == "sync":
        vcs.sync(res, s, host)
    elif cmd == "build":
        experiment.build(res, s, host)
    elif cmd == "install":
        experiment.install(res, s, host)
    elif cmd == "up":
        experiment.up(res, s, host, args.timeout)
    elif cmd == "scorch":
        experiment.scorch(res, s, host, args.run, args.settle, first_cap=args.first_cap)
    elif cmd == "verify":
        return _verify(res, s, host)
    elif cmd == "down":
        experiment.down(res, host)
    elif cmd == "bennu-check":
        if not vcs.load_manifest(s, name)["bennu"]:
            raise BurgerError(f"switchboard '{name}' has no bennu build")
        bennu_check(host, vcs.board_dir(s, name) / "bennu")
    return 0


def _step(args, s, host) -> int:
    names = args.board if isinstance(args.board, list) else [args.board]
    return max(_one(args, s, host, n) for n in names)


def _verify(res: Resolved, s, host) -> int:
    rows = verify.verify(res, s, host)
    out.print(results_table(rows))
    bad = sum(r.verdict == "FAIL" for r in rows)
    out.print(f"report: {vcs.board_dir(s, res.board.name) / 'report.md'}")
    return 1 if bad else 0


def _run(args, s, host) -> int:
    name = args.board
    for cmd in ("sync", "build", "install", "up", "scorch"):
        step = argparse.Namespace(**{**vars(args), "cmd": cmd, "run": None})
        _one(step, s, host, name)
        if cmd == "sync":  # render and compose offline first: hostname/run errors before docker
            res, rd = _board(s, name, args.linux_only), vcs.board_dir(s, name)
            topo = res.route.topology_dir
            stage = f"{s.phenix_dir}/burger/{name}"
            render(res, vcs.load_manifest(s, name), rd / topo if topo else None, stage, s)
    code = _verify(_board(s, name, args.linux_only), s, host)
    if code == 0 and not args.keep:  # a failing switchboard stays up to debug
        _one(argparse.Namespace(cmd="down"), s, host, name)
    return code


def _preflight(args, s, host) -> int:
    rows = []

    def chk(label, ok, hint=""):
        rows.append(Row("-", label, "ok" if ok else "FAIL", "" if ok else hint, []))

    for tool in ("git", "docker"):
        chk(f"{tool} on PATH", bool(shutil.which(tool)))
    if s.ctr:
        res = host.run(["docker", "inspect", "-f", "{{.State.Running}}", s.ctr], False, True)
        chk(
            f"container '{s.ctr}' running",
            res.stdout.strip() == "true",
            "set BURGER_PHENIX_CONTAINER (empty for a native install)",
        )
    chk("phenix CLI", host.phenix("version", check=False, capture=True).returncode == 0)
    chk(
        "minimega reachable",
        host.phenix("mm", "version", check=False, capture=True).returncode == 0,
    )
    try:
        host.api("/experiments")
        chk(f"REST API {s.api}", True)
    except (OSError, urllib.error.URLError) as e:
        chk(f"REST API {s.api}", False, f"{e}; set BURGER_API / BURGER_TOKEN")
    windows = load_route("waterway", s).windows_images
    for img in ["bennu.qc2", "minirouter.qc2", *([] if args.linux_only else windows)]:
        rc = host.ctr(
            "timeout", "60", "bash", "-c", READ_PROBE, "probe",
            f"{s.phenix_dir}/images/{img}", check=False,
        ).returncode  # fmt: skip
        hint = ""
        if rc not in (0, 2):
            hint = "unreadable: a read through its backing chain failed or hung (network storage?)"
        elif img in windows:
            hint = "build it or use --linux-only"
        chk(f"image {img}", rc == 0, hint)
    if s.mm_ctr:
        chk(
            f"miniccc in container '{s.mm_ctr}'",
            host.run(
                ["docker", "exec", s.mm_ctr, "test", "-f", "/opt/minimega/bin/miniccc"], check=False
            ).returncode
            == 0,
            "set BURGER_MINIMEGA_CONTAINER ('' skips the miniccc injection and router fix)",
        )
    chk(
        "pip in phēnix",
        host.ctr("python3", "-m", "pip", "--version", check=False, capture=True).returncode == 0,
    )
    df = host.ctr("df", "-Pk", s.phenix_dir, check=False, capture=True).stdout.split("\n")[1:2]
    free_gb = int(df[0].split()[3]) // 2**20 if df and df[0] else 0
    chk(
        f"{free_gb} GiB free on {s.phenix_dir}",
        free_gb >= 20,
        "switchboards need ~20 GiB of snapshots",
    )
    _leftover_rows(host, rows)
    if host.site:
        _site_rows(host, rows, chk)
    out.print(results_table(rows))
    return int(any(r.verdict == "FAIL" for r in rows))


def _leftover_rows(host, rows) -> None:
    """WARN on host taps and OVS ports that an earlier run left behind (never a FAIL)."""
    where = ["docker", "exec", host.s.mm_ctr] if host.s.mm_ctr else []
    links = host.run([*where, "ip", "-o", "link", "show"], check=False, capture=True)
    taps = sorted(set(re.findall(r"(?m)^\d+: (ebwes-[^:@\s]+)", links.stdout or "")))
    ovs = host.run([*where, "ovs-vsctl", "list-ports", MEGA_BRIDGE], check=False, capture=True)
    ports = (
        []
        if ovs.returncode
        else sorted(p for p in (ovs.stdout or "").split() if not p.startswith("mega_tap"))
    )
    cleanup = [f"ip link delete {t}" for t in taps] + [
        f"ovs-vsctl --if-exists del-port {MEGA_BRIDGE} {p}" for p in ports
    ]
    if cleanup:
        msg = f"leftover {', '.join(taps + ports)}; remove with: sudo {'; sudo '.join(cleanup)}"
        rows.append(Row("-", "leftover host state", "WARN", msg, []))


def _site_rows(host, rows, chk) -> None:
    """Preflight for the site file: CA bundle, and each mirror as burger will use it."""
    site = host.site
    chk(f"site file {site.path}", True)
    bundle = site.bundle(host.s.work)
    if bundle:
        chk(f"CA bundle ({bundle.read_text().count('BEGIN CERTIFICATE')} certs)", True)
    m = site.mirrors
    if m.get("registry"):
        image = site.image_name("ubuntu:22.04")
        res = host.run(["docker", "pull", image], check=False, capture=True)
        registry_host = m["registry"].split("/", 1)[0]
        chk(
            f"registry mirror: docker pull {image}",
            res.returncode == 0,
            f"the docker daemon pulls images: give it the CA in /etc/docker/certs.d/"
            f"{registry_host}/ca.crt and the proxy in its systemd http-proxy.conf drop-in",
        )
    if bundle:  # a public host the proxy does not re-sign must still verify with this bundle
        err = probe(PUBLIC_PROBE, site, bundle)
        if err is None:
            chk(f"CA bundle verifies {PUBLIC_PROBE}", True)
        elif "certificate" in err.lower():
            chk(
                f"CA bundle verifies {PUBLIC_PROBE}",
                False,
                f"{err}. ca_certs is the whole trust store: if the proxy passes some TLS "
                "through un-resigned, list the system bundle too "
                "(/etc/ssl/certs/ca-certificates.crt); see docs/proxy.md",
            )
        else:  # air-gapped sites reach no public host; that is not a CA problem
            rows.append(Row("-", f"CA bundle vs {PUBLIC_PROBE}", "WARN", f"not reached: {err}", []))
    for key, url in (
        ("pypi", m.get("pypi")),
        ("apt", m.get("apt") and m["apt"].rstrip("/") + "/dists/jammy/Release"),
        ("go", m.get("go")),
    ):
        if url:
            err = probe(url, site, bundle)
            chk(redact(f"{key} mirror {url}"), err is None, err or "")
    if m.get("git"):
        res = host.run(["git", "ls-remote", REPOS["apps"], "HEAD"], check=False, capture=True)
        chk(
            "git mirror (ls-remote sceptre-phenix-apps)",
            res.returncode == 0,
            redact(res.stderr.strip()),
        )
    if str(m.get("gosumdb", "")).lower() == "off":
        rows.append(Row("-", "GOSUMDB=off", "WARN", "Go checksum verification is off", []))


def _prs(args, s, host) -> int:
    table = Table("repo", "PR", "head", "title", "boards", "stale")
    for r in vcs.prs(s, host, args.author):
        stale = ", ".join(r["stale"])
        table.add_row(
            r["repo"], str(r["number"]), (r["head"] or "?")[:10], Text(r["title"]),
            ", ".join(r["boards"]), Text(stale, style="yellow"),
        )  # fmt: skip
    out.print(table)
    return 0


def _say(line: str) -> None:
    out.print(line, markup=False, highlight=False)


def _update(args, s, host) -> int:
    only = args.only.split(",") if args.only else list(update.LAYERS)
    rows = update.update(s, host, only, args.dry_run, args.force, _say)
    out.print(update.summary_table(rows))
    return 0


def _ref(path: Path) -> str:
    return path.stem if path.parent.name == "components" else f"{path.parent.name}/{path.stem}"


def _list(args, s, host) -> int:
    boards = Table("switchboard", "route", "PRs", "about")
    for name in all_switchboards(s):
        b = load_switchboard(name, s)
        prs = [str(n) for n in b.apps_prs]
        if b.apps_branch:
            prs = [f"{b.apps_branch['branch']} ({', '.join(prs) or 'no PRs'})"]
        if b.bennu:
            prs.append(f"bennu {b.bennu.get('pr') or b.bennu['branch']}")
        boards.add_row(name, b.route, ", ".join(prs), Text(b.about), end_section=False)
    items = Table("item", "kind", "routes", "about")
    for i in all_items(s):
        items.add_row(_ref(i.path), i.kind, ", ".join(i.routes) or "validate-only", Text(i.about))
    for t in (boards, items):
        t.columns[0].no_wrap = True
        out.print(t)
    return 0


def _new(args, s, host) -> int:
    if not NAME.fullmatch(args.name):
        raise BurgerError(f"bad name '{args.name}': use lowercase letters, digits, - and _")
    dest = s.root / KIND_DIR[args.kind] / f"{args.name}.yaml"
    if dest.exists():
        raise BurgerError(f"{dest} already exists; not overwriting")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text((TEMPLATES / f"{args.kind}.yaml").read_text().replace("__NAME__", args.name))
    load_item(_ref(dest), s)
    out.print(f"created {dest}: fill in `about`, then run burger check <switchboard>")
    return 0


def _synthetic_manifest(board) -> dict:
    """A stand-in for sync's manifest, so rendering needs no git."""
    man = {
        "apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
        "topologies": "d" * 40,
        "bennu": None,
    }
    if br := board.apps_branch:
        man["apps"] = {**br, "head": "a" * 40, "sha": "a" * 40, "prs": {}}
    if b := board.bennu:
        src = {k: b[k] for k in ("pr", "branch", "remote") if k in b}
        man["bennu"] = {**src, "sha": "c" * 40, "deb_version": "offline"}
        if "pybennu" in b.get("install", ["deb"]):
            man["bennu"]["wheel"] = "pybennu-offline-py3-none-any.whl"
        if b.get("pip"):
            man["bennu"]["wheels"] = [f"{p}-offline-py3-none-any.whl" for p in b["pip"]]
    return man


def _check(args, s, host) -> int:
    res = _board(s, args.board, args.linux_only, log=False)
    rd = vcs.board_dir(s, args.board)
    man = vcs.load_manifest(s, args.board) if (rd / "manifest.json").is_file() else None
    man = man or _synthetic_manifest(res.board)
    topo_dir = None
    if not res.route.base:
        env = os.environ.get("BURGER_TOPOLOGIES")
        topo_dir = (Path(env) if env else rd) / res.route.topology_dir
        if not topo_dir.is_dir():
            raise BurgerError(
                f"no topologies at {topo_dir}: set BURGER_TOPOLOGIES "
                f"or run burger sync {args.board}"
            )
    stage = f"{s.phenix_dir}/burger/{args.board}"
    topo, scen = render(res, man, topo_dir, stage, s)
    dest = rd / "check"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "topology.json").write_text(json.dumps(topo, indent=2))
    (dest / "scenario.json").write_text(json.dumps(scen, indent=2))
    _, runs = compose(res, man)
    table = Table("item", "kind", "advisory", "about")
    for i in res.items:
        table.add_row(i.name, i.kind, "yes" if i.advisory else "", Text(i.about))
    for i in res.validate_only:
        table.add_row(i.name, i.kind, "validate-only", Text(i.about))
    out.print(table)
    for n, run in enumerate(runs):
        stages = "; ".join(
            f"{k}: {', '.join(run[k])}"
            for k in ("configure", "start", "stop", "cleanup")
            if run.get(k)
        )
        out.print(f"run {n} {run.get('name')}: {stages}", markup=False, highlight=False)
    out.print(f"ok: {args.board} renders; wrote {dest}/topology.json and scenario.json")
    models_yaml(res, dest / "models.yaml")
    rows = [
        *invalid.scorch_rows(res, s, host, dest / "models.yaml"),
        *invalid.app_rows(res, s, host, topo, dest / "apps"),
    ]
    if rows:  # items that must be rejected, judged on the synced apps tree
        out.print(results_table(rows))
    return 1 if any(r.verdict == "FAIL" for r in rows) else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="burger", description="Switchboards of tests for sceptre-phenix"
    )
    p.add_argument("--version", action="store_true")
    p.add_argument("--quiet", action="store_true", help="no banner")
    p.add_argument("-v", dest="verbose", action="store_true", help="debug logs")
    sub = p.add_subparsers(dest="cmd")

    def add(name, help_, *flags, fn=_step, board="board", **kw):
        sp = sub.add_parser(name, help=help_, **kw)
        sp.set_defaults(fn=fn)
        if board:
            sp.add_argument(board, nargs="+" if name == "sync" else None, metavar="board")
        for f, k in flags:
            sp.add_argument(f, **k)
        return sp

    lo = ("--linux-only", {"action": "store_true", "help": "drop the Windows nodes"})
    to = ("--timeout", {"type": int, "default": 900, "help": "seconds to wait for VMs"})
    se = ("--settle", {"type": int, "default": 900, "help": "cap on seconds retrying health"})
    fc = (
        "--first-cap",
        {"type": int, "default": 600, "help": "cap on seconds for the first health attempt"},
    )
    keep = ("--keep", {"action": "store_true", "help": "leave the experiment running"})
    add(
        "run",
        "sync, build, install, up, scorch, verify, down",
        lo,
        to,
        se,
        fc,
        keep,
        fn=_run,
        aliases=["round"],
    )
    add("sync", "fetch PR heads and merge the switchboard's trees", lo)
    add("build", "build the apps tarball, mirror binary and bennu", lo)
    add("install", "install the apps into phēnix", lo)
    add("up", "render and start the experiment", lo, to)
    add("scorch", "run the SCORCH runs", ("--run", {"type": int}), se, fc)
    add("verify", "check install, experiment, models and SCORCH results")
    add("down", "delete the experiment and configs")
    add("bennu-check", "run bennu's docker test harness on the board's bennu PR")
    add("preflight", "check the phēnix host can run the boards", lo, fn=_preflight, board=None)
    add("prs", "my open PRs, the boards that carry them, stale syncs",
        ("--author", {"default": "@me"}), fn=_prs, board=None)  # fmt: skip
    add("list", "switchboards and items", fn=_list, board=None)
    add(
        "update",
        "bring burger, the phēnix containers, the apps and bennu.qc2 up to main",
        ("--only", {"help": f"comma list of {', '.join(update.LAYERS)} (default: all)"}),
        ("--dry-run", {"action": "store_true", "help": "print the commands, change nothing"}),
        ("--force", {"action": "store_true", "help": "update even while an experiment runs"}),
        fn=_update,
        board=None,
    )
    sp = add("new", "copy a commented item template", fn=_new, board=None)
    sp.add_argument("kind", choices=list(KIND_DIR))
    sp.add_argument("name")
    add("check", "resolve and render offline", lo, fn=_check)
    return p


_VERBOSE = False


def main(argv: list[str] | None = None) -> int:
    global _VERBOSE
    err = Console(stderr=True)
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:  # argparse: 2 for usage, 0 for --help
        return e.code if isinstance(e.code, int) else 2
    if args.version:
        out.print(f"burger {__version__}", markup=False)
        return 0
    if not args.cmd:
        err.print("usage: burger [--quiet] [-v] <command> ...  (burger --help)", markup=False)
        return 2
    _VERBOSE = args.verbose
    setup_logging(None, args.verbose)
    if not args.quiet and sys.stdout.isatty():
        banner(out)
    s = Settings.from_env()
    try:
        host = Host(s, load_site())
        return args.fn(args, s, host)
    except (BurgerError, urllib.error.URLError) as e:
        err.print(f"burger: {e}", style="red", markup=False, highlight=False, soft_wrap=True)
        return 1
