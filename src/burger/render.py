import copy
import json
from pathlib import Path

import yaml

from burger.bennu import bennu_injections
from burger.compose import compose
from burger.config import BurgerError, Settings
from burger.switchboard import Resolved

MONITOR_NODE = {
    "general": {"hostname": "monitor", "description": "burger mirror target"},
    "hardware": {
        "drives": [{"image": "bennu.qc2"}],
        "memory": 2048,
        "os_type": "linux",
        "vcpus": 2,
    },
    # `mirror` is the mirror app's own default (VLAN mirror, first target
    # 10.248.171.254/24), predefined so the guest brings up the GRE endpoint
    # whatever order the app and startup scripts run in.
    "network": {
        "interfaces": [
            {
                "name": "mgmt",
                "vlan": "MGMT",
                "address": "172.16.1.200",
                "mask": 16,
                "proto": "static",
                "type": "ethernet",
            },
            {
                "name": "mirror",
                "vlan": "mirror",
                "address": "10.248.171.254",
                "mask": 24,
                "proto": "static",
                "type": "ethernet",
            },
        ]
    },
    "type": "VirtualMachine",
}


# A Kali attacker on the ICS segment, injected when an item requires it (the
# ettercap MITM item). Sits with the RTUs and the router on ICS (10.1.1.0/24)
# so it can ARP man-in-the-middle any pair of them, and keeps a MGMT address
# so burger can cc into it. Added like MONITOR_NODE rather than committing a
# whole topology, so any route with an ICS segment can host the attack.
KALI_NODE = {
    "general": {"hostname": "kali", "description": "burger ettercap attacker"},
    "hardware": {
        "drives": [{"image": "kali.qc2"}],
        "memory": 4096,
        "os_type": "linux",
        "vcpus": 2,
    },
    "network": {
        "interfaces": [
            {
                "name": "IF0",
                "vlan": "ICS",
                "address": "10.1.1.200",
                "gateway": "10.1.1.1",
                "mask": 24,
                "proto": "static",
                "type": "ethernet",
            },
            {
                "name": "mgmt",
                "vlan": "MGMT",
                "address": "172.16.1.201",
                "mask": 16,
                "proto": "static",
                "type": "ethernet",
            },
        ]
    },
    "type": "VirtualMachine",
}


def _traffic_node(hostname: str, description: str, ics: str, mgmt: str) -> dict:
    return {
        "general": {"hostname": hostname, "description": description},
        "hardware": {
            "drives": [{"image": "ubuntu.qc2"}],
            "memory": 2048,
            "os_type": "linux",
            "vcpus": 2,
        },
        "network": {
            "interfaces": [
                {
                    "name": "IF0",
                    "vlan": "TRAFFIC",
                    "address": ics,
                    "mask": 24,
                    "proto": "static",
                    "type": "ethernet",
                },
                {
                    "name": "mgmt",
                    "vlan": "MGMT",
                    "address": mgmt,
                    "mask": 16,
                    "proto": "static",
                    "type": "ethernet",
                },
            ]
        },
        "type": "VirtualMachine",
    }


# The trafficgen component's three hosts, injected when an item requires them
# (requires.traffic_gen). They share their own TRAFFIC VLAN (10.1.9.0/24) so the
# generated traffic stays off the ICS/SCADA segments the other items watch; the
# background and malware clients target the traffic-server on that VLAN.
TRAFFIC_NODES = [
    _traffic_node("traffic-server", "burger trafficgen server", "10.1.9.10", "172.16.1.210"),
    _traffic_node(
        "background-gen", "burger trafficgen background client", "10.1.9.20", "172.16.1.211"
    ),
    _traffic_node("malware-gen", "burger trafficgen malware client", "10.1.9.30", "172.16.1.212"),
]


def _ics_server(
    hostname: str, about: str, image: str, *, memory: int, vcpus: int, host: int
) -> dict:
    """A VM on the ICS segment (10.1.1.<host>) with a MGMT address burger can cc into:
    172.16.1.<host + 10>. Taken so far: monitor .200, kali .201, trafficgen .210-.212."""
    return {
        "general": {"hostname": hostname, "description": about},
        "hardware": {
            "drives": [{"image": image}],
            "memory": memory,
            "os_type": "linux",
            "vcpus": vcpus,
        },
        "network": {
            "interfaces": [
                {
                    "name": "IF0",
                    "vlan": "ICS",
                    "address": f"10.1.1.{host}",
                    "gateway": "10.1.1.1",
                    "mask": 24,
                    "proto": "static",
                    "type": "ethernet",
                },
                {
                    "name": "mgmt",
                    "vlan": "MGMT",
                    "address": f"172.16.1.{host + 10}",
                    "mask": 16,
                    "proto": "static",
                    "type": "ethernet",
                },
            ]
        },
        "type": "VirtualMachine",
    }


