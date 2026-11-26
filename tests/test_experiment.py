import dataclasses
import json
import subprocess

import pytest

from burger import experiment
from burger.config import BurgerError
from burger.experiment import down, fix_routers, install, scorch, services, vm_uuids
from tests.conftest import MAN, res
from tests.fakes import FakeHost, StrictHost

NOT_FOUND = subprocess.CompletedProcess([], 1, "", "")


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
    verbs = [
        " ".join(c).split("phenix phenix ")[1].split()[:2] for c in host.calls if "phenix" in c
    ]
    assert verbs == [["experiment", "stop"], ["experiment", "delete"], ["config", "delete"]]


class StuckMinimega(FakeHost):
    """minimega whose `clear namespace` fails `fails` times (scheduler still running)
    and whose VMs survive until then; `kill_works` False makes a manual kill useless."""

    def __init__(self, settings, fails, kill_works=True):
        super().__init__(settings=settings)
        self.vms, self.fails, self.kill_works = ["RUNNING", "BUILDING"], fails, kill_works

    def run(self, cmd, **kw):
        super().run(cmd, **kw)
        line = " ".join(cmd)
        out, rc = "", 0
        if line.endswith("columns namespace namespace"):
            out = "minimega\nebwes-baseline\n"
        elif line.endswith("columns state vm info"):
            out = "\n".join(self.vms)
        elif line.endswith("clear namespace ebwes-baseline"):
            self.fails -= 1
            rc = int(self.fails >= 0)
            self.vms = self.vms if rc else []
        elif line.endswith("vm kill all") and self.kill_works:
            self.fails = 0
        return subprocess.CompletedProcess(cmd, rc, out, "")


def test_down_clears_vms_phenix_left_in_minimega(settings, monkeypatch):
    monkeypatch.setattr(experiment.time, "sleep", lambda s: None)
    host = StuckMinimega(settings, fails=2)
    host.api_responses["/experiments/ebwes-baseline"] = {"running": True}
    down(res(settings), host)
    assert host.vms == []
    assert not any("kill all" in " ".join(c) for c in host.calls)  # the retries were enough


def test_down_kills_by_hand_when_clear_namespace_keeps_failing(settings, monkeypatch):
    monkeypatch.setattr(experiment.time, "sleep", lambda s: None)
    host = StuckMinimega(settings, fails=99)
    down(res(settings), host)  # phenix no longer knows the experiment: still checked
    assert host.vms == []
    assert any("vm flush" in " ".join(c) for c in host.calls)


def test_down_fails_loudly_when_vms_cannot_be_removed(settings, monkeypatch):
    monkeypatch.setattr(experiment.time, "sleep", lambda s: None)
    host = StuckMinimega(settings, fails=99, kill_works=False)
    with pytest.raises(BurgerError, match="still holds VMs"):
        down(res(settings), host)


def test_down_goes_on_to_delete_when_phenix_stop_fails(settings):
    host = FakeHost(
        {"docker exec phenix phenix experiment stop": subprocess.CompletedProcess([], 1, "", "")},
        settings=settings,
    )
    host.api_responses["/experiments/ebwes-baseline"] = {"running": True}
    down(res(settings), host)
    assert any(c[-3:-1] == ["experiment", "delete"] for c in host.calls)


def test_down_leaves_services_to_the_down_command(settings):
    # up calls down() first to clear an old experiment; that must not stop the services
    r = res(settings)
    r.board.services = ["groundtruth-es"]
    host = FakeHost(settings=settings)
    down(r, host)
    assert not any("groundtruth-es.sh" in " ".join(c) for c in host.calls)
    services(r, settings, host, "stop")
    script = str(settings.root / "scripts" / "groundtruth-es.sh")
    assert ["env", f"EXP={r.board.exp}", "sh", script, "stop"] in host.calls


def test_a_service_is_told_the_experiment_it_serves(settings):
    # kafka.sh names a topic after the experiment (the component's default
    # subscription is <exp>.*), so a board other than `kafka` needs its own name
    r = res(settings)
    r.board.services = ["kafka"]
    host = FakeHost(settings=settings)
    services(r, settings, host, "start")
    ((env, exp, *_),) = [c for c in host.calls if c[-1] == "start"]
    assert (env, exp) == ("env", f"EXP={r.board.exp}")


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


