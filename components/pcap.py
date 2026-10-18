"""Python checks for the pcap item."""

from burger.checks.pcap import count_packets


def rtu1_has_no_modbus(ctx):
    """rtu-1's capture used its own `not tcp port 502` filter."""
    found = sorted(p for d in ctx.dirs for p in d.glob("rtu-1-*.pcap"))
    if not found:
        return False, "no rtu-1 capture"
    modbus = sum(count_packets(p, tcp_port=502) for p in found)
    return modbus == 0, f"{modbus} Modbus packet(s) in the filtered rtu-1 capture, want 0"
