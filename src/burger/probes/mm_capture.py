"""mm-capture: minimega's capture engine (#1699 engine, pcapng, namespace and bridge
captures; #1800 vlans=) driven through the minimega container against the live experiment.

Captures are made in the experiment's namespace, so they die with `finally`, and the
files are read back from the container's capture directory (`MM_FILEPATH`)."""

import base64
import csv
import io
import os
import struct
import time

from burger.checks import Row
from burger.config import BurgerError

ITEM = "mm-capture"
BRIDGE = "mega_bridge"  # minimega's OVS bridge (cli.MEGA_BRIDGE; cli imports probes)
PREFIX = "burger-mmcap-"
CLASSIC = {b"\xa1\xb2\xc3\xd4", b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\x3c\x4d", b"\x4d\x3c\xb2\xa1"}
PCAPNG = b"\x0a\x0d\x0d\x0a"


def pcap_frames(data: bytes) -> list[bytes]:
    """The frames of a classic pcap or a pcapng file (EPB and SPB blocks)."""
    magic = data[:4]
    frames = []
    if magic in CLASSIC:
        e = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
        pos = 24
        while pos + 16 <= len(data):
            n = struct.unpack_from(e + "I", data, pos + 8)[0]
            if pos + 16 + n > len(data):  # a cut-off last record
                break
            frames.append(data[pos + 16 : pos + 16 + n])
            pos += 16 + n
    elif magic == PCAPNG:
        e = "<" if data[8:12] == b"\x4d\x3c\x2b\x1a" else ">"
        pos = 0
        while pos + 12 <= len(data):
            kind, size = struct.unpack_from(e + "II", data, pos)
            if size < 12 or pos + size > len(data):
                break
            if kind == 6:  # enhanced packet: iface, ts hi, ts lo, caplen, origlen, data
                n = struct.unpack_from(e + "I", data, pos + 20)[0]
                frames.append(data[pos + 28 : pos + 28 + n])
            elif kind == 3:  # simple packet: origlen, data
                n = min(struct.unpack_from(e + "I", data, pos + 8)[0], size - 16)
                frames.append(data[pos + 12 : pos + 12 + n])
            pos += size
    else:
        raise ValueError(f"not a pcap or pcapng file (starts {magic.hex() or 'empty'})")
    return frames


def vlan_of(frame: bytes) -> int | None:
    """The 802.1Q (or 802.1ad) VLAN of an Ethernet frame, None when untagged."""
    if frame[12:14] in (b"\x81\x00", b"\x88\xa8") and len(frame) >= 16:
        return struct.unpack_from(">H", frame, 14)[0] & 0x0FFF
    return None


def _table(res) -> list[dict]:
    return list(csv.DictReader(io.StringIO(res.stdout)))


class Probe:
    def __init__(self, host, s, exp: str, vms: list[str]):
        self.host, self.s, self.exp, self.vms = host, s, exp, vms
        self.tag = f"{os.getpid()}-{int(time.time())}"
        self.dir = ""

    def mm(self, *cmd):
        return self.host.mm(self.exp, *cmd)

    def ok(self, *cmd) -> str:
        """One minimega command that must succeed; returns its output."""
        res = self.mm(*cmd)
        if res.returncode:
            raise BurgerError(f"{' '.join(cmd)}: {(res.stderr or res.stdout).strip()}")
        return res.stdout

    def name(self, kind: str, ext: str = "pcap") -> str:
        return f"{PREFIX}{kind}-{self.tag}.{ext}"

    def table(self, *cmd) -> list[dict]:
        res = self.mm(".csv true .headers true .annotate false", *cmd)
        if res.returncode:  # an empty table must not read as "nothing there"
            raise BurgerError(f"{' '.join(cmd)}: {(res.stderr or res.stdout).strip()}")
        return _table(res)

    def read(self, name: str) -> bytes:
        res = self.host.run(
            ["docker", "exec", self.s.mm_ctr, "base64", f"{self.dir}/{name}"],
            check=False,
            capture=True,
        )
        if res.returncode:
            raise BurgerError(f"cannot read {name}: {(res.stderr or res.stdout).strip()}")
        return base64.b64decode(res.stdout)

    def listed(self, name: str) -> list[dict]:
        return [r for r in self.table("capture") if r.get("path", "").endswith(name)]

    def wait(self):
        time.sleep(int(os.environ.get("BURGER_MMCAP_SECONDS", "10")))

    def clean(self):
        for cmd in ("capture pcap delete namespace all", "capture pcap delete vm all"):
            self.mm(cmd)  # an error here means nothing to delete
        self.mm("capture pcap delete bridge all")

    def frames(self, name: str, magic) -> list[bytes]:
        data = self.read(name)
        if not (data[:4] in magic if isinstance(magic, set) else data[:4] == magic):
            raise BurgerError(f"unexpected file magic {data[:4].hex()}")
        if not (frames := pcap_frames(data)):
            raise BurgerError("no frames captured")
        return frames

    # each step returns the reason it passed with, or raises
    def vm(self):
        f = self.name("vm")
        self.ok("capture pcap vm", self.vms[0], "0", f)
        self.wait()
        self.ok("capture pcap delete vm", self.vms[0])
        return f"{len(self.frames(f, CLASSIC))} frames, classic pcap"

    def bridge(self):
        vlan = int(self.table("vlans")[0]["vlan"])
        f = self.name("bridge")
        self.ok("capture pcap bridge", BRIDGE, f, f"vlans={vlan}")
        self.wait()
        self.ok("capture pcap delete bridge", BRIDGE)
        frames = self.frames(f, CLASSIC)
        if bad := {vlan_of(x) for x in frames} - {vlan}:
            raise BurgerError(f"frames tagged {sorted(map(str, bad))}, not only {vlan}")
        return f"{len(frames)} frames, all tagged {vlan}"

    def namespace(self):
        f = self.name("ns", "pcapng")
        self.ok("capture pcap namespace", f, "format=pcapng")
        self.wait()
        self.ok("capture pcap delete namespace", f)
        return f"{len(self.frames(f, PCAPNG))} frames, pcapng"

    def delete_id(self):
        names = [self.name("a"), self.name("b")]
        for n, vm in zip(names, [self.vms[0], self.vms[-1]], strict=True):
            self.ok("capture pcap vm", vm, "0", n)
        ids = [(self.listed(n) or [{}])[0].get("id") for n in names]
        if not all(ids):
            raise BurgerError(f"captures not listed: {ids}")
        self.ok("capture pcap delete id", ids[0])
        left = [self.listed(n) for n in names]
        if left[0] or not left[1]:
            raise BurgerError(f"after deleting id {ids[0]}, listed: {left}")
        return f"id {ids[0]} gone, id {ids[1]} still listed"

    def bad_options(self):
        f = self.name("bad")
        cases = {  # the namespace caps snaplen at 65535; vms= is bridge-only; format= is empty
            "snaplen=70000": f"capture pcap namespace {f} snaplen=70000",
            "vms=": f"capture pcap vm {self.vms[0]} 0 {f} vms=",
            "format=": f"capture pcap vm {self.vms[0]} 0 {f} format=",
        }
        taken = [o for o, cmd in cases.items() if not self.mm(cmd).returncode]
        if taken:
            raise BurgerError(f"accepted: {', '.join(taken)}")
        return "all three refused"

    def mirrors(self):
        self.clean()
        left = [r for r in self.table("bridge mirrors") if r.get("namespace") == self.exp]
        if left:
            raise BurgerError(f"{len(left)} mirrors left: {left[0]}")
        return "none left"

    def run(self) -> list[Row]:
        steps = {
            "vm capture": self.vm,
            "bridge capture vlans=": self.bridge,
            "namespace capture pcapng": self.namespace,
            "delete id": self.delete_id,
            "bad options": self.bad_options,
            "mirrors cleaned": self.mirrors,
        }
        rows = []
        try:
            res = self.host.run(
                ["docker", "exec", self.s.mm_ctr, "printenv", "MM_FILEPATH"],
                check=False,
                capture=True,
            )
            self.dir = res.stdout.strip() or "/tmp/minimega/files"
            for check, step in steps.items():
                try:
                    rows.append(Row(ITEM, check, "ok", step(), []))
                except Exception as e:  # one step failing does not stop the rest
                    rows.append(Row(ITEM, check, "FAIL", f"{type(e).__name__}: {e}", []))
        finally:
            self.clean()
            self.host.run(
                ["docker", "exec", self.s.mm_ctr, "sh", "-c", f"rm -f {self.dir}/{PREFIX}*"],
                check=False,
                capture=True,
            )
        return rows


def run(host, s, res, man) -> list[Row]:
    if not s.mm_ctr:
        return [Row(ITEM, "probe", "skipped", "needs BURGER_MINIMEGA_CONTAINER", [])]
    vms = res.route.values.get("rtus", [])
    if not vms:
        return [Row(ITEM, "probe", "skipped", "the route has no RTUs", [])]
    return Probe(host, s, res.board.exp, vms).run()