@pytest.mark.parametrize("cache_dir_exists", [False, True])
def test_build_iperf_creates_the_cache_dir_whatever_exists(settings, tmp_path, cache_dir_exists):
    from burger.iperf import build_iperf

    work = tmp_path / ("w1" if cache_dir_exists else "w2") / "work"
    cache = work / "cache"
    if cache_dir_exists:
        cache.mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()
    host = FakeHost(settings=dataclasses.replace(settings, work=work))
    real = host.run

    def run(cmd, **kw):
        if cmd[:2] == ["docker", "run"]:
            (cache / "iperf3-3.22").write_text("ELF")
        return real(cmd, **kw)

    host.run = run
    build_iperf(host, host.s, "3.22", out, "1:2")
    assert (out / "iperf3-3.22").read_text() == "ELF"


def test_build_marks_minimega_sha(settings, monkeypatch):
    rd = settings.work / "baseline"
    rd.mkdir(parents=True)
    man = {**MAN, "bennu": None, "minimega": {"pr": 1782, "sha": "c" * 40, "from": "flag"}}
    (rd / "manifest.json").write_text(json.dumps(man))
    monkeypatch.setattr(experiment, "build_iperf", lambda *a: None)
    host = FakeHost({"docker image inspect burger/minimega": NOT_FOUND}, settings)
    experiment.build(res(settings), settings, host)
    built = [c for c in host.calls if c[:2] == ["docker", "build"] and "-f" in c]
    assert built == [
        ["docker", "build", "-f", str(rd / "minimega" / "docker" / "Dockerfile"),
         "-t", "burger/minimega:cccccccccccc", str(rd / "minimega")]
    ]  # fmt: skip
    # the image, not only the commit: a plain and a layered build differ (burger-fr8)
    assert json.loads((rd / "out" / "built.json").read_text())["minimega"] == (
        "burger/minimega:cccccccccccc"
    )
    assert json.loads((rd / "manifest.json").read_text())["minimega"]["image"] == (
        "burger/minimega:cccccccccccc"
    )
    man["minimega"]["sha"] = "d" * 40  # a new sync moved minimega: the build is stale
    (rd / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(BurgerError, match="burger build baseline"):
        install(res(settings), settings, host)


def test_built_key_without_minimega_is_unchanged():
    # boards built before minimega support keep their built.json
    assert experiment._built_key(MAN) == {"apps": MAN["apps"]["head"], "bennu": MAN["bennu"]["sha"]}


def test_up_clears_the_stage_dir_before_putting_files(settings, monkeypatch, tmp_path, messages):
    r = res(settings)
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(experiment, "board_dir", lambda s, n: tmp_path)
    monkeypatch.setattr(experiment, "load_manifest", lambda s, n: {})
    monkeypatch.setattr(experiment, "_require_built", lambda *a: out)
    monkeypatch.setattr(experiment.minimega, "ensure", lambda *a: None)
    monkeypatch.setattr(experiment, "render", lambda *a: ({}, {}))
    monkeypatch.setattr(experiment, "down", lambda *a: None)
    monkeypatch.setattr(experiment, "services", lambda *a: None)
    monkeypatch.setattr(experiment, "wait_running", lambda *a: None)
    monkeypatch.setattr(experiment, "fix_routers", lambda *a: None)
    host = FakeHost(settings=settings)
    host.api_responses["/experiments"] = {"experiments": [{"name": "other", "running": True}]}
    experiment.up(r, settings, host, 1)
    assert any("other (0 VMs, 0 vCPUs)" in m for lvl, m in messages if lvl == "WARNING")
    stage = f"{settings.phenix_dir}/burger/{r.board.name}"
    lines = [" ".join(c) for c in host.calls]
    rm = next(i for i, c in enumerate(lines) if c.endswith(f"rm -rf {stage}"))
    put = next(i for i, c in enumerate(lines) if c.endswith(f"mkdir -p {stage}"))
    assert rm < put


def _with(r, route=None, **item):
    """The resolved board with route fields and every item's fields replaced."""
    items = [dataclasses.replace(i, **item) for i in r.items]
    return dataclasses.replace(r, items=items, route=dataclasses.replace(r.route, **(route or {})))


@pytest.mark.parametrize("arm64, name", [(False, "goart"), (True, "goart-arm64")])
def test_goart_binary_is_staged_by_route_arch(settings, tmp_path, arm64, name):
    root = tmp_path / "root"
    (root / "assets" / "art").mkdir(parents=True)
    (root / "assets" / "art" / name).write_text("bin")
    out = tmp_path / "out"
    out.mkdir()
    r = _with(res(settings), route={"image_map": {"a.qc2": "b.qc2"} if arm64 else {}})
    experiment._stage_goart(r, dataclasses.replace(settings, root=root), out)
    assert (out / "goart").read_text() == "bin"


def test_goart_binary_missing_says_where_to_put_it(settings, tmp_path):
    r = _with(res(settings), route={"image_map": {"a.qc2": "b.qc2"}})
    with pytest.raises(BurgerError, match="assets/art/goart-arm64"):
        experiment._stage_goart(r, dataclasses.replace(settings, root=tmp_path), tmp_path)


def test_item_assets_are_copied_flat_into_out_assets(settings, tmp_path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "gen.py").write_text("g")
    out = tmp_path / "out"
    out.mkdir()
    r = _with(res(settings), assets=("a/b/gen.py",))
    experiment._stage_assets(r, dataclasses.replace(settings, root=root), out)
    assert (out / "assets" / "gen.py").read_text() == "g"


def test_missing_item_asset_is_an_error(settings, tmp_path):
    r = _with(res(settings), assets=("nope/x.bin",))
    with pytest.raises(BurgerError, match=r"item asset not found: nope/x\.bin"):
        experiment._stage_assets(r, dataclasses.replace(settings, root=tmp_path), tmp_path)


def test_no_assets_makes_no_assets_dir(settings, tmp_path):
    experiment._stage_assets(res(settings), settings, tmp_path)
    assert not (tmp_path / "assets").exists()


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def _pre(settings, key):
    return f"docker exec {settings.ctr} phenix {key}"


def test_kill_scorch_tolerates_nothing_to_kill(settings):
    host = StrictHost({"docker exec": _cp(1)}, settings)  # pkill exits 1 when nothing matched
    experiment._kill_scorch(host)
    assert len([c for c in host.calls if "pkill" in c]) == 2


def test_scorch_attempt_keeps_output_of_a_failed_run(settings):
    host = StrictHost({_pre(settings, "experiment scorch"): _cp(1, "so\n", "se\n")}, settings)
    assert experiment._scorch_attempt(host, "e", 0, 600, False) == ("so\nse\n", "se\n", False)


def test_down_config_delete_is_quiet_and_may_fail(settings):
    seen = []

    class H(FakeHost):
        def run(self, cmd, check=True, capture=False, **kw):
            seen.append((cmd[-4:], check, capture))
            return super().run(cmd, check, capture, **kw)

    down(res(settings), H(settings=settings))
    assert [x for x in seen if "config" in x[0]][-1] == (
        ["config", "delete", "scenario/ebwes-baseline", "topology/ebwes-baseline"][-4:],
        False,
        True,
    )


def test_download_debs_skips_without_packages(settings, tmp_path):
    host = FakeHost(settings=settings)
    experiment._download_debs(host, tmp_path, "1:2", [])
    assert host.calls == [] and not (tmp_path / "debs").exists()


def test_download_debs_runs_apt_in_the_image(settings, tmp_path):
    host = FakeHost(settings=settings)
    experiment._download_debs(host, tmp_path, "1:2", ["curl", "jq"])
    (call,) = [c for c in host.calls if c[:2] == ["docker", "run"]]
    assert "apt-get download -q curl jq" in call[-1] and "chown 1:2" in call[-1]
    assert (tmp_path / "debs").is_dir()


def test_stage_startup_writes_the_debs_script(settings, tmp_path):
    r = _with(res(settings))
    r = dataclasses.replace(r, requires=dataclasses.replace(r.requires, debs_packages=["jq"]))
    experiment._stage_startup(r, dataclasses.replace(settings, mm_ctr=""), FakeHost(), {}, tmp_path)
    assert (tmp_path / "02-burger-debs.sh").read_text() == experiment.DEBS_STARTUP


def test_stage_startup_copies_miniccc_or_says_why_not(settings, tmp_path):
    r = res(settings)
    experiment._stage_startup(r, settings, FakeHost(settings=settings), {}, tmp_path)
    assert (tmp_path / "01-burger-miniccc.sh").read_text() == experiment.MINICCC_STARTUP
    host = StrictHost({"docker cp": _cp(1)}, settings)
    with pytest.raises(BurgerError, match="cannot copy miniccc"):
        experiment._stage_startup(r, settings, host, {}, tmp_path)


def test_stage_startup_stages_protonuke_or_says_why_not(settings, tmp_path):
    r = res(settings)
    r = dataclasses.replace(r, requires=dataclasses.replace(r.requires, protonuke_nodes=["rtu-1"]))
    experiment._stage_startup(r, settings, FakeHost(settings=settings), {}, tmp_path)
    assert (tmp_path / "03-burger-protonuke.sh").read_text() == experiment.PROTONUKE_STARTUP
    host = FakeHost({f"docker cp {settings.mm_ctr}:/opt/minimega/bin/protonuke": _cp(1)}, settings)
    with pytest.raises(BurgerError, match=r"requires\.protonuke needs protonuke"):
        experiment._stage_startup(r, settings, host, {}, tmp_path)


def test_stage_assets_into_an_existing_assets_dir(settings, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "g.py").write_text("g")
    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    experiment._stage_assets(
        _with(res(settings), assets=("g.py",)), dataclasses.replace(settings, root=root), out
    )
    assert (out / "assets" / "g.py").read_text() == "g"


def test_attempt_log_creates_missing_dirs_and_reuses_them(settings):
    r = res(settings)
    log = experiment._write_attempt_log(r, settings, 0, 1, "one")
    experiment._write_attempt_log(r, settings, 0, 2, "two")  # results/ already exists
    assert log == settings.work / "baseline" / "results" / "run-0-attempt-1.log"
    assert log.read_text() == "one"


def test_build_twice_and_passes_the_bennu_spec(settings, monkeypatch):
    rd = settings.work / "baseline"
    rd.mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps(MAN))
    got = []
    monkeypatch.setattr(experiment, "build_iperf", lambda *a: None)
    monkeypatch.setattr(experiment, "build_bennu", lambda *a: got.append(a))
    r = res(settings)
    spec = {"pr": 58, "install": ["pip"], "pip": ["x"], "pip_env": {"A": "1"}}
    r = dataclasses.replace(r, board=dataclasses.replace(r.board, bennu=spec))
    experiment.build(r, settings, FakeHost(settings=settings))
    experiment.build(r, settings, FakeHost(settings=settings))  # out/ exists now
    assert got[0][2] == rd / "out" and got[0][4:6] == (["pip"], ["x"]) and got[0][7] == {"A": "1"}
    r = dataclasses.replace(r, board=dataclasses.replace(r.board, bennu=None))
    experiment.build(r, settings, FakeHost(settings=settings))  # no spec: defaults
    assert got[-1][4:6] == (["deb"], []) and got[-1][7] == {}


