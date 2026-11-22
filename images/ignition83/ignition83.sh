#!/bin/sh
# Chroot build step: Ignition 8.3 gateway at /opt/ignition (tree via the overlay, stage.sh). Trial mode (no licence),
# `ignition` systemd unit, as the phenix ignition app expects (data tree /opt/ignition/data, `systemctl start ignition`).
set -eu
IG=/opt/ignition
tar -C /opt -xf /opt/ignition-tree.tar; rm -f /opt/ignition-tree.tar
test -x $IG/ignition-gateway
# docker image leftovers: wrapper log symlinked to /dev/stdout
rm -f $IG/logs/wrapper.log; mkdir -p $IG/logs $IG/data/local $IG/temp
# The official container's docker-entrypoint.sh stages data/gateway.xml (copy of gateway.xml_clean, absent from the
# extracted tree) before launch; ExecStartPre below does the same. Commissioning itself is the gateway's own (the
# entrypoint never reads these vars): they accept the EULA, make the admin user and the connections. Accepting IA's EULA is the operator's decision: only with IGNITION_ACCEPT_EULA=1 at stage time.
if [ -e /opt/ignition-eula-accepted ]; then
rm -f /opt/ignition-eula-accepted
mkdir -p /etc/systemd/system/ignition.service.d
cat > /etc/systemd/system/ignition.service.d/commission.conf <<'UNIT'
[Service]
Environment=ACCEPT_IGNITION_EULA=Y
Environment=GATEWAY_ADMIN_USERNAME=admin
Environment=GATEWAY_ADMIN_PASSWORD=password
Environment=IGNITION_EDITION=standard
Environment=GATEWAY_HTTP_PORT=8088
Environment=GATEWAY_HTTPS_PORT=8043
Environment=GATEWAY_GAN_PORT=8060
UNIT
fi
sed -i 's/^wrapper.java.initmemory=.*/wrapper.java.initmemory=512/;s/^wrapper.java.maxmemory=.*/wrapper.java.maxmemory=2048/' $IG/data/ignition.conf
# The phenix ignition app expects a COMMISSIONED gateway: it injects resources into data/config/resources/core and a
# first start finds that directory non-empty and faults ("Unable to create 'core' resource collection"). So commission
# here, once: start with the commissioning vars (when the operator accepted the EULA), wait for RUNNING, stop.
if [ -e /etc/systemd/system/ignition.service.d/commission.conf ]; then
  cp -n $IG/data/gateway.xml_clean $IG/data/gateway.xml
  # shellcheck disable=SC2046  # one KEY=value word per line, values have no spaces
  (cd $IG && env $(sed -n 's/^Environment=//p' /etc/systemd/system/ignition.service.d/commission.conf) ./ignition.sh start)
  ok=0
  for _ in $(seq 120); do curl -s -m 3 http://localhost:8088/StatusPing | grep -q '"state":"RUNNING"' && ok=1 && break; sleep 5; done
  (cd $IG && ./ignition.sh stop) || true
  test $ok = 1 || { tail -50 $IG/logs/wrapper.log >&2; echo "gateway did not commission" >&2; exit 1; }
  test -d $IG/data/config/resources/core
  rm -f $IG/logs/* $IG/Ignition-Gateway.pid
fi
cat > /etc/systemd/system/ignition.service <<'UNIT'
[Unit]
Description=Ignition Gateway 8.3 (trial mode)
After=network.target

[Service]
Type=forking
WorkingDirectory=/opt/ignition
ExecStartPre=/bin/sh -c 'test -f data/gateway.xml || cp data/gateway.xml_clean data/gateway.xml'
ExecStart=/opt/ignition/ignition.sh start
ExecStop=/opt/ignition/ignition.sh stop
PIDFile=/opt/ignition/Ignition-Gateway.pid
TimeoutStartSec=600
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/ignition.service /etc/systemd/system/multi-user.target.wants/ignition.service
