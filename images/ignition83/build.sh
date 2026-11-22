#!/bin/bash
# usage: images/ignition83/build.sh [waterway-repo]   (default ../phenix-waterway-range next to this repo)
# Builds ignition83-commissioned-arm64 with the waterway repo's images/build.sh: copies the recipe into its images/recipes/
# (build.sh reads recipes/<name>.env there) and points SCRIPTS at this directory. Run stage.sh first.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WW=${1:-$HERE/../../../phenix-waterway-range}
test -x "$WW/images/build.sh" || { echo "no images/build.sh in $WW" >&2; exit 1; }
# never overwrite: waterway's build.sh deletes an image of the same name before it builds
if limactl shell phenix -- test -e /phenix/images/ignition83-commissioned-arm64.qc2; then
  echo "/phenix/images/ignition83-commissioned-arm64.qc2 exists: refusing to overwrite it" >&2; exit 1
fi
cp "$HERE/ignition83-commissioned-arm64.env" "$WW/images/recipes/"
trap 'rm -f "$WW/images/recipes/ignition83-commissioned-arm64.env"' EXIT  # leave the waterway checkout as it was
IGNITION83_DIR=$HERE "$WW/images/build.sh" ignition83-commissioned-arm64