# A Caldera C2 server on the ICS segment, injected when an item requires it (the
# caldera component). It sits with the RTUs so operations can reach them, keeps a
# MGMT address so burger can cc into it, and runs the Caldera server the
# component drives over its REST API (via cc). Needs a caldera.qc2 image with
# Caldera installed and the adversary/fact source the board names already loaded;
# built like KALI_NODE rather than committing a topology, so any ICS route hosts
# it.
CALDERA_NODE = _ics_server(
    "caldera", "burger Caldera C2 server", "caldera.qc2", memory=4096, vcpus=2, host=210
)
# An Ignition gateway on the ICS segment, injected when an item requires it (the
# ignition app item): waterway has no SCADA master that polls DNP3. Needs the
# ignition.qc2 image; the app writes its files into the gateway's data tree.
IGNITION_NODE = _ics_server(
    "ignition", "burger Ignition gateway", "ignition.qc2", memory=4096, vcpus=2, host=211
)


# On the rig tested, older bennu VMs had no miniccc binary and no virtio_console
# loaded, so they never registered with cc (an image built without `phenix image
# inject-miniexe`). The install path below stays as the rescue for such images:
# it installs the minimega container's own miniccc (same ron protocol) before
# sceptre's startup. A running miniccc must never be restarted: newer bennu.qc2
# images ship a working one, and `systemctl restart miniccc` drops its live ron
# connection. An in-flight once cc command is never re-delivered after that, so
# phēnix's mm_cc_send_wait (timeout=0.0) waits forever.
MINICCC_STARTUP = """#!/bin/bash
# install miniccc (matching the host's minimega) and start it
exec >>/var/log/burger-miniccc.log 2>&1
if pgrep -x miniccc >/dev/null; then
  echo "miniccc already running; leaving the image's own alone"
  exit 0
fi
install -D -m 0755 /root/burger-miniccc /opt/minimega/bin/miniccc
modprobe virtio_console
for _ in $(seq 60); do [ -e /dev/virtio-ports/cc ] && break; sleep 1; done
ls /dev/virtio-ports/
systemctl restart miniccc 2>/dev/null ||
  nohup /opt/minimega/bin/miniccc -v=false -serial /dev/virtio-ports/cc -logfile /var/log/miniccc.log &
"""  # noqa: E501


def _injection(src: str, dst: str, description: str, executable: bool = False) -> dict:
    """One phēnix file injection; `executable` gives it mode 0755."""
    inj = {"src": src, "dst": dst, "description": f"burger: {description}"}
    if executable:
        inj["permissions"] = "0755"
    return inj


def _staged_with_startup(stage_dir, payload, dst, what, script, does):
    """A file or directory `payload` staged into the VM at `dst`, and the startup
    `script` (in /etc/phenix/startup, run in name order) that installs it."""
    return [
        _injection(f"{stage_dir}/{payload}", dst, what),
        _injection(f"{stage_dir}/{script}", f"/etc/phenix/startup/{script}", does, executable=True),
    ]


# _staged_with_startup arguments: payload, its VM path, what it is, script, what it does
MINICCC_STAGE = (
    "burger-miniccc",
    "/root/burger-miniccc",
    "miniccc from the minimega container",
    "01-burger-miniccc.sh",
    "install and start miniccc",
)


def miniccc_injections(stage_dir):
    return _staged_with_startup(stage_dir, *MINICCC_STAGE)


DEBS_STARTUP = """#!/bin/bash
# everything-burger: install the round's packages
# find: works whether phēnix copies the directory's contents or the directory
# noninteractive: a package's debconf questions (snort's interface, say) take their defaults
export DEBIAN_FRONTEND=noninteractive
find /root/burger-debs -name '*.deb' -exec dpkg -i {} + >/var/log/burger-debs.log 2>&1
"""


DEBS_STAGE = (
    "debs/",
    "/root/burger-debs/",
    "the round's packages",
    "02-burger-debs.sh",
    "install the round's packages",
)