def test_collect_returns_status_only_when_fetched(settings, monkeypatch):
    monkeypatch.setattr(experiment, "collect_status", lambda dst, since: {"k": since})
    ok = FakeHost(settings=settings)
    assert experiment.collect(res(settings), settings, ok, 0, "t") == {"k": "t"}
    bad = FakeHost({"docker cp": _cp(1)}, settings)
    assert experiment.collect(res(settings), settings, bad, 0, "t") == {}


@pytest.mark.parametrize(
    "node, expect",
    [
        ({"type": "Router"}, True),
        ({"hardware": {"os_type": "minirouter"}}, True),
        ({"hardware": {"drives": [{"image": "minirouter.qc2"}]}}, True),
        ({"type": "VM", "hardware": {"os_type": "linux", "drives": [{"image": "a.qc2"}]}}, False),
        ({}, False),
    ],
)
def test_is_router_any_one_marker_is_enough(node, expect):
    assert experiment.is_router(node) is expect


class ListingHost(FakeHost):
    """minimega's `vm info` answers the next listing in turn (the last one repeats)."""

    def __init__(self, settings, listings):
        super().__init__(settings=settings)
        self.listings, self.looks = listings, 0

    def run(self, cmd, **kw):
        if "columns name,uuid" in " ".join(map(str, cmd)):
            out = self.listings[min(self.looks, len(self.listings) - 1)]
            self.looks += 1
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return super().run(cmd, **kw)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(experiment, "time", c)
    return c


