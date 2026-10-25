"""mm-check: minimega #1684's cc_filtered against a live minimega.

#1684 adds a context manager to pyapigen's template, so its own test only sees
the static part. This generates minimega.py from the PR head, with the CLI of the
minimega that is running (pyapigen reads `minimega -cli`; a stand-in script
hands it the running one's output, so nothing cgo has to build), and drives it
in the board's experiment from the phēnix container, where the apps talk to
minimega.
"""

import os
from pathlib import Path

from loguru import logger

from burger.config import REPOS, BurgerError
from burger.site import site_image
from burger.vcs import _ensure_repo

# ponytail: one PR; take it from the board (like bennu's `pr`) if a second one needs this
PR = 1684
GOLANG = "golang:1.24"
STAGE = "/tmp/burger-mm"

BUILD = (
    "mkdir /tmp/mm && git -c safe.directory='*' -C /repo archive {sha} | tar -x -C /tmp/mm"
    " && cd /tmp/mm && GOTOOLCHAIN=local go build -o /tmp/pyapigen ./cmd/pyapigen"
    " && printf '#!/bin/sh\\ncat /out/cli.json\\n' > /tmp/mmcli && chmod +x /tmp/mmcli"
    " && /tmp/pyapigen -out /out/minimega.py /tmp/mmcli && chown {uid} /out/minimega.py"
)

# Run in the phēnix container: argv is the stage dir, the experiment and a VM.
CHECK = """
import sys
sys.path.insert(0, sys.argv[1])
import minimega
stage, exp, vm = sys.argv[1:]
assert minimega.__file__.startswith(stage), "imported " + minimega.__file__
mm = minimega.connect(namespace=exp)

def current():
    return [row for r in mm.cc_filter() for row in (r.get("Tabular") or [])]

mm.clear_cc_filter()
with mm.cc_filtered("name=" + vm):
    rows = current()
    assert rows and vm in " ".join(rows[0]), "filter not set inside the block: %r" % rows
assert not current(), "filter left after the block: %r" % current()
try:
    with mm.cc_filtered("name=" + vm):
        raise KeyError("burger")
except KeyError:
    pass
assert not current(), "filter left after the block raised: %r" % current()
print("cc_filtered ok in " + exp)
"""


def build_bindings(host, settings) -> Path:
    """minimega.py from the PR head, cached by its sha."""
    repo = _ensure_repo(host, settings, "minimega")
    ref = f"refs/burger/pr/{PR}"
    host.git(repo, "fetch", "--quiet", REPOS["minimega"], f"+refs/pull/{PR}/head:{ref}")
    sha = host.git(repo, "rev-parse", ref, capture=True).stdout.strip()
    out = settings.work / "cache" / "minimega-py" / sha
    if not (out / "minimega.py").is_file():
        out.mkdir(parents=True, exist_ok=True)
        cli = host.run(["docker", "exec", settings.mm_ctr, "minimega", "-cli"], capture=True)
        (out / "cli.json").write_text(cli.stdout)
        uid = f"{os.getuid()}:{os.getgid()}"
        host.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo}:/repo:ro",
                "-v",
                f"{out}:/out",
                site_image(host, GOLANG),
                "bash",
                "-ec",
                BUILD.format(sha=sha, uid=uid),
            ]
        )
        logger.info("generated minimega.py from #{} {} into {}", PR, sha[:7], out)
    return out / "minimega.py"


def mm_check(host, settings, exp: str, vms: list[str]) -> None:
    if not vms:
        raise BurgerError("mm-check needs a route with RTUs (waterway or soap)")
    host.put(build_bindings(host, settings), f"{STAGE}/minimega.py")
    res = host.ctr("python3", "-c", CHECK, STAGE, exp, vms[0], capture=True)
    logger.info(res.stdout.strip())
