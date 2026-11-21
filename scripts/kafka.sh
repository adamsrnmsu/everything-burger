#!/bin/sh
# A throwaway single-node Kafka (KRaft) for the kafka boards, on the phenix docker host, plus a
# producer that feeds the topics the kafka component items read. The broker is on the host network
# at 127.0.0.1:19092 (the phenix container shares the host network, and the component's listener
# runs there). Usage: kafka.sh start|stop|status|dump <topic>
# The producers (one console producer per topic) send a round of messages every second for 30
# minutes; the listener starts at `latest`, so it only sees
# what is sent after it attached. EXP is the experiment name the board runs under (the component's
# default subscription is <exp>.*). Env: KAFKA_IMAGE (apache/kafka:3.9.1), KAFKA_HEAP (384m).
set -eu
NAME=ebwes-kafka-broker
IMAGE=${KAFKA_IMAGE:-apache/kafka:3.9.1}
EXP=${EXP:-ebwes-kafka}
BS=127.0.0.1:19092
K=/opt/kafka/bin
case "${1:-}" in
start)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --network host -m 1g \
    -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
    -e KAFKA_LISTENERS=PLAINTEXT://127.0.0.1:19092,CONTROLLER://127.0.0.1:19093 \
    -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://127.0.0.1:19092 \
    -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
    -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
    -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@127.0.0.1:19093 \
    -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
    -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
    -e KAFKA_HEAP_OPTS="-Xms${KAFKA_HEAP:-384m} -Xmx${KAFKA_HEAP:-384m}" "$IMAGE" >/dev/null
  up=
  for _ in $(seq 1 60); do
    docker exec "$NAME" $K/kafka-topics.sh --bootstrap-server $BS --list >/dev/null 2>&1 && { up=1; break; }
    sleep 2
  done
  [ -n "$up" ] || { echo "$NAME did not come up" >&2; exit 1; }
  # Topics first, so a listener that attaches before the producer's first round still finds them.
  for t in burger.sensors.a burger.sensors.b "$EXP.default" burger.cols burger.raw; do
    docker exec "$NAME" $K/kafka-topics.sh --bootstrap-server $BS --create --if-not-exists --topic "$t" >/dev/null
  done
  # One console producer per topic, fed a round of messages every second for 30 minutes (the
  # JVMs are capped at 64 MB). burger.raw's first message each round is not JSON (kafka-bad-json);
  # burger.cols alternates two key sets (kafka-csv-columns); the dev rows of burger.sensors.a say
  # state on (kafka-topic-filter).
  docker exec -i "$NAME" sh -c 'cat > /tmp/feed.sh' <<'FEED'
t=$1; i=0
while [ $i -lt 1800 ]; do
  case $t in
  burger.sensors.a) echo "{\"name\":\"bus1\",\"v\":$i}"; echo "{\"name\":\"dev1\",\"v\":$i,\"state\":\"on\"}" ;;
  burger.sensors.b) echo "{\"name\":\"bus2\",\"v\":$i}"; echo "{\"name\":\"dev2\",\"v\":$i}" ;;
  burger.cols) echo "{\"x\":$i,\"y1\":$i}"; echo "{\"x\":$i,\"y2\":$i}" ;;
  burger.raw) echo "not json $i"; echo "{\"name\":\"ok\",\"v\":$i}" ;;
  *) echo "{\"x\":$i}" ;;
  esac
  i=$((i+1)); sleep 1
done
FEED
  for t in burger.sensors.a burger.sensors.b "$EXP.default" burger.cols burger.raw; do
    docker exec -d -e KAFKA_HEAP_OPTS=-Xmx64m "$NAME" sh -c \
      "sh /tmp/feed.sh $t | $K/kafka-console-producer.sh --bootstrap-server $BS --topic $t >/dev/null 2>&1"
  done
  echo "$NAME up" ;;
stop) docker rm -f "$NAME" >/dev/null 2>&1 || true ;;
status) docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}' ;;
dump) docker exec "$NAME" $K/kafka-console-consumer.sh --bootstrap-server $BS --topic "$2" --from-beginning --max-messages "${3:-5}" ;;
*) echo "usage: $0 start|stop|status|dump <topic> [n]" >&2; exit 2 ;;
esac
