import struct

from burger.checks import pcap
from tests.conftest import make_ctx


def ip_packet(proto=6, sport=40000, dport=502):
    ports = struct.pack(">HH", sport, dport) + b"\0" * 16
    n = 20 + len(ports)
    hdr = struct.pack(">BBHHHBBH4s4s", 0x45, 0, n, 0, 0, 64, proto, 0, b"\1" * 4, b"\2" * 4)
    return hdr + ports


def eth(ip):
    return b"\0" * 12 + b"\x08\x00" + ip


def sll(ip):
    return b"\0" * 14 + b"\x08\x00" + ip


def classic(frames, link=1, end="<", magic=0xA1B2C3D4):
    out = struct.pack(end + "IHHiIII", magic, 2, 4, 0, 0, 65535, link)
    for f in frames:
        out += struct.pack(end + "IIII", 0, 0, len(f), len(f)) + f
    return out


def block(btype, body):
    pad = body + b"\0" * (-len(body) % 4)
    return struct.pack("<II", btype, len(pad) + 12) + pad + struct.pack("<I", len(pad) + 12)


def ng(frames, link=1):
    out = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    out += block(1, struct.pack("<HHI", link, 0, 65535))
    for f in frames:
        out += block(6, struct.pack("<IIIII", 0, 0, 0, len(f), len(f)) + f)
    return out


def modbus(wrap=eth):
    return [wrap(ip_packet(dport=502)) for _ in range(3)] + [wrap(ip_packet(dport=80))]


def test_pcap_counts_modbus(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic(modbus()))
    assert pcap.count_packets(p, tcp_port=502) == 3
    assert pcap.count_packets(p, udp_port=502) == 0
    assert pcap.count_packets(p) == 4
    p.write_bytes(classic(modbus(), end=">"))
    assert pcap.count_packets(p, tcp_port=502) == 3
    p.write_bytes(classic(modbus(), magic=0xA1B23C4D))
    assert pcap.count_packets(p, tcp_port=502) == 3


def test_pcap_sll_and_pcapng(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic(modbus(sll), link=113))
    assert pcap.count_packets(p, tcp_port=502) == 3
    p.write_bytes(ng(modbus()))
    assert pcap.count_packets(p, tcp_port=502) == 3


def test_pcap_vlan_and_raw(tmp_path):
    p = tmp_path / "a.pcap"
    tagged = b"\0" * 12 + b"\x81\x00\x00\x05\x08\x00" + ip_packet()
    p.write_bytes(classic([tagged]))
    assert pcap.count_packets(p, tcp_port=502) == 1
    p.write_bytes(classic([ip_packet()], link=101))
    assert pcap.count_packets(p, tcp_port=502) == 1


def test_pcap_gre(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic([eth(ip_packet(proto=47)), eth(ip_packet())]))
    assert pcap.count_packets(p, ip_proto=47) == 1


def test_pcap_truncated_tail(tmp_path):
    p = tmp_path / "a.pcap"
    data = classic(modbus())
    p.write_bytes(data[:-10])
    assert pcap.count_packets(p, tcp_port=502) == 3
    p.write_bytes(ng(modbus())[:-10])
    assert pcap.count_packets(p, tcp_port=502) == 3


def test_pcap_check_missing_is_fail(tmp_path, settings):
    ctx, _ = make_ctx(settings, tmp_path)
    r = pcap.pcap({"glob": "*.pcap", "tcp_port": 502}, ctx)
    assert r.verdict == "FAIL"
    assert r.reason.startswith("no output found at")
