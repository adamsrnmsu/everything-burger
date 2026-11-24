"""mirror-subnet: the mirror app really set up this experiment's mirror (apps #224/#227).

The mirror app used to log a failed post-start and exit 0, and experiments created before any
of them started all got the default mirror subnet, so OVS rejected the second GRE port with the
same remote_ip. This reads the experiment's mirror status, the GRE port in OVS for each mirror
target, the target's address in the topology, and every other stored experiment's mirror subnet.
"""

import ipaddress
import json

from burger.checks import Row
from burger.config import BurgerError

ITEM = "mirror-subnet"


def _exp(host, name: str) -> dict:
    got = host.phenix("cfg", "get", f"experiment/{name}", "-o", "json", check=False, capture=True)
    if got.returncode:
        raise BurgerError(f"phenix cfg get experiment/{name}: {(got.stderr or '').strip()[-200:]}")
    return json.loads(got.stdout)


def _hosts(exp: dict) -> dict[str, str]:
    """Mirror target hostname -> its mirror interface's address/mask, from the topology."""
    spec = exp.get("spec") or {}
    apps = (spec.get("scenario") or {}).get("apps") or []
    app = next((a for a in apps if a.get("name") == "mirror"), None)
    nodes = {
        (n.get("general") or {}).get("hostname"): n
        for n in (spec.get("topology") or {}).get("nodes") or []
    }
    out = {}
    for h in (app or {}).get("hosts") or []:
        want = (h.get("metadata") or {}).get("interface")
        node = nodes.get(h.get("hostname")) or {}
        for i in (node.get("network") or {}).get("interfaces") or []:
            if i.get("name") == want and i.get("address"):
                out[h["hostname"]] = f"{i['address']}/{i.get('mask', 24)}"
    return out


def _subnets(exp: dict) -> set:
    nets = {ipaddress.ip_interface(a).network for a in _hosts(exp).values()}
    st = ((exp.get("status") or {}).get("apps") or {}).get("mirror") or {}
    if st.get("subnet"):
        nets.add(ipaddress.ip_network(st["subnet"], strict=False))
    return nets


def _ovs(host, s, *args) -> str:
    # ponytail: single-host rig, OVS in the minimega container; a cluster needs `mesh send`.
    got = host.run(["docker", "exec", s.mm_ctr, "ovs-vsctl", *args], check=False, capture=True)
    return got.stdout.strip() if not got.returncode else f"<{(got.stderr or '').strip()}>"


def run(host, s, res, man) -> list[Row]:
    name = res.board.exp
    exp = _exp(host, name)
    st = ((exp.get("status") or {}).get("apps") or {}).get("mirror") or {}
    rows = []

    def add(check, ok, why):
        rows.append(Row(ITEM, check, "ok" if ok else "FAIL", "" if ok else why, []))

    mirrors = st.get("mirrors") or {}
    add(
        "post-start status",
        bool(st.get("subnet") and mirrors),
        f"no mirror app status (subnet {st.get('subnet')!r}, {len(mirrors)} mirrors): "
        "post-start failed without failing the stage",
    )
    if not st.get("subnet"):
        return rows
    net = ipaddress.ip_network(st["subnet"], strict=False)

    targets = _hosts(exp)
    for vm, cfg in sorted(mirrors.items()):
        port, ip = cfg.get("mirrorName", ""), cfg.get("ip", "")
        kind = _ovs(host, s, "get", "interface", port, "type")
        remote = _ovs(host, s, "get", "interface", port, "options:remote_ip").strip('"')
        err = _ovs(host, s, "get", "interface", port, "error").strip('"')
        add(
            f"gre port {vm}",
            kind == "gre" and remote == ip and err in ("[]", ""),
            f"port {port}: type {kind}, remote_ip {remote}, want {ip}, error {err}",
        )
        addr = targets.get(vm, "")
        add(
            f"target address {vm}",
            bool(addr) and addr.split("/")[0] == ip and ipaddress.ip_address(ip) in net,
            f"topology {addr or 'none'}, status ip {ip}, subnet {net}",
        )

    clash = []
    data = host.api("/experiments") or {}
    for other in (data.get("experiments") if isinstance(data, dict) else data) or []:
        oname = other.get("name")
        if not oname or oname == name:
            continue
        if any(n.overlaps(net) for n in _subnets(_exp(host, oname))):
            clash.append(oname)
    add("subnet unique", not clash, f"{net} also used by {', '.join(clash)}")
    return rows
