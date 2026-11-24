#!/bin/sh
# A stored, never-started phenix experiment with a mirror app, for the mirror boards (apps #224,
# #227). Its configure takes the default mirror subnet (10.248.171.0/24), so the board's own
# experiment, created after it, must get a different one. Usage: mirror-decoy.sh start|stop
# Env: PHENIX_CTR (phenix), DECOY (burger-mirror-decoy).
set -eu
CTR=${PHENIX_CTR:-phenix}
D=${DECOY:-burger-mirror-decoy}
px() { docker exec "$CTR" phenix "$@"; }
stop() {
  px experiment delete "$D" >/dev/null 2>&1 || true
  px config delete "scenario/$D" >/dev/null 2>&1 || true
  px config delete "topology/$D" >/dev/null 2>&1 || true
}
case "${1:-}" in
start)
  stop
  # phenix config create reads one document per file
  docker exec -i "$CTR" sh -c "cat > /tmp/$D-topology.yaml" <<YAML
apiVersion: phenix.sandia.gov/v1
kind: Topology
metadata: {name: $D}
spec:
  nodes:
    - type: VirtualMachine
      general: {hostname: decoy-monitor}
      hardware: {os_type: linux, vcpus: 1, memory: 512, drives: [{image: bennu.qc2}]}
      network:
        interfaces:
          - {name: mirror, vlan: mirror, address: 10.248.171.254, mask: 24, proto: static, type: ethernet}
YAML
  docker exec -i "$CTR" sh -c "cat > /tmp/$D-scenario.yaml" <<YAML
apiVersion: phenix.sandia.gov/v2
kind: Scenario
metadata: {name: $D, annotations: {topology: $D}}
spec:
  apps:
    - name: mirror
      hosts: [{hostname: decoy-monitor, metadata: {interface: mirror, vlans: [mirror]}}]
YAML
  px config create "/tmp/$D-topology.yaml" "/tmp/$D-scenario.yaml" >/dev/null
  px experiment create "$D" -t "$D" -s "$D" >/dev/null
  echo "$D stored (not started)" ;;
stop) stop ;;
*) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
