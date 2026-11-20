"""Rewrite tests/golden/*.{topology,scenario}.json from the current render.

Usage: make goldens [BOARDS="main harden"]

Uses the same man_for/render path as tests/test_render.py, so a regenerated
golden is what the test compares. Needs BURGER_TOPOLOGIES (make sets it).
"""

import copy
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from burger.render import render  # noqa: E402
from tests.conftest import GOLDEN, ROOT_SETTINGS, res  # noqa: E402
from tests.test_render import CASES, STAGE, man_for  # noqa: E402

SITE_ENV = {
    "BURGER_VM_PASSWORD": "lab",
    "BURGER_SITE_TOPOLOGY": str(GOLDEN / "site-topology.yaml"),
    "BURGER_SITE_SCENARIO": str(GOLDEN / "site-scenario.yaml"),
    "BURGER_SITE_PROVIDER": "pp1",
    "BURGER_ARM64_RANGE": str(GOLDEN / "arm64-range"),
}


def main(boards):
    topologies = os.environ.get("BURGER_TOPOLOGIES")
    if not topologies:
        sys.exit("BURGER_TOPOLOGIES is not set")
    os.environ.update(SITE_ENV)
    settings = dataclasses.replace(ROOT_SETTINGS, work=Path(tempfile.mkdtemp()))
    for name, linux_only in CASES:
        if boards and name not in boards:
            continue
        r = res(settings, name, linux_only=linux_only)
        where = None if r.route.base else Path(topologies) / r.route.topology_dir
        if where is not None and not where.is_dir():
            print(f"skip {name}: {r.route.topology_dir}/ is not in BURGER_TOPOLOGIES")
            continue
        topo, scen = render(r, copy.deepcopy(man_for(r.board)), where, STAGE, settings)
        tag = f"{name}.linux" if linux_only else name
        for kind, doc in (("topology", topo), ("scenario", scen)):
            text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
            (GOLDEN / f"{tag}.{kind}.json").write_text(text)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
