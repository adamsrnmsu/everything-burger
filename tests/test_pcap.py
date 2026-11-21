import struct

import pytest

from burger.checks import pcap
from burger.config import BurgerError
from tests.conftest import make_ctx


def ip_packet(proto=6, sport=40000, dport=502, src=b"\1" * 4, dst=b"\2" * 4):
    ports = struct.pack(">HH", sport, dport) + b"\0" * 16
    n = 20 + len(ports)
    hdr = struct.pack(">BBHHHBBH4s4s", 0x45, 0, n, 0, 0, 64, proto, 0, src, dst)
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


def fragment(offset=0, more=False, **kw):
    ip = bytearray(ip_packet(proto=17, **kw))
    ip[6:8] = struct.pack(">H", (0x2000 if more else 0) | offset)
    return bytes(ip)


def test_pcap_counts_ip_fragments(tmp_path):
    p = tmp_path / "a.pcap"
    whole = ip_packet(proto=17, sport=40000, dport=40000)
    first = fragment(more=True, sport=40000, dport=40000)  # MF set, offset 0
    last = fragment(offset=185, sport=40000, dport=40000)  # offset only: carries no ports
    p.write_bytes(classic([eth(whole), eth(first), eth(last)]))
    assert pcap.count_fragments(p) == 2
    assert pcap.count_packets(p, udp_port=40000) == 2  # the whole packet and the first fragment
    p.write_bytes(classic([eth(whole)]))
    assert pcap.count_fragments(p) == 0


def test_pcap_check_max_fragments(tmp_path, settings):
    ctx, loop = make_ctx(settings, tmp_path)
    spec = {"glob": "*.pcap", "udp_port": 40000, "min_packets": 1, "max_fragments": 0}
    whole = eth(ip_packet(proto=17, sport=40000, dport=40000))
    (loop / "a.pcap").write_bytes(classic([whole]))
    assert pcap.pcap(spec, ctx).verdict == "ok"
    (loop / "a.pcap").write_bytes(classic([whole, eth(fragment(more=True, sport=40000))]))
    r = pcap.pcap(spec, ctx)
    assert r.verdict == "FAIL" and "1 IP fragment(s), at most 0" in r.reason
    (loop / "a.pcap").write_bytes(classic([]))  # an empty capture proves nothing
    assert pcap.pcap(spec, ctx).verdict == "FAIL"


def test_pcap_src_dst_filter(tmp_path):
    opc, rtu = bytes([10, 1, 2, 50]), bytes([10, 1, 1, 101])
    p = tmp_path / "a.pcap"
    p.write_bytes(
        classic([eth(ip_packet(src=opc, dst=rtu))] * 3 + [eth(ip_packet(src=rtu, dst=opc))])
    )
    assert pcap.count_packets(p, tcp_port=502) == 4
    assert pcap.count_packets(p, tcp_port=502, src="10.1.2.50", dst="10.1.1.101") == 3
    assert pcap.count_packets(p, tcp_port=502, src="10.1.1.101", dst="10.1.2.50") == 1
    assert pcap.count_packets(p, tcp_port=502, src="10.9.9.9") == 0


def ng_be(frames, link=1):
    def blk(btype, body):
        pad = body + b"\0" * (-len(body) % 4)
        return struct.pack(">II", btype, len(pad) + 12) + pad + struct.pack(">I", len(pad) + 12)

    out = blk(0x0A0D0D0A, struct.pack(">IHHq", 0x1A2B3C4D, 1, 0, -1))
    out += blk(1, struct.pack(">HHI", link, 0, 65535))
    for f in frames:
        out += blk(6, struct.pack(">IIIII", 0, 0, 0, len(f), len(f)) + f)
    return out


def test_pcap_odd_ethernet_frames(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic([b"\0" * 13, b"\0" * 12 + b"\x08\x00", b"\0" * 12 + b"\x81\x00"]))
    assert pcap.count_packets(p) == 0
    exact = b"\0" * 12 + b"\x08\x00" + ip_packet()[:20]  # an IPv4 header and nothing after
    p.write_bytes(classic([exact]))
    assert pcap.count_packets(p) == 1


