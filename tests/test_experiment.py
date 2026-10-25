import dataclasses
import json
import subprocess

import pytest

from burger import experiment
from burger.config import BurgerError
from burger.experiment import down, fix_routers, install, scorch, vm_uuids
from tests.conftest import MAN, res
from tests.fakes import FakeHost


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


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, n):
        self.now += n


def _up(settings, monkeypatch, verdicts):
    """A switchboard that is up with one health run; evaluate() answers `verdicts` in turn."""
    (settings.work / "baseline").mkdir()
    (settings.work / "baseline" / "state.json").write_text(json.dumps({"linux_only": False}))
    (settings.work / "baseline" / "manifest.json").write_text(json.dumps(MAN))
    runs = [{"name": "health", "start": ["health"]}]
    monkeypatch.setattr(experiment, "compose", lambda res, man: ([], runs))
    monkeypatch.setattr(experiment, "collect", lambda *a: {})
    answers = iter(verdicts)
    monkeypatch.setattr(
        experiment,
        "evaluate",
        lambda *a: [("c", "s", "x", "FAIL" if next(answers) else "ok", "no sceptre yet")],
    )


@pytest.fixture
def messages():
    from loguru import logger

    got = []
    hid = logger.add(lambda m: got.append((m.record["level"].name, m.record["message"])), level=0)
    yield got
    logger.remove(hid)


NS = "docker exec minimega minimega -e namespace e "


def test_wait_for_cc_blocks_until_every_vm_registered(settings):
    clock = FakeClock()
    clients = iter(["u1\n", "u1\n", "u1\nu2\n"])

    class H(FakeHost):
        def run(self, cmd, **kw):
            if "columns uuid cc clients" in " ".join(map(str, cmd)):
                return subprocess.CompletedProcess(cmd, 0, next(clients), "")
            return super().run(cmd, **kw)

    listing = subprocess.CompletedProcess([], 0, "a,u1\nb,u2\n", "")
    host = H({NS + ".csv true .headers false .annotate false .columns name": listing}, settings)
    host.api_responses["/experiments/e/vms"] = {
        "vms": [{"name": "a"}, {"name": "b"}, {"name": "skipped", "dnb": True}]
    }
    experiment.wait_for_cc(host, settings, "e", clock=clock)
    assert clock.now == 10  # two polls found b missing, the third saw both


def test_wait_for_cc_gives_up_with_a_warning(settings, messages):
    clock = FakeClock()
    host = FakeHost(settings=settings)
    host.api_responses["/experiments/e/vms"] = {"vms": [{"name": "a"}]}
    experiment.wait_for_cc(host, settings, "e", timeout=20, clock=clock)
    assert clock.now > 20
    assert any(lvl == "WARNING" and "not registered" in m for lvl, m in messages)


def test_wait_for_cc_skipped_without_minimega(settings):
    host = FakeHost(settings=dataclasses.replace(settings, mm_ctr=""))
    experiment.wait_for_cc(host, host.s, "e")
    assert host.calls == []


def test_health_waits_for_cc_before_scorch(settings, monkeypatch):
    _up(settings, monkeypatch, [False])
    order = []
    monkeypatch.setattr(experiment, "wait_for_cc", lambda *a, **k: order.append("cc"))

    class H(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd:
                order.append("scorch")
            return super().run(cmd, **kw)

    scorch(res(settings), settings, H(settings=settings), None, 0, clock=FakeClock())
    assert order == ["cc", "scorch"]


def test_first_attempt_cap_kills_scorch_and_retry_takes_over(settings, monkeypatch):
    _up(settings, monkeypatch, [False])  # the retry after the timeout is healthy
    clock = FakeClock()
    timeouts = []

    class Hung(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd and "experiment" in cmd:
                timeouts.append(kw.get("timeout"))
                if len(timeouts) == 1:
                    clock.now += kw["timeout"]
                    raise subprocess.TimeoutExpired(cmd, kw["timeout"], b"started\n")
            return super().run(cmd, **kw)

    host = Hung(settings=settings)
    scorch(res(settings), settings, host, None, 900, clock=clock, first_cap=120)
    assert timeouts == [120, None]  # only the first attempt is capped
    kills = [c[-3:] for c in host.calls if "pkill" in c]
    assert kills == [
        ["pkill", "-f", "experiment scorch"],
        ["pkill", "-f", "phenix-scorch-component-"],
    ]
    results = settings.work / "baseline" / "results"
    assert (results / "run-0-attempt-1.log").read_text() == "started\n"
    assert (results / "run-0-attempt-2.log").is_file()


def test_retry_lines_and_one_line_final_failure(settings, monkeypatch, messages):
    _up(settings, monkeypatch, [True] * 9)
    clock = FakeClock()

    class Loud(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd and "experiment" in cmd:
                clock.now += 100
                return subprocess.CompletedProcess(cmd, 1, "out-text\n", "err-text\n")
            return super().run(cmd, **kw)

    scorch(res(settings), settings, Loud(settings=settings), None, 400, clock=clock)
    retries = [m for lvl, m in messages if "not healthy yet" in m]
    assert "attempt 1" in retries[0] and "settle 400 s left" in retries[0]
    assert "~2 more attempt(s)" in retries[0]  # 400 // (60 + 100)
    final = [m for lvl, m in messages if lvl == "WARNING" and "failed:" in m]
    # one line on the final failure, pointing at the attempt log; the output is not dumped
    assert len(final) == 1 and "run-0-attempt-" in final[0] and "out-text" not in final[0]
    assert not any("out-text" in m for _, m in messages)
    log = settings.work / "baseline" / "results" / "run-0-attempt-1.log"
    assert log.read_text() == "out-text\nerr-text\n"


def test_passes_log_at_success_level(settings, monkeypatch, messages):
    _up(settings, monkeypatch, [False])
    scorch(res(settings), settings, FakeHost(settings=settings), None, 0, clock=FakeClock())
    assert [m.split(" ")[0] for lvl, m in messages if lvl == "SUCCESS"] == ["run", "scorch"]


def test_build_iperf_builds_once_and_caches(settings):
    from burger.iperf import build_iperf

    out = settings.work / "out"
    out.mkdir()
    host = FakeHost(settings=settings)
    real = host.run

    def run(cmd, **kw):
        if cmd[:2] == ["docker", "run"]:
            (settings.work / "cache" / "iperf3-3.22").write_text("ELF")
        return real(cmd, **kw)

    host.run = run
    build_iperf(host, settings, "3.22", out, "1:2")
    build_iperf(host, settings, "3.22", out, "1:2")
    runs = [c for c in host.calls if c[:2] == ["docker", "run"]]
    assert len(runs) == 1
    assert "--enable-static-bin" in runs[0][-1]
    assert "esnet/iperf/archive/refs/tags/3.22.tar.gz" in runs[0][-1]
    assert (out / "iperf3-3.22").read_text() == "ELF"
    assert (settings.work / "cache" / "iperf3-3.22").stat().st_mode & 0o111
