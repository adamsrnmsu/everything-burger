"""Field-device logs: pull them off the RTUs over cc, scan them, report them.

A bennu field device logs to stderr (and stdout), the sceptre watcher keeps its own
copies, and each protocol server writes `<proto>-server.log` in the device's cwd. A
crash, an fd leak or a flood shows up there first, so every run collects them.
"""

import base64
import json
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from burger.config import BurgerError
from burger.vcs import board_dir

TAIL_BYTES = 2_000_000  # per file, from the end
MAX_FILES = 40  # per host
OUT = "/tmp/burger-fdlogs"
FAIL_ON = ("crash", "error", "none")
DEFAULTS = {"fail_on": "none", "flood_bytes_per_min": 50_000_000, "repeat": 5000}

CRASH = re.compile(
    r"terminate called|Aborted|Segmentation fault|core dumped|double free|AddressSanitizer"
    r"|ThreadSanitizer|panic",
    re.I,
)
ERROR = re.compile(r"Too many open files|(?-i:(^|\s)E: )|error|failed", re.I)
_STAMP = re.compile(
    r"^\s*\[?(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?|[A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d"
    r"|\d\d:\d\d:\d\d(?:[.,]\d+)?)\]?\s*"
)

# Runs on the RTU under python3 (stdlib only). Copies the tail of every log into
# /tmp/burger-fdlogs and writes _manifest.json; never reads more than TAIL bytes.
VM_SCRIPT = r"""
import glob, json, os, shutil, time
TAIL, MAXF, OUT = %(tail)d, %(maxf)d, "/tmp/burger-fdlogs"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
def pids():
    for d in glob.glob("/proc/[0-9]*"):
        try:
            argv = open(d + "/cmdline", "rb").read().split(bytes(1))
        except OSError:
            continue
        if os.path.basename(argv[0]) in (b"bennu-field-device", b"bennu-field-deviced"):
            yield int(d.rsplit("/", 1)[1]), b" ".join(argv).decode(errors="replace")
found = {}  # real path -> why
procs = []
for pid, cmd in pids():
    p = {"pid": pid, "cmd": cmd[:200]}
    try:
        ticks = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19])
        hz = os.sysconf("SC_CLK_TCK")
        p["age_s"] = int(float(open("/proc/uptime").read().split()[0]) - ticks / hz)
    except (OSError, ValueError, IndexError):
        pass
    for fd, nm in ((1, "stdout"), (2, "stderr")):
        try:
            t = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        p[nm] = t
        if t.startswith("/") and os.path.isfile(t):
            found.setdefault(t, f"pid {pid} {nm}")
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        p["cwd"] = cwd
        for f in glob.glob(os.path.join(cwd, "*-server.log")):
            found.setdefault(os.path.normpath(f), f"pid {pid} protocol log")
    except OSError:
        pass
    procs.append(p)
for f in glob.glob("/etc/sceptre/log/*"):
    if os.path.isfile(f):
        found.setdefault(os.path.normpath(f), "watcher")
files = []
sizes = {}
for f in list(found)[:MAXF]:
    try:
        sizes[f] = os.path.getsize(f)
    except OSError:
        pass
t0 = time.time()
time.sleep(3)
dt = max(time.time() - t0, 1)
for i, (f, why) in enumerate(found.items()):
    if f not in sizes:
        continue
    try:
        size = os.path.getsize(f)
        with open(f, "rb") as src:
            src.seek(max(0, size - TAIL))
            data = src.read(TAIL)
        name = "%%02d-%%s" %% (i, os.path.basename(f))
        open(os.path.join(OUT, name), "wb").write(data)
        files.append({"src": f, "name": name, "why": why, "size": size, "kept": len(data),
                      "grew": size - sizes[f], "bytes_per_min": int((size - sizes[f]) / dt * 60)})
    except OSError as e:
        files.append({"src": f, "why": why, "error": str(e)})
json.dump({"procs": procs, "files": files, "skipped": max(0, len(found) - MAXF)},
          open(OUT + "/_manifest.json", "w"))
print(len(procs), "field device(s),", len(files), "file(s)")
"""


def vm_command() -> str:
    """The cc exec payload: the script as base64, so no `$` or quotes reach minimega."""
    script = VM_SCRIPT % {"tail": TAIL_BYTES, "maxf": MAX_FILES}
    b64 = base64.b64encode(script.encode()).decode()
    return f"echo {b64} | base64 -d | python3 -"


@dataclass
class Line:
    text: str
    count: int = 0
    first: str = ""
    last: str = ""


