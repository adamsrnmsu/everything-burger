import base64
import dataclasses
import struct
import subprocess
from typing import ClassVar

from burger import probes
from burger.probes import mm_capture
from tests.conftest import res as resolved
from tests.fakes import FakeHost, enforce

DIR = "/phenix/images"


def eth(vlan=None):
    tag = b"" if vlan is None else b"\x81\x00" + struct.pack(">H", vlan)
    return b"\xaa" * 6 + b"\xbb" * 6 + tag + b"\x08\x00" + b"payload!"


def classic(frames, magic=b"\xd4\xc3\xb2\xa1"):  # little endian
    out = magic + struct.pack("<HHiIII", 2, 4, 0, 0, 65535, 1)
    for f in frames:
        out += struct.pack("<IIII", 1, 2, len(f), len(f)) + f
    return out


def block(kind, body):
    pad = body + b"\0" * (-len(body) % 4)
    return struct.pack("<II", kind, len(pad) + 12) + pad + struct.pack("<I", len(pad) + 12)


def pcapng(frames, spb=()):
    out = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    out += block(1, struct.pack("<HHI", 1, 0, 65535))
    for f in frames:
        out += block(6, struct.pack("<IIIII", 0, 0, 0, len(f), len(f)) + f)
    for f in spb:
        out += block(3, struct.pack("<I", len(f)) + f)
    return out


def test_parsers():
    tagged, plain = eth(101), eth()
    assert mm_capture.pcap_frames(classic([tagged, plain])) == [tagged, plain]
    assert mm_capture.pcap_frames(classic([plain], b"\x4d\x3c\xb2\xa1")) == [plain]
    assert mm_capture.pcap_frames(pcapng([tagged], spb=[plain])) == [tagged, plain]
    assert mm_capture.pcap_frames(classic([plain])[:-3]) == []  # cut-off record
    assert mm_capture.vlan_of(tagged) == 101 and mm_capture.vlan_of(plain) is None


