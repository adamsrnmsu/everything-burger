#!/usr/bin/env python3
"""Traffic target for the trafficgen SCORCH component.

A tiny stdlib-only listener the background and malware generators aim at. It
serves HTTP on :80 and echoes on a TCP port so there is something for benign
and malicious traffic to hit, and it logs every request so an IDS run (and a
human) can see what arrived. Runs until killed; the component starts it in the
background and `pkill`s it on stop.

Range-only: it serves nothing sensitive and runs no code it is sent.
"""

import argparse
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT = 80
ECHO_PORT = 9000


def _log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    # quiet the default stderr logging; we log our own one-liners
    def log_message(self, *_a):
        pass

    def _serve(self):
        ua = self.headers.get("User-Agent", "-")
        _log(f"HTTP {self.command} {self.path} ua={ua!r} from {self.client_address[0]}")
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # the generators use GET/POST/HEAD; map them all to the same sink
    do_GET = do_POST = do_HEAD = _serve


def _echo_server(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(64)
    while True:
        try:
            conn, addr = s.accept()
        except OSError:
            break
        threading.Thread(target=_echo_conn, args=(conn, addr), daemon=True).start()


def _echo_conn(conn: socket.socket, addr) -> None:
    with conn:
        conn.settimeout(5)
        try:
            data = conn.recv(4096)
            if data:
                _log(f"TCP {len(data)}B from {addr[0]}")
                conn.sendall(data)
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="trafficgen traffic server")
    ap.add_argument("--http-port", type=int, default=HTTP_PORT)
    ap.add_argument("--echo-port", type=int, default=ECHO_PORT)
    args = ap.parse_args()

    threading.Thread(target=_echo_server, args=(args.echo_port,), daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", args.http_port), Handler)
    _log(f"serving HTTP on :{args.http_port}, echo on :{args.echo_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