@dataclass
class FileScan:
    name: str
    src: str
    size: int
    kept: int
    crash: int = 0
    error: int = 0
    flood: str = ""  # why, or ""
    top: list[Line] = field(default_factory=list)


def _norm(line: str) -> tuple[str, str]:
    """(grouping key, timestamp): a line with its stamp and its numbers removed."""
    m = _STAMP.match(line)
    stamp = m.group(1) if m else ""
    body = line[m.end() :] if m else line
    return re.sub(r"\d+", "N", body).strip(), stamp


def scan_text(text: str, repeat: int) -> tuple[int, int, list[Line], str]:
    """Counts of crash-class and error-class lines, collapsed distinct lines (most
    frequent first, only crash/error ones), and a flood reason for one line repeated."""
    crash = error = 0
    seen: dict[str, Line] = {}
    all_counts: Counter = Counter()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, stamp = _norm(line)
        all_counts[key] += 1
        kind = 2 if CRASH.search(line) else 1 if ERROR.search(line) else 0
        if not kind:
            continue
        crash += kind == 2
        error += kind == 1
        entry = seen.setdefault(key, Line(_STAMP.sub("", line, 1)[:200], 0, stamp))
        entry.count += 1
        entry.last = stamp
    top = sorted(seen.values(), key=lambda x: -x.count)
    flood = ""
    if all_counts:
        key, n = all_counts.most_common(1)[0]
        if n > repeat:
            flood = f"one line repeated {n}x: {key[:60]}"
    return crash, error, top, flood


def scan_file(path: Path, meta: dict, flood_bpm: int, repeat: int) -> FileScan:
    text = path.read_text(errors="replace") if path.is_file() else ""
    crash, error, top, flood = scan_text(text, repeat)
    if meta.get("bytes_per_min", 0) > flood_bpm:
        flood = f"{meta['bytes_per_min'] // 1_000_000} MB/min" + (f"; {flood}" if flood else "")
    return FileScan(
        meta.get("name", ""), meta.get("src", ""), meta.get("size", 0), meta.get("kept", 0),
        crash, error, flood, top[:3],
    )  # fmt: skip


def scan_host(hostdir: Path, cfg: dict) -> dict:
    """One host's summary from its fdlogs directory (_manifest.json plus the tails)."""
    mf = hostdir / "_manifest.json"
    if not mf.is_file():
        return {"error": "no manifest received", "files": [], "procs": []}
    man = json.loads(mf.read_text())
    files = [
        scan_file(hostdir / f["name"], f, cfg["flood_bytes_per_min"], cfg["repeat"])
        for f in man["files"]
        if "name" in f
    ]
    top = Counter()
    firsts = {}
    for f in files:
        for ln in f.top:
            top[ln.text] += ln.count
            firsts.setdefault(ln.text, ln)
    return {
        "procs": man["procs"],
        "files": [
            {k: v for k, v in vars(f).items() if k != "top"} | {"top": [vars(x) for x in f.top]}
            for f in files
        ],
        "crash": sum(f.crash for f in files),
        "error": sum(f.error for f in files),
        "flood": any(f.flood for f in files),
        "top": [
            {"text": t, "count": n, "first": firsts[t].first, "last": firsts[t].last}
            for t, n in top.most_common(3)
        ],
    }


def settings_for(board) -> dict:
    """The fdlogs knobs: the board's `fdlogs:` over defaults. Boards that install a
    bennu deb from a PR or branch fail on crash-class lines; the rest only report."""
    cfg = dict(DEFAULTS)
    b = board.bennu
    if b and "deb" in b.get("install", ["deb"]):
        cfg["fail_on"] = "crash"
    cfg.update(board.fdlogs or {})
    if cfg["fail_on"] not in FAIL_ON:
        raise BurgerError(
            f"fdlogs.fail_on must be one of {', '.join(FAIL_ON)}, not {cfg['fail_on']!r}"
        )
    return cfg


def verdict(summary: dict, fail_on: str) -> tuple[str, str]:
    """(verdict, reason) for the whole section: ok, WARN or FAIL."""
    missing = [h for h, s in summary.items() if isinstance(s.get("error"), str)]
    hosts = [s for h, s in summary.items() if h not in missing]
    crash = sum(s["crash"] for s in hosts)
    error = sum(s["error"] for s in hosts)
    flood = [h for h, s in summary.items() if s.get("flood")]
    bits = [f"{crash} crash-class line(s)", f"{error} error-class line(s)"]
    if flood:
        bits.append(f"flood on {', '.join(flood)}")
    if missing:
        bits.append(f"no logs from {', '.join(missing)}")
    reason = "; ".join(bits)
    if (fail_on == "crash" and crash) or (fail_on == "error" and (crash or error)):
        return "FAIL", reason
    return ("WARN" if crash or error or flood or missing else "ok"), reason