class FakeMM(FakeHost):
    """A minimega container: captures by command, files by name, errors by substring."""

    def __init__(self, settings, vlan_frames=(101,), refuse=()):
        super().__init__(None, settings)
        self.files, self.caps, self.mirrors = {}, [], []
        self.vlan_frames, self.refuse = vlan_frames, refuse

    def run(self, cmd, check=True, capture=False, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        out = lambda s="", rc=0: subprocess.CompletedProcess(cmd, rc, s, "")  # noqa: E731
        if cmd[2:4] == [self.s.mm_ctr, "printenv"]:
            return out(DIR + "\n")
        if cmd[3:4] == ["base64"]:
            data = self.files.get(cmd[4].rsplit("/", 1)[-1])
            return out(base64.encodebytes(data).decode()) if data else out("", 1)
        if cmd[3:4] == ["sh"]:
            self.files.clear()
            return out()
        words = cmd[-1].removeprefix("namespace ebwes-main ").split()
        if any(cmd[-1].endswith(r) for r in self.refuse):  # as minimega -e: exit 0, Error line
            return subprocess.CompletedProcess(cmd, 0, "", "Error (rig): refused\n")
        if words[:2] == ["capture", "pcap"] and words[2] == "delete":
            self.delete(words[3:])
        elif words[:2] == ["capture", "pcap"]:
            self.start(words[2:])
        elif words[-1] == "capture":
            return out(self.table(["id", "path"], [[i, f"{DIR}/{n}"] for i, n, *_ in self.caps]))
        elif words[-2:] == ["vm", "info"]:
            return out('name,bridge\nrtu-1,"[phenix, phenix]"\nrtu-2,"[phenix]"\n')
        elif words[-1] == "vlans":
            return out("alias,vlan\nEXP_1,101\n")
        elif words[-2:] == ["bridge", "mirrors"]:
            return out(self.table(["bridge", "namespace"], self.mirrors))
        return out()

    @staticmethod
    def table(head, rows):
        return "\n".join(",".join(map(str, r)) for r in [head, *rows]) + "\n"

    def start(self, w):
        kind, name = w[0], next(x for x in w if x.startswith("burger-mmcap-"))
        if kind == "bridge":
            data = classic([eth(v) for v in self.vlan_frames])
        elif kind == "namespace":
            data = pcapng([eth(1)])
        else:
            data = classic([eth()])
        self.caps.append((len(self.caps) + 1, name, data, kind))

    def delete(self, w):
        keep = []
        for c in self.caps:
            gone = w[-1] in ("all", str(c[0]), c[1]) or (w[0] == c[3] and w[0] != "id")
            if gone:
                self.files[c[1]] = c[2]
            else:
                keep.append(c)
        self.caps = keep


def run(settings, host, monkeypatch):
    monkeypatch.setenv("BURGER_MMCAP_SECONDS", "0")
    return mm_capture.run(host, settings, resolved(settings, "main"), {})


def verdicts(rows):
    return {r.check: r.verdict for r in rows}


def test_all_steps_pass(settings, monkeypatch):
    host = FakeMM(settings, refuse=["snaplen=70000", "vms=", "format="])
    rows = run(settings, host, monkeypatch)
    assert set(verdicts(rows).values()) == {"ok"}, rows
    assert len(rows) == 6 and {r.item for r in rows} == {"mm-capture"}
    assert host.caps == [] and host.files == {}  # captures stopped, files removed
    started = "namespace ebwes-main capture pcap bridge phenix "
    assert any(c[-1].startswith(started) for c in host.calls)  # the VMs' bridge


def test_wrong_vlan_and_accepted_options_fail(settings, monkeypatch):
    host = FakeMM(settings, vlan_frames=(101, 102))
    v = verdicts(run(settings, host, monkeypatch))
    assert v["bridge capture vlans="] == "FAIL" and v["bad options"] == "FAIL"
    assert v["vm capture"] == v["namespace capture pcapng"] == v["delete id"] == "ok"


def test_left_mirrors_fail(settings, monkeypatch):
    host = FakeMM(settings)
    host.mirrors = [["mega_bridge", "ebwes-main"]]
    assert verdicts(run(settings, host, monkeypatch))["mirrors cleaned"] == "FAIL"


def test_cleanup_runs_when_a_step_raises(settings, monkeypatch):
    host = FakeMM(settings)

    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(mm_capture.Probe, "vm", boom)
    rows = run(settings, host, monkeypatch)
    assert verdicts(rows)["vm capture"] == "FAIL" and "boom" in rows[0].reason
    cmds = [c[-1] for c in host.calls]
    assert any("delete namespace all" in c for c in cmds[-6:])
    assert any(c[-3:-1] == ["sh", "-c"] and "burger-mmcap-" in c[-1] for c in host.calls)


def test_registered_and_skips_without_rtus(settings):
    assert probes.PROBES["mm-capture"] is mm_capture.run
    res = resolved(settings, "main")
    res.route.values["rtus"] = []
    (row,) = mm_capture.run(FakeHost(None, settings), settings, res, {})
    assert row.verdict == "skipped"


def test_failing_mirrors_listing_fails(settings, monkeypatch):
    host = FakeMM(settings, refuse=["bridge mirrors"])  # e.g. a minimega without the command
    assert verdicts(run(settings, host, monkeypatch))["mirrors cleaned"] == "FAIL"


def test_skips_without_minimega_container(settings):
    s = dataclasses.replace(settings, mm_ctr="")
    (row,) = mm_capture.run(FakeHost(None, s), s, resolved(s, "main"), {})
    assert row.verdict == "skipped" and "BURGER_MINIMEGA_CONTAINER" in row.reason


class StrictMM(FakeMM):
    """FakeMM with the real Host's check/capture contract; `env` is what printenv answers
    (exit 1 and nothing when unset); two RTUs on different bridges."""

    env = DIR + "\n"
    broken: ClassVar[dict] = {}  # command text -> (returncode, stdout, stderr)

    def run(self, cmd, check=True, capture=False, **kw):
        res = super().run(cmd, check, capture, **kw)
        line = " ".join(map(str, cmd))
        if cmd[2:4] == [self.s.mm_ctr, "printenv"]:
            res = subprocess.CompletedProcess(cmd, 0 if self.env else 1, self.env, "")
        elif cmd[3:4] == ["sh"]:
            res = subprocess.CompletedProcess(cmd, 1, "", "")
        elif cmd[-1].endswith("vm info"):
            res = subprocess.CompletedProcess(
                cmd, 0, 'name,bridge\nrtu-1,"[br-one, br-two]"\nrtu-2,"[br-two]"\n', ""
            )
        for text, (rc, out, err) in self.broken.items():
            if text in line:
                res = subprocess.CompletedProcess(cmd, rc, out, err)
        return enforce(res, cmd, check, capture)


def strict_run(settings, monkeypatch, **cls):
    host = type("Host", (StrictMM,), cls)(settings, refuse=["snaplen=70000", "vms=", "format="])
    rows = run(settings, host, monkeypatch)
    return host, rows


def test_steps_pick_the_first_and_last_rtu_and_their_bridge(settings, monkeypatch):
    host, rows = strict_run(settings, monkeypatch)
    assert set(verdicts(rows).values()) == {"ok"}, rows
    cmds = [c[-1].removeprefix("namespace ebwes-main ") for c in host.calls]
    starts = [c.split()[:6] for c in cmds if c.startswith("capture pcap ")]
    assert ["capture", "pcap", "vm", "rtu-1", "0"] in [s[:5] for s in starts]
    assert any(c.startswith("capture pcap bridge br-one burger-mmcap-bridge") for c in cmds)
    assert "capture pcap delete vm rtu-1" in cmds and "capture pcap delete bridge br-one" in cmds
    # delete id: one capture on the first RTU, one on the last
    named = [c.split() for c in cmds if "burger-mmcap-a-" in c or "burger-mmcap-b-" in c]
    assert [(w[3], w[4]) for w in named] == [("rtu-1", "0"), ("rtu-2", "0")]
    for opt in ("vms=", "format="):  # refused: `capture pcap vm rtu-1 0 <file> opt`
        assert any(
            c.startswith("capture pcap vm rtu-1 0 burger-mmcap-bad") and c.endswith(opt)
            for c in cmds
        )
    assert rows[-1].reason == "none left"
    assert rows[3].reason == "id 1 gone, id 2 still listed"


def test_the_probe_runs_where_minimega_answers_even_with_no_file_path(settings, monkeypatch):
    host, rows = strict_run(settings, monkeypatch, env="")
    assert set(verdicts(rows).values()) == {"ok"}, rows
    assert any("rm -f /tmp/minimega/files/burger-mmcap-*" in c[-1] for c in host.calls)


def test_a_failed_command_says_why(settings, monkeypatch):
    broken = {"pcap vm rtu-1 0 burger-mmcap-vm": (1, "", "Error (rig): no such vm")}
    _, rows = strict_run(settings, monkeypatch, broken=broken)
    assert "no such vm" in rows[0].reason
    broken = {"bridge mirrors": (1, "", "Error (rig): no mirrors command")}
    _, rows = strict_run(settings, monkeypatch, broken=broken)
    assert "no mirrors command" in rows[-1].reason
    broken = {"base64": (1, "", "no such file")}
    _, rows = strict_run(settings, monkeypatch, broken=broken)
    assert "cannot read" in rows[0].reason and "no such file" in rows[0].reason


def test_a_vm_missing_from_vm_info_fails_the_bridge_step(settings, monkeypatch):
    broken = {"name,bridge vm info": (0, "name,bridge\nrtu-9,[x]\n", "")}
    _, rows = strict_run(settings, monkeypatch, broken=broken)
    assert verdicts(rows)["bridge capture vlans="] == "FAIL"
    assert "rtu-1 not in vm info" in rows[1].reason


def test_a_tagged_16_byte_frame_has_its_vlan():
    assert mm_capture.vlan_of(eth(101)[:16]) == 101
    assert mm_capture.vlan_of(eth(101)[:15]) is None


def test_parser_edges():
    import pytest

    plain = eth()
    with pytest.raises(ValueError, match="starts empty"):
        mm_capture.pcap_frames(b"")
    assert mm_capture.pcap_frames(classic([plain, b""])) == [plain, b""]  # empty last frame
    assert mm_capture.pcap_frames(pcapng([plain]) + block(3, b"")) == [plain, b""]
    pad = plain + b"\0\0"  # a simple packet holds its frame padded to 4 bytes
    assert mm_capture.pcap_frames(pcapng([], spb=[plain])) == [plain]
    short = pcapng([]) + block(3, struct.pack("<I", 99) + plain)
    assert mm_capture.pcap_frames(short) == [pad]  # origlen beyond the block is clipped to it
    assert mm_capture.pcap_frames(
        pcapng([plain]) + block(0x99, b"") + block(3, struct.pack("<I", 22) + plain)
    ) == [plain, plain]
    corrupt = pcapng([plain]) + struct.pack("<II", 6, 4) + b"\0" * 8
    assert mm_capture.pcap_frames(corrupt) == [plain]
    whole = pcapng([plain, plain, plain])
    assert mm_capture.pcap_frames(whole[:-5]) == [plain, plain]  # a cut-off last block


def test_delete_id_must_remove_only_that_capture(settings, monkeypatch):
    class Keeps(StrictMM):  # `delete id` succeeds but removes nothing
        def delete(self, w):
            if w[0] != "id":
                super().delete(w)

    class Wipes(StrictMM):  # `delete id` removes every capture
        def delete(self, w):
            super().delete(["id", "all"] if w[0] == "id" else w)

    for cls in (Keeps, Wipes):
        host = cls(settings, refuse=["snaplen=70000", "vms=", "format="])
        row = next(r for r in run(settings, host, monkeypatch) if r.check == "delete id")
        assert row.verdict == "FAIL" and row.reason.startswith(
            "BurgerError: after deleting id 1, listed: "
        )


def test_delete_id_captures_the_first_and_the_last_rtu(settings, monkeypatch):
    host = StrictMM(settings, refuse=["snaplen=70000", "vms=", "format="])
    monkeypatch.setenv("BURGER_MMCAP_SECONDS", "0")
    res = resolved(settings, "main")
    res.route.values["rtus"] = ["rtu-1", "rtu-2", "rtu-3"]
    mm_capture.run(host, settings, res, {})
    named = [
        c[-1].split()
        for c in host.calls
        if "burger-mmcap-a-" in c[-1] or "burger-mmcap-b-" in c[-1]
    ]
    assert [w[5] for w in named] == ["rtu-1", "rtu-3"]


def test_left_mirrors_are_named(settings, monkeypatch):
    host = StrictMM(settings)
    host.mirrors = [["mega_bridge", "ebwes-main"]]
    row = next(r for r in run(settings, host, monkeypatch) if r.check == "mirrors cleaned")
    assert (
        row.reason
        == "BurgerError: 1 mirrors left: {'bridge': 'mega_bridge', 'namespace': 'ebwes-main'}"
    )
