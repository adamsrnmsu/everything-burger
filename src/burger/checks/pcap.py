"""Stdlib pcap/pcapng reader: counts IPv4 packets matching a TCP/UDP port or IP protocol,
and IP fragments."""

import socket
import struct
from collections.abc import Iterator
from pathlib import Path

from burger.checks.base import CheckContext, Row, find, missing, row
from burger.config import BurgerError

_CLASSIC = {  # first four bytes read little-endian -> file byte order (micro and nano magics)
    0xA1B2C3D4: "<",
    0xD4C3B2A1: ">",
    0xA1B23C4D: "<",
    0x4D3CB2A1: ">",
}
_SHB, _IDB, _EPB = 0x0A0D0D0A, 1, 6
_VLAN = (0x8100, 0x88A8)


def _records(data: bytes) -> Iterator[tuple[int, bytes]]:
    """(link type, packet bytes) per record; a truncated tail ends the iteration."""
    if data[:4] == b"\x0a\x0d\x0d\x0a":
        yield from _pcapng(data)
        return
    end = _CLASSIC.get(struct.unpack("<I", data[:4])[0]) if len(data) >= 24 else None
    if end is None:
        raise BurgerError("not a pcap or pcapng file")
    link = struct.unpack(end + "I", data[20:24])[0] & 0x0FFFFFFF
    pos = 24
    while pos + 16 <= len(data):
        incl = struct.unpack(end + "I", data[pos + 8 : pos + 12])[0]
        if pos + 16 + incl > len(data):
            return
        yield link, data[pos + 16 : pos + 16 + incl]
        pos += 16 + incl


def _pcapng(data: bytes) -> Iterator[tuple[int, bytes]]:
    end, links, pos = "<", [], 0
    while pos + 12 <= len(data):
        if data[pos : pos + 4] == b"\x0a\x0d\x0d\x0a":
            end = "<" if data[pos + 8 : pos + 12] == b"\x4d\x3c\x2b\x1a" else ">"
            links = []
        btype, blen = struct.unpack(end + "II", data[pos : pos + 8])
        if blen < 12 or pos + blen > len(data):
            return
        body = data[pos + 8 : pos + blen - 4]
        if btype == _IDB:
            links.append(struct.unpack(end + "H", body[:2])[0])
        elif btype == _EPB:
            iface, _, _, caplen = struct.unpack(end + "IIII", body[:16])
            if iface < len(links):
                yield links[iface], body[20 : 20 + caplen]
        pos += blen


def _ethernet_payload(pkt: bytes) -> bytes:
    """The bytes after the ethertype, VLAN tags skipped; empty unless the ethertype is IPv4."""
    off = 12
    while len(pkt) >= off + 2 and struct.unpack(">H", pkt[off : off + 2])[0] in _VLAN:
        off += 4
    if len(pkt) < off + 2 or struct.unpack(">H", pkt[off : off + 2])[0] != 0x0800:
        return b""
    return pkt[off + 2 :]


def _ipv4(link: int, pkt: bytes) -> bytes | None:
    if link == 1:  # Ethernet
        ip = _ethernet_payload(pkt)
    elif link == 113:  # Linux SLL
        ip = pkt[16:] if pkt[14:16] == b"\x08\x00" else b""
    elif link == 276:  # Linux SLL2
        ip = pkt[20:] if pkt[0:2] == b"\x08\x00" else b""
    elif link in (101, 228):  # raw IP
        ip = pkt
    else:
        return None
    return ip if len(ip) >= 20 and ip[0] >> 4 == 4 else None


def _ipv4_packets(path: Path) -> Iterator[bytes]:
    for link, pkt in _records(Path(path).read_bytes()):
        if (ip := _ipv4(link, pkt)) is not None:
            yield ip


def count_fragments(path: Path) -> int:
    """IPv4 packets that are a fragment: more-fragments set or a nonzero offset."""
    return sum(struct.unpack(">H", ip[6:8])[0] & 0x3FFF != 0 for ip in _ipv4_packets(path))


def _matches(ip: bytes, tcp_port, udp_port, ip_proto) -> bool:
    """Whether ip has the IP protocol ip_proto, or (in a first fragment) the right TCP/UDP
    port; with no criteria, every packet matches."""
    if tcp_port is None and udp_port is None and ip_proto is None:
        return True
    proto, hdr = ip[9], (ip[0] & 0x0F) * 4
    if ip_proto is not None and proto == int(ip_proto):
        return True
    want = tcp_port if proto == 6 else udp_port if proto == 17 else None
    first_fragment = struct.unpack(">H", ip[6:8])[0] & 0x1FFF == 0
    if want is None or not first_fragment or len(ip) < hdr + 4:
        return False
    return int(want) in struct.unpack(">HH", ip[hdr : hdr + 4])


def count_packets(
    path: Path, tcp_port=None, udp_port=None, ip_proto=None, src=None, dst=None
) -> int:
    """IPv4 packets with TCP port tcp_port, UDP port udp_port, or IP protocol ip_proto
    (any one matching counts); with no criteria, every IPv4 packet. src and dst, when given,
    first keep only packets from/to that IPv4 address."""
    want_src = socket.inet_aton(src) if src else None
    want_dst = socket.inet_aton(dst) if dst else None
    return sum(
        _matches(ip, tcp_port, udp_port, ip_proto)
        for ip in _ipv4_packets(path)
        if not ((want_src and ip[12:16] != want_src) or (want_dst and ip[16:20] != want_dst))
    )


def pcap(spec: dict, ctx: CheckContext) -> Row:
    found = find(spec, ctx)
    if not found:
        return missing(spec, ctx, "pcap")
    total = frags = 0
    for p in found:
        try:
            total += count_packets(
                p,
                spec.get("tcp_port"),
                spec.get("udp_port"),
                spec.get("ip_proto"),
                spec.get("src"),
                spec.get("dst"),
            )
            if "max_fragments" in spec:
                frags += count_fragments(p)
        except BurgerError as e:
            return row(ctx, "pcap", False, f"{p.name}: {e}", found)
    need = spec.get("min_packets", 1)
    ok, why = total >= need, f"{total} matching packet(s), need {need}"
    if "max_fragments" in spec:  # a fragment carries no ports, so this counts all of them
        most = spec["max_fragments"]
        ok, why = ok and frags <= most, f"{why}; {frags} IP fragment(s), at most {most}"
    return row(ctx, "pcap", ok, why, found)