def test_vm_uuids_retries_until_listed(settings, clock):
    host = ListingHost(settings, ["", "a,u1\n"])
    assert vm_uuids(host, "e", {"a"}) == {"a": "u1"}
    assert (host.looks, clock.now) == (2, 5)


def test_vm_uuids_returns_at_once_when_all_listed(settings, clock):
    host = ListingHost(settings, ["a,u1\nb,u2\n"])
    assert vm_uuids(host, "e", {"a"}, timeout=0) == {"a": "u1", "b": "u2"}
    assert (host.looks, clock.now) == (1, 0)


def test_vm_uuids_gives_up_only_after_the_timeout(settings, clock):
    host = ListingHost(settings, [""])
    assert vm_uuids(host, "e", {"a"}, timeout=0) == {}
    assert (host.looks, clock.now) == (2, 5)  # at the deadline exactly: one more look


def test_wait_for_cc_looks_once_per_pass_and_counts(settings, clock, messages):
    host = ListingHost(settings, ["a,u1\n"])
    host.api_responses["/experiments/e/vms"] = {"vms": [{"name": "a"}, {"name": "b"}]}
    experiment.wait_for_cc(host, settings, "e", timeout=0, clock=clock)
    assert host.looks == 2  # vm_uuids does not retry inside a pass
    assert any("(0 of 2): a, b" in m for _, m in messages)