def debs_injections(stage_dir):
    return _staged_with_startup(stage_dir, *DEBS_STAGE)


def iperf_injections(stage_dir, version):
    name = f"iperf3-{version}"
    return [
        _injection(
            f"{stage_dir}/{name}",
            f"/usr/local/bin/{name}",
            f"static iperf3 {version}",
            executable=True,
        )
    ]


def goart_injections(stage_dir):
    # The art component expects goart already on the VM at /phenix/art/goart
    # (its default goartPath), so burger injects the binary rather than the
    # component sending it. The binary is staged from assets/art/goart.
    return [
        _injection(
            f"{stage_dir}/goart",
            "/phenix/art/goart",
            "goart (Atomic Red Team runner) for the art component",
            executable=True,
        )
    ]


def arm64_node(node: dict, image_map: dict) -> None:
    """Make an injected node boot on an arm64 range: the mapped image and the
    hardware block the range's own topologies use (UEFI firmware, virt machine,
    xhci in place of the PCI USB bus, partition 2 for phenix's file injection)."""
    node["hardware"]["cpu"] = "host"
    for d in node["hardware"]["drives"]:
        d["image"] = image_map.get(d["image"], d["image"])
        d.update(interface="virtio", inject_partition=2)
    node["advanced"] = {
        "machine": "virt",
        "vga": "virtio",
        "qemu-append": "-bios /usr/share/AAVMF/AAVMF_CODE.fd",
    }
    node["overrides"] = {"bus=usb-bus.0": "bus=xhci.0"}


