"""mm-bindings: minimega's python bindings (#1684 cc_filtered, #1686 context manager and
close) against the live minimega, from the phēnix container where the apps use them.

On a board with its own minimega, `minimega.rows` already proves phēnix imports that
container's minimega.py, so this drives whatever phēnix imports."""

import json

from burger.checks import Row
from burger.config import BurgerError

# Run in the phēnix container: argv is the experiment and a VM. Prints [[name, error], ...].
SCRIPT = """
import copy, json, pickle, sys
import minimega
exp, vm = sys.argv[1:]
out = []

def check(name, fn):
    try:
        fn()
        out.append([name, ""])
    except BaseException as e:
        out.append([name, "%s: %s" % (type(e).__name__, e)])

def current(mm):
    return [row for r in mm.cc_filter() for row in (r.get("Tabular") or [])]

if not hasattr(type(minimega.connect(namespace=exp)), "__enter__"):
    out.append(["context manager", "minimega.py predates #1686"])
    print(json.dumps(out))
    sys.exit(0)

def with_block():
    with minimega.connect(namespace=exp) as mm:
        assert vm in str(mm.vm_info()), "vm_info does not list " + vm
    closed.append(mm)

def after_block():
    try:
        closed[0].vm_info()
    except Exception:
        return
    raise AssertionError("a call after the block still worked")

def close_twice():
    mm = minimega.connect(namespace=exp)
    mm.close()
    mm.close()

def copies():
    with minimega.connect(namespace=exp) as mm:
        copy.copy(mm)

def no_pickle():
    with minimega.connect(namespace=exp) as mm:
        try:
            pickle.dumps(mm)
        except Exception:
            return
    raise AssertionError("pickle.dumps(client) worked")

def filtered():
    with minimega.connect(namespace=exp) as mm:
        mm.clear_cc_filter()
        with mm.cc_filtered("name=" + vm):
            rows = current(mm)
            assert rows and vm in " ".join(rows[0]), "filter not set inside the block: %r" % rows
        assert not current(mm), "filter left after the block: %r" % current(mm)

def filtered_raises():
    with minimega.connect(namespace=exp) as mm:
        try:
            with mm.cc_filtered("name=" + vm):
                raise KeyError("burger")
        except KeyError:
            pass
        assert not current(mm), "filter left after the block raised: %r" % current(mm)

closed = []
for fn in (with_block, after_block, close_twice, copies, no_pickle, filtered, filtered_raises):
    check(fn.__name__.replace("_", " "), fn)
print(json.dumps(out))
"""


def run(host, s, res, man) -> list[Row]:
    vms = res.route.values.get("rtus", [])
    if not vms:
        return [Row("mm-bindings", "probe", "skipped", "the route has no RTUs", [])]
    got = host.ctr("python3", "-c", SCRIPT, res.board.exp, vms[0], check=False, capture=True)
    if got.returncode:
        raise BurgerError((got.stderr or got.stdout or f"exit {got.returncode}").strip()[-300:])
    return [
        Row("mm-bindings", name, "FAIL" if err else "ok", err, [])
        for name, err in json.loads(got.stdout.strip().splitlines()[-1])
    ]
