#!/usr/bin/env bash
# Run the groundtruth boards in order: stock, #85 alone, then #84 + #85 + #86. Each gets a fresh
# Elasticsearch container (scripts/groundtruth-es.sh) and its experiment is deleted after verify.
# Evidence: work/<board>/report.md and work/<board>/results/run-1/groundtruth/.
set -u
cd "$(dirname "$0")/.." || exit 1
for b in gt-stock gt-es8 gt; do
  scripts/groundtruth-es.sh start || exit 2
  ./burger run "$b" || echo "board $b: FAIL (expected for gt-stock and gt-es8)" >&2
  scripts/groundtruth-es.sh count > "work/$b/es-indices.txt" 2>&1 || true
  ./burger down "$b" >/dev/null 2>&1 || true
done
scripts/groundtruth-es.sh stop
