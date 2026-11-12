#!/usr/bin/env bash
# Run the groundtruth boards in order: stock, #85 alone, #84 + #85 + #86, then all six PRs. Each
# board starts its own Elasticsearch container on `up` and removes it on `down` (its `services`
# line, scripts/groundtruth-es.sh). A failing board stays up to debug: `burger down <board>`.
# Evidence: work/<board>/report.md and work/<board>/results/run-1/groundtruth/.
set -u
cd "$(dirname "$0")/.." || exit 1
for b in gt-stock gt-es8 gt-84-86 gt; do
  ./burger run "$b" || echo "board $b: FAIL (expected for gt-stock and gt-es8)" >&2
  ./burger down "$b" >/dev/null 2>&1 || true
done
