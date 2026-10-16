import dataclasses
import json
import subprocess

import pytest

from burger import experiment
from burger.config import BurgerError
from burger.experiment import down, fix_routers, install, scorch, vm_uuids
from burger.switchboard import load_switchboard, resolve
from tests.conftest import MAN
from tests.fakes import FakeHost


def res(settings, name="baseline"):
    return resolve(load_switchboard(name, settings), settings)


def sent(host):
    """The minimega commands, without the docker exec prefix and namespace."""
    return [" ".join(c).split(" namespace e ", 1)[1] for c in host.calls if "minimega" in c]


def test_fix_routers(settings, monkeypatch):
    iface = lambda addr: {"address": addr, "mask": 24} if addr else {"name": "mgmt"}  # noqa: E731
    topo = {
        "spec": {
            "nodes": [
                {
                    "general": {"hostname": "r1"},
                    "hardware": {"drives": [{"image": "minirouter.qc2"}]},
                    "network": {"interfaces": [iface("10.1.2.1"), iface(None), iface("10.1.1.1")]},
                },
                {"general": {"hostname": "r2"}, "type": "Router", "network": {"interfaces": []}},
                {
                    "general": {"hostname": "rtu-1"},
                    "hardware": {"os_type": "linux", "drives": [{"image": "bennu.qc2"}]},
                    "network": {"interfaces": [iface("10.1.1.101")]},
                },
            ]
        }
    }
    listing = subprocess.CompletedProcess([], 0, "r1,u1\nrtu-1,u9\n", "")
    host = FakeHost(settings=settings)
    monkeypatch.setattr(experiment, "vm_uuids", lambda h, e, want: {"r1": "u1", "rtu-1": "u9"})
    fix_routers(host, settings, "e", topo)
    cmds = sent(host)
    execs = [c for c in cmds if c.startswith("cc exec")]
    assert cmds[0] == "cc filter uuid=u1" and cmds[-1] == "clear cc filter"
    assert (
        len(execs) == 2
    )  # eth0 and eth2; eth1 has no address, r2 is not listed, rtu-1 is no router
    assert execs[0].startswith(
        'cc exec /bin/sh -c "ip link set eth0 up; ip addr add 10.1.2.1/24 dev eth0'
    )
    assert "${" not in execs[0] and "replace" not in execs[0]
    assert "eth2" in execs[1] and "10.1.1.1/24" in execs[1]

    host = FakeHost({"docker exec minimega minimega -e namespace e .csv": listing}, settings)
    monkeypatch.undo()
    assert vm_uuids(host, "e", {"r1"}) == {"r1": "u1", "rtu-1": "u9"}
    assert sent(host) == [".csv true .headers false .annotate false .columns name,uuid vm info"]

    monkeypatch.setattr(experiment, "vm_uuids", lambda *a: pytest.fail("no mm_ctr, no lookups"))
    host = FakeHost(settings=dataclasses.replace(settings, mm_ctr=""))
    fix_routers(host, host.s, "e", topo)
    assert host.calls == []


class ScorchHost(FakeHost):
    """Every scorch attempt costs 300 s on the fake clock, and starts at its current reading."""

    def __init__(self, settings, clock, attempts):
        super().__init__(settings=settings)
        self.clock, self.attempts = clock, attempts

    def run(self, cmd, **kw):
        if "scorch" in cmd:
            self.attempts.append(self.clock["now"])
            self.clock["now"] += 300  # an unregistered VM costs a full cc client grace
        return super().run(cmd, **kw)


def test_scorch_settle_is_a_hard_cap(settings, monkeypatch):
    clock = {"now": 0.0}
    attempts: list[float] = []

    class FakeTime:
        time = staticmethod(lambda: clock["now"])

        @staticmethod
        def sleep(n):
            clock["now"] += n

    r = res(settings)
    (settings.work / "baseline").mkdir()
    (settings.work / "baseline" / "state.json").write_text(json.dumps({"linux_only": False}))
    (settings.work / "baseline" / "manifest.json").write_text(json.dumps(MAN))
    runs = [{"name": "health", "start": ["health"]}]
    monkeypatch.setattr(experiment, "compose", lambda res, man: ([], runs))  # never healthy
    host = ScorchHost(settings, clock, attempts)

    scorch(r, settings, host, None, 1000, clock=FakeTime)
    # the clock starts when the first attempt ends (300): deadline 1300; a
    # fourth attempt at 1080 would end at 1380, so three fit
    assert attempts == [0, 360, 720]
    assert clock["now"] <= 1300

    attempts.clear()
    clock["now"] = 0.0
    scorch(r, settings, host, None, 0, clock=FakeTime)
    assert attempts == [0]  # the first attempt always runs


def test_scorch_needs_up(settings):
    with pytest.raises(BurgerError, match="burger up baseline"):
        scorch(res(settings), settings, FakeHost(), None, 0)


def test_install_refuses_stale_build(settings):
    rd = settings.work / "baseline"
    (rd / "out").mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps(MAN))
    (rd / "out" / "built.json").write_text(json.dumps({"apps": "x", "bennu": None}))
    host = FakeHost(settings=settings)
    with pytest.raises(BurgerError, match="burger build baseline"):
        install(res(settings), settings, host)
    assert host.calls == []


def test_down_stops_then_deletes(settings):
    host = FakeHost(settings=settings)
    host.api_responses["/experiments/ebwes-baseline"] = {"running": True}
    down(res(settings), host)
    verbs = [" ".join(c).split("phenix phenix ")[1].split()[:2] for c in host.calls]
    assert verbs == [["experiment", "stop"], ["experiment", "delete"], ["config", "delete"]]
