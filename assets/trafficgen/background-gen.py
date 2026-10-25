#!/usr/bin/env python3
"""Benign background traffic generator for the trafficgen SCORCH component.

Sends ordinary-looking HTTP requests to the target for --duration seconds at
roughly --rate requests/second; --probability is the chance to actually fire on
each tick (so a rate with gaps looks less metronomic). Stdlib only.

Invoked by the component as:
  python3 /background-gen.py --ip <target> --rate <r> --duration <d> --probability <p>
"""

import argparse
import random
import time
import urllib.request

# ordinary browser-ish user agents and paths — nothing an IDS should flag
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "curl/7.88.1",
]
PATHS = ["/", "/index.html", "/status", "/api/health", "/assets/app.js", "/favicon.ico"]


def _get(ip: str, timeout: float = 2.0) -> None:
    path = random.choice(PATHS)
    req = urllib.request.Request(
        f"http://{ip}{path}", headers={"User-Agent": random.choice(USER_AGENTS)}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1024)
    except Exception:
        # the target may be momentarily busy; background traffic is best-effort
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="benign background traffic")
    ap.add_argument("--ip", required=True)
    ap.add_argument("--rate", type=float, default=10000.0, help="requests/second")
    ap.add_argument("--duration", type=float, default=30.0, help="seconds")
    ap.add_argument("--probability", type=float, default=0.01, help="fire chance per tick")
    args = ap.parse_args()

    interval = 1.0 / args.rate if args.rate > 0 else 0.1
    deadline = time.monotonic() + args.duration
    sent = 0
    while time.monotonic() < deadline:
        if random.random() < args.probability:
            _get(args.ip)
            sent += 1
        time.sleep(interval)
    print(f"background-gen: sent {sent} requests to {args.ip}", flush=True)


if __name__ == "__main__":
    main()