def render_section(summary: dict, cfg: dict) -> list[str]:
    if not summary:
        return []
    out = [
        "",
        "## Field-device logs",
        "",
        f"fail_on: `{cfg['fail_on']}`; flood: over "
        f"{cfg['flood_bytes_per_min'] // 1_000_000} MB/min or one line more than "
        f"{cfg['repeat']}x. Only the last {TAIL_BYTES // 1_000_000} MB of each file is "
        "scanned; sizes are the whole file.",
        "",
        "| host | files | sizes | crash | error | flood | top lines |",
        "|---|---|---|---|---|---|---|",
    ]
    for host, s in summary.items():
        if isinstance(s.get("error"), str):
            out.append(f"| {host} | - | - | - | - | - | {s['error']} |")
            continue
        sizes = ", ".join(f"{Path(f['src']).name} {f['size']:,}" for f in s["files"])
        top = "<br>".join(f"{t['count']}x `{t['text'][:80]}`".replace("|", "/") for t in s["top"])
        flood = "; ".join(f"{Path(f['src']).name}: {f['flood']}" for f in s["files"] if f["flood"])
        out.append(
            f"| {host} | {len(s['files'])} | {sizes} | {s['crash']} | {s['error']} "
            f"| {flood or 'no'} | {top or '-'} |"
        )
    return out


def _last_id(host, exp: str) -> str:
    out = host.mm(exp, ".csv true .headers false .annotate false .columns id cc commands").stdout
    ids = [int(ln) for ln in out.split() if ln.isdigit()]
    return str(max(ids)) if ids else ""


def _answered(host, exp: str, cid: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = host.mm(
            exp, ".csv true .headers false .annotate false .columns id,responses cc commands"
        ).stdout
        if f"{cid},1" in out.split():
            return True
        time.sleep(2)
    return False


def collect_host(host, settings, exp: str, uuid: str, dst: Path, timeout: int = 90) -> str:
    """Run the VM script on one VM over cc and copy its /tmp/burger-fdlogs into dst.
    Returns "" or why it failed. cc filters are namespace-wide: nothing else may be
    sending commands meanwhile (verify runs after SCORCH)."""
    if host.mm(exp, "cc", "filter", f"uuid={uuid}").returncode:
        return f"minimega in '{settings.mm_ctr}' unreachable"
    try:
        host.mm(exp, "cc", "exec", "/bin/sh", "-c", f'"{vm_command()}"')
        host.mm(exp, "cc", "recv", f"{OUT}/*")
        cid = _last_id(host, exp)
    finally:
        host.mm(exp, "clear", "cc", "filter")
    if not cid or not _answered(host, exp, cid, timeout):
        return f"no cc response within {timeout} s"
    src = f"{settings.phenix_dir}/images/{exp}/miniccc_responses/{cid}/{uuid}{OUT}"
    if not host.fetch(src, dst):
        return "the VM sent no logs (no bennu field device?)"
    return ""


def collect(res, settings, host, cfg: dict) -> dict:
    """Fetch and scan every RTU's field-device logs into <board>/results/fdlogs/<host>/.
    Never raises: a host that cannot be reached is a summary entry saying why."""
    from burger.experiment import cc_registered, vm_uuids  # late: experiment imports verify's deps

    exp = res.board.exp
    root = board_dir(settings, res.board.name) / "results" / "fdlogs"
    shutil.rmtree(root, ignore_errors=True)
    rtus = res.route.values.get("rtus", [])
    summary: dict = {}
    if not settings.mm_ctr or not rtus:
        return summary
    try:
        uuids = vm_uuids(host, exp, set(rtus), timeout=-1)
        registered = cc_registered(host, exp)
        for name in rtus:
            if uuids.get(name) not in registered:
                summary[name] = {"error": "not registered with cc"}
                continue
            why = collect_host(host, settings, exp, uuids[name], root / name)
            summary[name] = {"error": why} if why else scan_host(root / name, cfg)
    except Exception as e:  # logs are evidence, never a reason to lose the run's verdict
        logger.warning("field-device log collection failed: {}: {}", type(e).__name__, e)
        summary["-"] = {"error": f"{type(e).__name__}: {e}"}
    if summary:
        root.mkdir(parents=True, exist_ok=True)
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
