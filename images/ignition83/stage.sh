#!/bin/bash
# usage: images/ignition83/stage.sh [tag]   (Mac; drives the Lima VM "phenix")
# Extracts the Ignition 8.3 tree from inductiveautomation/ignition:<tag> (arm64) into the overlay for
# the ignition83-commissioned-arm64 recipe. Same route as phenix-waterway-range/images/ignition-stage.sh (8.1), but the
# tree lands at /opt/ignition: that is where the phenix ignition app writes its data tree (/opt/ignition/data).
set -euo pipefail
TAG=${1:-8.3.10}
VM() { limactl shell phenix -- "$@"; }
VM sudo docker pull -q --platform linux/arm64 inductiveautomation/ignition:$TAG
VM sudo docker rm -f ign83x >/dev/null 2>&1 || true
VM sudo docker create --platform linux/arm64 --name ign83x inductiveautomation/ignition:$TAG >/dev/null
OV=/phenix/images/overlays-src/ignition83-arm64
VM sudo rm -rf "$OV" /phenix/images/ign83-tree; VM sudo mkdir -p "$OV/opt" /phenix/images/ign83-tree
VM sudo docker cp ign83x:/usr/local/bin/ignition /phenix/images/ign83-tree/
VM sudo docker rm ign83x >/dev/null
# one tar: vmdb2's copy-dir hangs on the tree's symlinks (logs/wrapper.log -> /dev/stdout)
VM sudo tar -C /phenix/images/ign83-tree -cf "$OV/opt/ignition-tree.tar" ignition
VM sudo rm -rf /phenix/images/ign83-tree
# opt-in: the operator accepts the Inductive Automation EULA for headless commissioning
[[ ${IGNITION_ACCEPT_EULA:-0} = 1 ]] && VM sudo touch "$OV/opt/ignition-eula-accepted"
VM sudo ls -lh "$OV/opt/ignition-tree.tar"