def test_wait_running_waits_then_passes(settings, clock, monkeypatch):
    bad = iter(["no VMs listed", ""])
    monkeypatch.setattr(experiment, "not_running", lambda h, e: next(bad))
    experiment.wait_running(FakeHost(settings=settings), "e", 60)
    assert clock.now == 10


def test_wait_running_times_out(settings, clock, monkeypatch):
    monkeypatch.setattr(experiment, "not_running", lambda h, e: "no VMs listed")
    with pytest.raises(BurgerError, match="e not running after 25s: no VMs listed"):
        experiment.wait_running(FakeHost(settings=settings), "e", 25)
    assert clock.now == 30


def test_scorch_refuses_a_run_out_of_range(settings, monkeypatch):
    _up(settings, monkeypatch, [False, False])
    host = FakeHost(settings=settings)
    for bad in (-1, 1):
        with pytest.raises(BurgerError, match="runs 0-0"):
            scorch(res(settings), settings, host, bad, 0, clock=FakeClock())
    scorch(res(settings), settings, host, 0, 0, clock=FakeClock())  # the only run


def test_scorch_gives_up_on_a_run_that_stays_busy(settings, monkeypatch):
    _up(settings, monkeypatch, [False])
    clock = FakeClock()
    tries = []

    class Busy(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd and "experiment" in cmd:
                tries.append(1)
                return _cp(1, "", "already running")
            return super().run(cmd, **kw)

    with pytest.raises(BurgerError, match="still busy after 10 min"):
        scorch(res(settings), settings, Busy(settings=settings), None, 0, clock=clock)
    assert len(tries) == 21 and clock.now == 20 * 30


def test_scorch_retries_when_another_attempt_just_fits(settings, monkeypatch, messages):
    _up(settings, monkeypatch, [True, True, True])
    clock = FakeClock()
    starts = []

    class Slow(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd and "experiment" in cmd:
                starts.append(clock.now)
                clock.now += 100
            return super().run(cmd, **kw)

    # deadline = 100 + 160; at 100 an attempt (60 + 100) ends exactly on it: it still runs
    scorch(res(settings), settings, Slow(settings=settings), None, 160, clock=clock)
    assert starts == [0, 160]
    running = [m for _, m in messages if "running SCORCH" in m]
    assert running == [
        "run 0 (health) attempt 1: running SCORCH",
        "run 0 (health) attempt 2: running SCORCH",
    ]
    assert any("settle 160 s left, ~1 more" in m for _, m in messages)


def test_non_health_run_gets_one_shot(settings, monkeypatch):
    _up(settings, monkeypatch, [True, True])
    monkeypatch.setattr(experiment, "compose", lambda r, m: ([], [{"name": "other"}]))
    clock, n = FakeClock(), []

    class H(FakeHost):
        def run(self, cmd, **kw):
            if "scorch" in cmd and "experiment" in cmd:
                n.append(1)
            return super().run(cmd, **kw)

    scorch(res(settings), settings, H(settings=settings), None, 1000, clock=clock)
    assert len(n) == 1


def test_wait_running_deadline_is_inclusive(settings, clock, monkeypatch):
    bad = iter(["no VMs listed", ""])
    monkeypatch.setattr(experiment, "not_running", lambda h, e: next(bad))
    experiment.wait_running(FakeHost(settings=settings), "e", 0)  # still not past the deadline
    assert clock.now == 10


def _api_host(settings, info, vms):
    host = FakeHost(settings=settings)
    host.api_responses = {"/experiments/e": info, "/experiments/e/vms": vms}
    return host


@pytest.mark.parametrize(
    "info, vms, expect",
    [
        (None, None, "experiment not found"),
        ({"running": False}, None, "experiment not running"),
        ({"running": True}, None, "no VMs listed"),
        ({"running": True}, {"vms": []}, "no VMs listed"),
        (
            {"running": True},
            {
                "vms": [
                    {"name": "a", "state": "RUNNING"},
                    {"name": "b", "state": "BUILDING"},
                    {"name": "c", "state": "stopped", "dnb": True},
                    {"name": "d"},
                ]
            },
            "b=BUILDING, d=None",
        ),
        ({"running": True}, {"vms": [{"name": "a", "state": "running"}]}, ""),
    ],
)
def test_not_running_names_what_is_not_up(settings, info, vms, expect):
    assert experiment.not_running(_api_host(settings, info, vms), "e") == expect


def test_download_debs_mounts_its_own_debs_dir(settings, tmp_path):
    host = FakeHost(settings=settings)
    experiment._download_debs(host, tmp_path, "1:2", ["curl"])
    (call,) = [c for c in host.calls if c[:2] == ["docker", "run"]]
    assert call[call.index("-v") + 1] == f"{tmp_path / 'debs'}:/out"
    assert call[call.index("-w") + 1] == "/out"


def test_build_mounts_the_go_source_and_out(settings, monkeypatch):
    rd = settings.work / "baseline"
    rd.mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps({**MAN, "bennu": None}))
    monkeypatch.setattr(experiment, "build_iperf", lambda *a: None)
    host = FakeHost(settings=settings)
    experiment.build(res(settings), settings, host)
    (call,) = [c for c in host.calls if c[:2] == ["docker", "run"] and "go" in c]
    mounts = [call[i + 1] for i, a in enumerate(call) if a == "-v"]
    assert mounts == [f"{rd / 'apps' / 'src' / 'go'}:/src", f"{rd / 'out'}:/out"]


def _vm(cpus, dnb=False):
    return {"name": "v", "cpus": cpus, "dnb": dnb}


def test_warn_other_experiments_names_them_with_vm_and_vcpu_counts(settings, messages):
    # another experiment's VMs slow every guest boot on a shared rig (burger-ic4)
    host = FakeHost(settings=settings)
    host.api_responses["/experiments"] = {
        "experiments": [
            {"name": "ebwes-x", "running": True, "vms": [_vm(2)]},  # this board's own
            {"name": "other", "running": True, "vms": [_vm(2), _vm(4), _vm(8, dnb=True)]},
            {"name": "stopped", "running": False, "vms": [_vm(2)]},
        ]
    }
    experiment.warn_other_experiments(host, "ebwes-x")
    warned = [m for lvl, m in messages if lvl == "WARNING"]
    assert len(warned) == 1
    assert "other (2 VMs, 6 vCPUs)" in warned[0]
    assert "ebwes-x" not in warned[0] and "stopped" not in warned[0]


def test_warn_other_experiments_is_quiet_when_none_runs(settings, messages):
    host = FakeHost(settings=settings)
    host.api_responses["/experiments"] = {"experiments": [{"name": "ebwes-x", "running": True}]}
    experiment.warn_other_experiments(host, "ebwes-x")
    assert not [m for lvl, m in messages if lvl == "WARNING"]