def test_pcap_sll2_and_short_or_wrong_version_ip(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(
        classic(
            [b"\x08\x00" + b"\0" * 18 + ip_packet(), b"\x86\xdd" + b"\0" * 18 + ip_packet()],
            link=276,
        )
    )
    assert pcap.count_packets(p, tcp_port=502) == 1
    v6 = bytes([0x65]) + ip_packet()[1:]
    p.write_bytes(classic([v6, ip_packet()[:19], ip_packet()[:20], b""], link=101))
    assert pcap.count_packets(p) == 1


def test_pcap_header_edge_cases(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(b"\x0a\x0d\x0d\x0a")
    assert pcap.count_packets(p) == 0
    for bad in (b"", b"\xd4\xc3\xb2\xa1" + b"\0" * 16, b"\0" * 30):
        p.write_bytes(bad)
        with pytest.raises(BurgerError, match="not a pcap"):
            pcap.count_packets(p)


def test_pcap_truncated_counts_every_whole_record(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic(modbus())[:-10])
    assert pcap.count_packets(p) == 3
    p.write_bytes(ng(modbus())[:-10])
    assert pcap.count_packets(p) == 3
    p.write_bytes(ng(modbus()))  # a block ending exactly at end of file is whole
    assert pcap.count_packets(p) == 4


def test_pcapng_big_endian_and_second_section(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(ng_be(modbus()))
    assert pcap.count_packets(p, tcp_port=502) == 3
    p.write_bytes(ng_be(modbus()) + ng(modbus(), link=101)[:0] + ng([ip_packet()], link=101))
    assert (
        pcap.count_packets(p, tcp_port=502) == 4
    )  # the second section resets endianness and links


def test_pcapng_skips_unknown_and_orphan_blocks(tmp_path):
    p = tmp_path / "a.pcap"
    empty = block(0x99, b"")  # a 12-byte block
    frame = eth(ip_packet())
    epb = lambda iface: block(6, struct.pack("<IIIII", iface, 0, 0, len(frame), len(frame)) + frame)  # noqa: E731
    shb, idb = ng([])[:28], block(1, struct.pack("<HHI", 1, 0, 65535))
    p.write_bytes(shb + epb(0) + idb + empty + epb(0) + epb(1) + epb(0))  # first and third orphaned
    assert pcap.count_packets(p) == 2


def test_pcapng_corrupt_block_length_ends_the_read(tmp_path):
    p = tmp_path / "a.pcap"
    good = ng(modbus())
    p.write_bytes(good + struct.pack("<II", 6, 0) + b"\0" * 8)
    assert pcap.count_packets(p) == 4


def test_pcapng_caplen_beyond_block_is_clipped(tmp_path):
    p = tmp_path / "a.pcap"
    hdr = ip_packet()[:20]
    data = ng([])[:28] + block(1, struct.pack("<HHI", 101, 0, 65535))
    epb = block(6, struct.pack("<IIIII", 0, 0, 0, 24, 24) + hdr)  # claims 4 bytes more than it has
    blen = len(epb)
    p.write_bytes(data + epb)
    port = struct.unpack(">H", struct.pack("<I", blen)[:2])[0]  # what the block trailer would say
    assert pcap.count_packets(p, tcp_port=port) == 0


def test_pcap_ip_proto_excludes_others_and_short_tcp(tmp_path):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic([eth(ip_packet())] * 3 + [eth(ip_packet(proto=47))]))
    assert pcap.count_packets(p, ip_proto="47") == 1
    short = ip_packet()
    p.write_bytes(classic([short[:22], short[:24]], link=101))
    assert pcap.count_packets(p, tcp_port=40000) == 1  # 24 bytes holds the ports, 22 does not


def test_pcap_check_needs_a_packet_by_default(tmp_path, settings):
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "a.pcap").write_bytes(classic([]))
    assert pcap.pcap({"glob": "*.pcap"}, ctx).verdict == "FAIL"
    (loop / "a.pcap").write_bytes(classic([eth(ip_packet())]))
    assert pcap.pcap({"glob": "*.pcap"}, ctx).verdict == "ok"


def test_pcap_sll2_matches_only_the_ip_ethertype(tmp_path):
    p = tmp_path / "a.pcap"
    ip4 = b"\x08\x00" + b"\0" * 18 + ip_packet()
    p.write_bytes(classic([ip4, ip4, b"\x86\xdd" + b"\0" * 18 + ip_packet()], link=276))
    assert pcap.count_packets(p) == 2


def test_pcap_header_only_file_has_no_packets_and_a_bad_file_is_a_failed_check(tmp_path, settings):
    p = tmp_path / "a.pcap"
    p.write_bytes(classic([]))
    assert pcap.count_packets(p) == 0
    ctx, loop = make_ctx(settings, tmp_path)
    (loop / "bad.pcap").write_bytes(b"not a capture")
    r = pcap.pcap({"glob": "*.pcap"}, ctx)
    assert r.verdict == "FAIL" and r.reason == "bad.pcap: not a pcap or pcapng file"
