"""Regenerate the golden renders from the old root `burger` script.

    BURGER_TOPOLOGIES=/path/to/sceptre-phenix-topologies python tests/golden/make_golden.py
"""

import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader("burger", str(HERE.parents[1] / "burger"))
burger = importlib.util.module_from_spec(importlib.util.spec_from_loader("burger", loader))
loader.exec_module(burger)

TOPOLOGIES = Path(os.environ["BURGER_TOPOLOGIES"])
MAN = {"apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
       "bennu": {"pr": 58, "sha": "c" * 40, "deb_version": "6.0.1.abc1234"}, "topologies": "d" * 40}

os.environ["BURGER_VM_PASSWORD"] = "lab"
os.environ["BURGER_RTDS_TOPOLOGY"] = str(HERE / "site-topology.yaml")
os.environ["BURGER_RTDS_SCENARIO"] = str(HERE / "site-scenario.yaml")
os.environ["BURGER_RTDS_PROVIDER"] = "pp1"


def dump(path, doc):
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


for name in burger.all_rounds():
    rnd = burger.load_round(name)
    man = copy.deepcopy(MAN)
    if b := rnd.get("bennu"):
        man["bennu"]["pr"] = b["pr"]
        if "pybennu" in b.get("install", ["deb"]):
            man["bennu"]["wheel"] = "pybennu-6.0.0-py3-none-any.whl"
        if b.get("pip"):
            man["bennu"]["wheels"] = ["loguru-0.7.3-py3-none-any.whl"]
    else:
        man["bennu"] = None
    waterway = burger.topology(rnd) == "waterway" and not rnd.get("base")
    for linux_only in (False, True) if waterway else (False,):
        where = None if rnd.get("base") else TOPOLOGIES / burger.topology(rnd)
        topo, scen = burger.render(rnd, copy.deepcopy(man), where, linux_only, "/phenix/burger/x")
        tag = f"{name}.linux" if linux_only else name
        dump(HERE / f"{tag}.topology.json", topo)
        dump(HERE / f"{tag}.scenario.json", scen)