def _load(path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise BurgerError(f"{path}: {e}") from None


def _load_docs(route, topology_dir: Path | None, name: str) -> tuple[dict, dict]:
    """The route's topology and scenario, renamed to the switchboard's experiment."""
    if route.base:
        topo, scen = _load(route.base["topology"]), _load(route.base["scenario"])
    else:
        topo = _load(Path(topology_dir) / route.files[0])
        scen = _load(Path(topology_dir) / route.files[1])
    topo["metadata"]["name"] = name
    scen["metadata"]["name"] = name
    scen["metadata"].setdefault("annotations", {})["topology"] = name
    return topo, scen


def _drop_nodes(res: Resolved, nodes: list[dict], scen: dict) -> None:
    """Remove the route's dropped nodes (and Windows under --linux-only) and their app hosts;
    an app whose every host went is removed too (soap's mirror to netmonitor)."""
    dropped = set(res.route.drop_nodes) - set(res.requires.keep_nodes)
    if res.linux_only:
        dropped |= {n["general"]["hostname"] for n in nodes if _is_windows(n)}
    if not dropped:
        return
    nodes[:] = [n for n in nodes if n["general"]["hostname"] not in dropped]
    apps = []
    for app in scen["spec"]["apps"]:
        if app.get("hosts"):
            app["hosts"] = [h for h in app["hosts"] if h["hostname"] not in dropped]
            if not app["hosts"]:
                continue
        apps.append(app)
    scen["spec"]["apps"] = apps


def _is_windows(node: dict) -> bool:
    return node["hardware"].get("os_type") == "windows"


def _required_nodes(req) -> list[dict]:
    """The nodes burger's own items need: they are injected and get burger's miniccc."""
    return [
        *([MONITOR_NODE] if req.monitor else []),
        *([KALI_NODE] if req.attacker else []),
        *(TRAFFIC_NODES if req.traffic_gen else []),
        *([CALDERA_NODE] if req.caldera_server else []),
        *([IGNITION_NODE] if req.ignition_server else []),
    ]


def _add_nodes(route, req, nodes: list[dict]) -> None:
    """Inject the required nodes the topology lacks (the mitm topology has its own kali)."""
    have = {n["general"]["hostname"] for n in nodes}
    added = [copy.deepcopy(n) for n in _required_nodes(req) if n["general"]["hostname"] not in have]
    if route.arm64:
        for n in added:
            arm64_node(n, route.image_map)
    nodes.extend(added)


def _rename_mgmt_vlan(nodes: list[dict], vlan: str | None) -> None:
    """Exercise case-insensitive mgmt VLAN matching (#158)."""
    if not vlan:
        return
    for n in nodes:
        for iface in n["network"]["interfaces"]:
            if iface.get("vlan") == "MGMT":
                iface["vlan"] = vlan


def _check_hosts(res: Resolved, nodes: list[dict], bennu_nodes: list[str]) -> None:
    hosts = {n["general"]["hostname"] for n in nodes}
    req = res.requires
    for key, want in (
        ("bennu_nodes", bennu_nodes),
        ("cc_nodes", req.cc_nodes),
        ("debs nodes", req.debs_nodes),
        ("iperf_static nodes", req.iperf_nodes),
        ("goart nodes", req.goart_nodes),
    ):
        missing = [h for h in want if h not in hosts]
        if missing:
            raise BurgerError(
                f"switchboard '{res.board.name}' {key} {missing} are not in the topology"
            )


def _injections(res, man, stage_dir, settings, nodes) -> list[tuple[set, list[dict]]]:
    """Which hosts get which injections, in the order a node receives them."""
    req = res.requires
    bennu_nodes = res.route.bennu_nodes if man.get("bennu") else []
    cc_hosts = set()
    if settings.mm_ctr:  # Windows images carry their own working miniccc
        linux = {n["general"]["hostname"] for n in nodes if not _is_windows(n)}
        cc_hosts = linux & (
            set(req.cc_nodes or res.route.bennu_nodes)
            | {n["general"]["hostname"] for n in _required_nodes(req)}
        )
    return [
        (set(bennu_nodes), bennu_injections(man, stage_dir) if bennu_nodes else []),
        (cc_hosts, miniccc_injections(stage_dir)),
        (set(req.debs_nodes), debs_injections(stage_dir)),
        (
            set(req.iperf_nodes) if req.iperf_static else set(),
            iperf_injections(stage_dir, req.iperf_static),
        ),
        (set(req.goart_nodes), goart_injections(stage_dir)),
    ]


def _inject(nodes: list[dict], injections: list[tuple[set, list[dict]]]) -> None:
    for n in nodes:
        for hosts, files in injections:
            if n["general"]["hostname"] in hosts:
                n.setdefault("injections", []).extend(copy.deepcopy(files))


def _scenario_apps(res: Resolved, man: dict, scen: dict) -> None:
    """The base scenario's apps, then the items' apps, then the switchboard's SCORCH
    pipeline (which replaces any the base scenario already has)."""
    components, runs = compose(res, man)
    extra = []
    for i in res.items:
        # mgmt-vlan-alias only renames the VLAN; it is not a scenario app.
        if i.kind == "app" and not i.requires.get("mgmt_vlan"):
            app = {"name": i.type or i.name}
            if i.config:
                app["metadata"] = copy.deepcopy(i.config)
            if i.hosts:
                app["hosts"] = copy.deepcopy(i.hosts)
            extra.append(app)
    scen["spec"]["apps"] = [a for a in scen["spec"]["apps"] if a["name"] != "scorch"]
    scen["spec"]["apps"] += extra
    scen["spec"]["apps"].append(
        {"name": "scorch", "metadata": {"components": components, "runs": runs}}
    )


def _replace(doc: dict, old: str, new: str) -> dict:
    return json.loads(json.dumps(doc).replace(old, new))


def render(
    res: Resolved, man: dict, topology_dir: Path | None, stage_dir: str, settings: Settings
) -> tuple[dict, dict]:
    """Build the switchboard's topology and scenario documents from its route's files."""
    route = res.route
    topo, scen = _load_docs(route, topology_dir, res.board.exp)
    nodes = topo["spec"]["nodes"]
    _drop_nodes(res, nodes, scen)
    _add_nodes(route, res.requires, nodes)
    _rename_mgmt_vlan(nodes, res.requires.mgmt_vlan)
    _check_hosts(res, nodes, route.bennu_nodes if man.get("bennu") else [])
    _inject(nodes, _injections(res, man, stage_dir, settings, nodes))
    _scenario_apps(res, man, scen)

    # Items that stage assets (requires-free `assets:`) reference them with the
    # {asset_dir} token; it resolves to where `up` puts them under the stage dir.
    scen = _replace(scen, "{asset_dir}", f"{stage_dir}/assets")
    if route.base:  # its assets are already where its scenario says
        return topo, scen
    # Both documents: soap's topology injects from its asset directory too.
    stock = f"/phenix/topologies/{route.topology_dir}"
    ours = f"{settings.phenix_dir}/topologies/burger-{route.topology_dir}"
    return _replace(topo, stock, ours), _replace(scen, stock, ours)
