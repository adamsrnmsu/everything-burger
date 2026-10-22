"""A static iperf3 for boards that need a newer one than the jammy debs carry.

Built once in a (site-layered) ubuntu:22.04 container and cached under the work
dir; every build copies it into the board's out/ and the render injects it.
"""

import shutil

from loguru import logger

from burger.site import site_image

UBUNTU = "ubuntu:22.04"  # bennu.qc2's release; the binary is static anyway
URL = "https://github.com/esnet/iperf/archive/refs/tags/{v}.tar.gz"
SCRIPT = (
    "apt-get update -qq && apt-get install -y -qq build-essential curl ca-certificates && "
    "curl -fsSL {url} | tar xz -C /tmp && cd /tmp/iperf-{v} && "
    "./configure --enable-static-bin --without-openssl --without-sctp && "
    'make -j"$(nproc)" && strip src/iperf3 && cp src/iperf3 /out/iperf3-{v} && '
    "chown {uid} /out/iperf3-{v}"
)


def build_iperf(host, settings, version: str, out, uid: str) -> None:
    """Copy iperf3-<version> into out, building (through the site proxy/CA) on a cache miss."""
    cached = settings.work / "cache" / f"iperf3-{version}"
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        image = site_image(host, UBUNTU)
        script = SCRIPT.format(url=URL.format(v=version), v=version, uid=uid)
        host.run(
            ["docker", "run", "--rm", "-v", f"{cached.parent}:/out", image, "bash", "-ec", script]
        )
        cached.chmod(0o755)
        logger.info("built static iperf3 {} into {}", version, cached)
    shutil.copy(cached, out / f"iperf3-{version}")
