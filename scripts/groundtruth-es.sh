#!/bin/sh
# A throwaway single-node Elasticsearch for the groundtruth boards, on the phenix docker host.
# The VMs reach it through mgmt_tap's host address (172.16.0.1) on port 19200, which is
# published on every host address. Usage: groundtruth-es.sh start|stop|status|count
# The disk watermarks are off: the Lima disk is over 90% full and Elasticsearch would otherwise
# leave the new index unassigned (red). Env: ES_IMAGE (default docker.elastic.co/elasticsearch/elasticsearch:8.19.3), ES_HEAP (512m).
set -eu
NAME=ebwes-gt-es
IMAGE=${ES_IMAGE:-docker.elastic.co/elasticsearch/elasticsearch:8.19.3}
case "${1:-}" in
start)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p 19200:9200 -m 1536m \
    -e discovery.type=single-node -e xpack.security.enabled=false \
    -e cluster.routing.allocation.disk.threshold_enabled=false \
    -e ES_JAVA_OPTS="-Xms${ES_HEAP:-512m} -Xmx${ES_HEAP:-512m}" "$IMAGE" >/dev/null
  for _ in $(seq 1 90); do
    docker exec "$NAME" curl -fs http://127.0.0.1:9200/_cluster/health >/dev/null 2>&1 && { echo "$NAME up"; exit 0; }
    sleep 2
  done
  echo "$NAME did not come up" >&2; exit 1 ;;
stop) docker rm -f "$NAME" >/dev/null 2>&1 || true ;;
status) docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}' ;;
count) docker exec "$NAME" curl -fs "http://127.0.0.1:9200/_cat/indices/bennu-*?v" ;;
*) echo "usage: $0 start|stop|status|count" >&2; exit 2 ;;
esac
