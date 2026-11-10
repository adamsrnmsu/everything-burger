import os
import shlex
import shutil
from pathlib import Path

from loguru import logger

from burger.site import build_dockerfile


def bennu_startup(man: dict) -> str:
    """Installs the round's bennu build before sceptre-start.sh runs
    (phenix-start.sh runs /etc/phenix/startup/* in glob order)."""
    lines = [
        "#!/bin/bash",
        "# everything-burger: install the round's bennu PR build",
        "echo '# burger bennu install log' >/var/log/burger-bennu.log",
    ]
    if man["bennu"].get("deb_version"):
        # The arm64 range images carry a source build under /usr/local, which PATH and ld.so find
        # before the deb's /usr: remove it so the PR build is the one that runs.
        lines.append(
            "rm -f /usr/local/bin/bennu-* /usr/local/lib/libbennu-* "
            "&& ldconfig >>/var/log/burger-bennu.log 2>&1"
        )
        lines.append("dpkg -i /root/burger-bennu.deb >>/var/log/burger-bennu.log 2>&1 && ldconfig")
        # The same image copies of libraries the deb ships (libopendnp3.so, with the DNP3 server
        # code) would still win in /usr/local/lib: remove them so the PR's libraries are loaded.
        lines.append(
            "for f in $(dpkg -L bennu | grep '^/usr/lib/lib.*[.]so$'); do "
            "rm -f /usr/local/lib/$(basename $f)*; done; ldconfig"
        )
    if man["bennu"].get("wheel"):
        w = " ".join(f"/root/{n}" for n in [*man["bennu"].get("wheels", []), man["bennu"]["wheel"]])
        # --break-system-packages is unknown to pip < 23: retry without it.
        lines.append(
            f"pip3 install --no-deps --force-reinstall --break-system-packages {w} "
            f">>/var/log/burger-bennu.log 2>&1 || pip3 install --no-deps --force-reinstall {w} "
            ">>/var/log/burger-bennu.log 2>&1"
        )
    return "\n".join(lines) + "\n"


def bennu_injections(man: dict, stage_dir: str) -> list:
    inj = [
        {
            "src": f"{stage_dir}/00-burger-bennu.sh",
            "dst": "/etc/phenix/startup/00-burger-bennu.sh",
            "description": "burger: install bennu PR build",
            "permissions": "0755",
        }
    ]
    if man["bennu"].get("deb_version"):
        inj.append(
            {
                "src": f"{stage_dir}/bennu.deb",
                "dst": "/root/burger-bennu.deb",
                "description": "burger: bennu PR build",
            }
        )
    if man["bennu"].get("wheel"):
        inj.append(
            {
                "src": f"{stage_dir}/{man['bennu']['wheel']}",
                "dst": f"/root/{man['bennu']['wheel']}",
                "description": "burger: pybennu PR build",
            }
        )
        inj += [
            {
                "src": f"{stage_dir}/wheels/{n}",
                "dst": f"/root/{n}",
                "description": "burger: pybennu dependency",
            }
            for n in man["bennu"].get("wheels", [])
        ]
    return inj


