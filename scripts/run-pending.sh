#!/usr/bin/env bash
# Run the boards with no live evidence yet: opendss (bennu #64), mtu (bennu #67),
# ssh (phenix-apps #170). --iperf-322 adds iperf-322 (#165 on iperf3 3.22).
# A failing board is recorded, brought down, and the next one still runs.
set -u
cd "$(dirname "$0")/.." || exit 1

boards=(opendss mtu ssh)
[[ ${1:-} == --iperf-322 ]] && boards+=(iperf-322)

if [[ -z ${BURGER_VM_PASSWORD:-} ]]; then
  read -rsp "BURGER_VM_PASSWORD (root password of the bennu.qc2 image): " BURGER_VM_PASSWORD
  echo
  export BURGER_VM_PASSWORD
fi

./burger preflight || { echo "preflight failed; fix the rig first" >&2; exit 2; }

results=()
for b in "${boards[@]}"; do
  if ./burger run "$b"; then
    results+=(PASS)
  else
    results+=(FAIL)
    ./burger down "$b"
  fi
done

rc=0
for i in "${!boards[@]}"; do
  printf '%-10s %s  work/%s/report.md\n' "${boards[i]}" "${results[i]}" "${boards[i]}"
  [[ ${results[i]} == PASS ]] || rc=1
done
exit $rc
