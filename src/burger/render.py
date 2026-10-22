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


def miniccc_injections(stage_dir):
    return [
        {
            "src": f"{stage_dir}/burger-miniccc",
            "dst": "/root/burger-miniccc",
            "description": "burger: miniccc from the minimega container",
        },
        {
            "src": f"{stage_dir}/01-burger-miniccc.sh",
            "dst": "/etc/phenix/startup/01-burger-miniccc.sh",
            "description": "burger: install and start miniccc",
            "permissions": "0755",
        },
    ]


DEBS_STARTUP = """#!/bin/bash
# everything-burger: install the round's packages
# find: works whether phēnix copies the directory's contents or the directory
find /root/burger-debs -name '*.deb' -exec dpkg -i {} + >/var/log/burger-debs.log 2>&1
"""


def debs_injections(stage_dir):
    return [
        {
            "src": f"{stage_dir}/debs/",
            "dst": "/root/burger-debs/",
            "description": "burger: the round's packages",
        },
        {
            "src": f"{stage_dir}/02-burger-debs.sh",
            "dst": "/etc/phenix/startup/02-burger-debs.sh",
            "description": "burger: install the round's packages",
            "permissions": "0755",
        },
    ]


def iperf_injections(stage_dir, version):
    name = f"iperf3-{version}"
    return [
        {
            "src": f"{stage_dir}/{name}",
            "dst": f"/usr/local/bin/{name}",
            "description": f"burger: static iperf3 {version}",
            "permissions": "0755",
        }
    ]


def _load(path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise BurgerError(f"{path}: {e}") from None


def render(
    res: Resolved, man: dict, topology_dir: Path | None, stage_dir: str, settings: Settings
) -> tuple[dict, dict]:
    """Build the switchboard's topology and scenario documents from its route's files."""
    route, req, name = res.route, res.requires, res.board.exp
    base = route.base
    if base:
        topo, scen = _load(base["topology"]), _load(base["scenario"])
    else:
        topo = _load(Path(topology_dir) / route.files[0])
        scen = _load(Path(topology_dir) / route.files[1])
    topo["metadata"]["name"] = name
    scen["metadata"]["name"] = name
    scen["metadata"].setdefault("annotations", {})["topology"] = name

    nodes = topo["spec"]["nodes"]
    dropped = set(route.drop_nodes) - set(req.keep_nodes)
    if res.linux_only:
        dropped |= {
            n["general"]["hostname"] for n in nodes if n["hardware"]["os_type"] == "windows"
        }
    if dropped:
        nodes[:] = [n for n in nodes if n["general"]["hostname"] not in dropped]
        apps = []
        for app in scen["spec"]["apps"]:
            if app.get("hosts"):
                app["hosts"] = [h for h in app["hosts"] if h["hostname"] not in dropped]
                if not app["hosts"]:  # every host dropped (soap's mirror to netmonitor)
                    continue
            apps.append(app)
        scen["spec"]["apps"] = apps
    if req.monitor:
        nodes.append(copy.deepcopy(MONITOR_NODE))
    if req.mgmt_vlan:  # exercise case-insensitive mgmt VLAN matching (#158)
        for n in nodes:
            for iface in n["network"]["interfaces"]:
                if iface.get("vlan") == "MGMT":
                    iface["vlan"] = req.mgmt_vlan
    hosts = {n["general"]["hostname"] for n in nodes}
    bennu_nodes = route.bennu_nodes if man.get("bennu") else []
    for key, want in (
        ("bennu_nodes", bennu_nodes),
        ("cc_nodes", req.cc_nodes),
        ("debs nodes", req.debs_nodes),
        ("iperf_static nodes", req.iperf_nodes),
    ):
        missing = [h for h in want if h not in hosts]
        if missing:
            raise BurgerError(
                f"switchboard '{res.board.name}' {key} {missing} are not in the topology"
            )
    for n in nodes:
        if n["general"]["hostname"] in bennu_nodes:
            n.setdefault("injections", []).extend(bennu_injections(man, stage_dir))
    if settings.mm_ctr:  # Windows images carry their own working miniccc
        cc_hosts = set(req.cc_nodes or route.bennu_nodes) | ({"monitor"} if req.monitor else set())
        for n in nodes:
            if n["general"]["hostname"] in cc_hosts and n["hardware"].get("os_type") != "windows":
                n.setdefault("injections", []).extend(miniccc_injections(stage_dir))
    for n in nodes:
        if n["general"]["hostname"] in req.debs_nodes:
            n.setdefault("injections", []).extend(debs_injections(stage_dir))
        if req.iperf_static and n["general"]["hostname"] in req.iperf_nodes:
            n.setdefault("injections", []).extend(iperf_injections(stage_dir, req.iperf_static))

    components, runs = compose(res, man)
    extra = []
    for i in res.items:
        # mgmt-vlan-alias only renames the VLAN above; it is not a scenario app.
        if i.kind == "app" and not i.requires.get("mgmt_vlan"):
            app = {"name": i.type or i.name}
            if i.config:
                app["metadata"] = copy.deepcopy(i.config)
            if i.hosts:
                app["hosts"] = copy.deepcopy(i.hosts)
            extra.append(app)
    # The switchboard's pipeline replaces any the base scenario already has.
    scen["spec"]["apps"] = [a for a in scen["spec"]["apps"] if a["name"] != "scorch"]
    scen["spec"]["apps"] += extra
    scen["spec"]["apps"].append(
        {"name": "scorch", "metadata": {"components": components, "runs": runs}}
    )

    if base:  # its assets are already where its scenario says
        return topo, scen
    # Both documents: soap's topology injects from its asset directory too.
    stock = f"/phenix/topologies/{route.topology_dir}"
    ours = f"{settings.phenix_dir}/topologies/burger-{route.topology_dir}"
    return tuple(json.loads(json.dumps(d).replace(stock, ours)) for d in (topo, scen))