def build_bennu(
    host, wt: Path, out: Path, man: dict, install: list[str], pip: list[str], arm64: bool = False
) -> None:
    """Build the round's bennu deb (and wheel) in the bennu test image. On an arm64 route the
    image and every container run are linux/arm64, so the deb is compiled natively."""
    repo = Path(os.environ.get("BURGER_BENNU_REPO", host.s.work / "repos" / "bennu")).resolve()
    platform = "linux/arm64" if arm64 else None
    plat = ["--platform", platform] if platform else []
    image = "burger-bennu-build:22.04" + ("-arm64" if arm64 else "")
    build_dockerfile(host, wt / "docker" / "test", image, platform)
    if "pybennu" in install:
        # Copied first: the build backend may write next to the sources.
        script = (
            f"cp -r {shlex.quote(str(wt.resolve()))}/src/pybennu /tmp/pyb && "
            # bennu's own `make dist`; jammy's pip cannot run the uv_build backend.
            "pip3 install -q uv && uv build -q --wheel -o /tmp/w /tmp/pyb "
            "&& cp /tmp/w/pybennu-*.whl /out/ && "
            f"chown {os.getuid()}:{os.getgid()} /out/pybennu-*.whl"
        )
        host.run(
            [
                "docker",
                "run",
                *plat,
                "--rm",
                "-v",
                f"{wt.resolve()}:{wt.resolve()}:ro",
                "-v",
                f"{out}:/out",
                image,
                "bash",
                "-ec",
                script,
            ]
        )
        man["bennu"]["wheel"] = next(out.glob("pybennu-*.whl")).name
        logger.info(f"pybennu wheel {man['bennu']['wheel']}")
        if pip:  # new deps of the PR that the image lacks; the VMs have no index
            shutil.rmtree(out / "wheels", ignore_errors=True)
            host.run(
                [
                    "docker",
                    "run",
                    *plat,
                    "--rm",
                    "-v",
                    f"{out}:/out",
                    image,
                    "bash",
                    "-ec",
                    f"pip3 wheel -q --no-deps -w /out/wheels {shlex.join(pip)} && "
                    f"chown -R {os.getuid()}:{os.getgid()} /out/wheels",
                ]
            )
            man["bennu"]["wheels"] = sorted(p.name for p in (out / "wheels").glob("*.whl"))
            logger.info(f"extra wheels {', '.join(man['bennu']['wheels'])}")
    if "deb" not in install:
        return
    # The worktree's .git file points into the main repo, and CMake stamps the
    # deb version with `git rev-parse --short HEAD`: mount both at their host paths.
    # CpackConfig.cmake hard-codes `Architecture: amd64` (bennu #78 fixes that) and dpkg on the
    # arm64 RTUs refuses such a deb, so on arm64 relabel it with the build's own architecture.
    # Its Depends lists -dev packages the offline RTU images do not carry (only the runtime libs),
    # so dpkg leaves bennu unconfigured: drop the line too. Drop this once #78 is merged and the
    # RTU images have the -dev packages.
    relabel = (
        " && d=$(mktemp -d) && dpkg-deb -R /out/bennu.deb $d && "
        'sed -i "s/^Architecture:.*/Architecture: $(dpkg --print-architecture)/;/^Depends:/d" '
        "$d/DEBIAN/control "
        "&& dpkg-deb --root-owner-group -b $d /out/bennu.deb >/dev/null"
        if arm64
        else ""
    )
    script = (
        "git config --global --add safe.directory '*' && mkdir -p /tmp/b && cd /tmp/b && "
        f"cmake -DBUILD_TESTS=OFF {shlex.quote(str(wt.resolve()))} >/dev/null && "
        'make -j"$(nproc)" package >/dev/null && cp bennu-*.deb /out/bennu.deb'
        f"{relabel} && chown {os.getuid()}:{os.getgid()} /out/bennu.deb"
    )
    host.run(
        [
            "docker",
            "run",
            *plat,
            "--rm",
            "-v",
            f"{repo}:{repo}:ro",
            "-v",
            f"{wt.resolve()}:{wt.resolve()}:ro",
            "-v",
            f"{out}:/out",
            image,
            "bash",
            "-ec",
            script,
        ]
    )
    version = host.run(
        [
            "docker",
            "run",
            *plat,
            "--rm",
            "-v",
            f"{out}:/out",
            image,
            "dpkg-deb",
            "-f",
            "/out/bennu.deb",
            "Version",
        ],
        capture=True,
    ).stdout.strip()
    man["bennu"]["deb_version"] = version
    logger.info(f"bennu.deb {version}")


def bennu_check(host, wt: Path) -> None:
    """bennu's own docker harness (build, smoke, ctest, pytest, gotest) on the
    build's bennu PR: the coverage for pybennu (#56) and gobennu (#57), which
    waterway never runs."""
    if host.site:  # run.sh builds its image only when the tag is missing: give it ours
        build_dockerfile(host, wt / "docker" / "test", "bennu-verify:22.04-lint")
    host.run(
        [
            "bash",
            wt / "docker" / "test" / "run.sh",
            "-s",
            wt,
            "build",
            "smoke",
            "ctest",
            "pytest",
            "gotest",
        ]
    )
