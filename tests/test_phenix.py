import argparse
import dataclasses
import hashlib
import json
import subprocess

import pytest
import yaml

from burger import cli, experiment, phenix, update
from burger.config import BurgerError
from burger.switchboard import load_switchboard, resolve
from burger.vcs import sync
from tests.conftest import MAN, res
from tests.fakes import FakeHost, StrictHost
from tests.test_minimega import PY_AT, board_built
from tests.test_minimega import labels as mm_labels

SHA = "e" * 40
MM_IMAGE = "burger/minimega:cccccccccccc-0123abcd"
TAG = f"burger/phenix:eeeeeeeeeeee-{hashlib.sha256(MM_IMAGE.encode()).hexdigest()[:12]}"
INSPECT = "docker inspect -f {{json .Config.Labels}} phenix"
BASE = "docker compose -p stack --project-directory /srv/phenix -f /srv/phenix/a.yml"
UP = "up -d --no-deps --force-recreate phenix"
DOCKERFILE = (
    "ARG MM=latest\n"
    "FROM ghcr.io/sandia-minimega/minimega:${MM} AS minimega\n"
    "RUN a\n"
    "FROM golang:1.24 AS gobuilder\n"
    "ARG PHENIX_COMMIT\n"
)


def done(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


def labels(settings, sha=None, image=None, work=None):
    """The phenix container's labels: stock, or swapped by burger to `sha`."""
    files = "/srv/phenix/a.yml"
    if sha:
        files += f",{work or settings.work}/{update.OVERRIDES[1]}"
    return done(
        json.dumps(
            {
                "com.docker.compose.project.working_dir": "/srv/phenix",
                "com.docker.compose.project.config_files": files,
                "com.docker.compose.service": "phenix",
                "com.docker.compose.project": "stack",
                **({phenix.LABEL: sha, phenix.IMAGE_LABEL: image or TAG} if sha else {}),
            }
        )
    )


class PxHost(FakeHost):
    """A FakeHost whose phenix labels follow the compose `up` it is given, as docker's do."""

    sticky = False

    def run(self, cmd, check=True, capture=False, **kw):
        got = super().run(cmd, check, capture, **kw)
        line = " ".join(str(c) for c in cmd)
        if line.startswith("docker compose") and line.endswith(UP) and not self.sticky:
            override = next((c for c in cmd if str(c).endswith(update.OVERRIDES[1])), None)
            svc = yaml.safe_load(open(override))["services"]["phenix"] if override else None
            self.responses[INSPECT] = labels(
                self.s, svc["labels"][phenix.LABEL] if svc else None, svc and svc["image"]
            )
        return got


def px_host(settings, sha=None, running=()):
    host = PxHost(
        {
            INSPECT: labels(settings, sha),
            "docker inspect -f {{json .Config.Labels}} minimega": mm_labels(settings),
            "docker exec phenix python3 -c import minimega; print(minimega.__file__)": done(PY_AT),
        },
        settings,
    )
    host.api_responses["/experiments"] = {
        "experiments": [{"name": n, "running": True} for n in running]
    }
    return host


def joined(host):
    return [" ".join(c) for c in host.calls]


def test_image_tag_names_the_phenix_and_the_minimega_it_carries():
    assert phenix.image_tag(SHA, MM_IMAGE) == TAG
    assert phenix.image_tag(SHA, None) == "burger/phenix:eeeeeeeeeeee-stock"
    assert phenix.image_tag(SHA, "burger/minimega:other") != TAG


# --- build ---


def wt_with(tmp_path):
    wt = tmp_path / "phenix"
    (wt / "docker").mkdir(parents=True)
    (wt / "docker" / "Dockerfile").write_text(DOCKERFILE)
    return wt


def test_build_image_seds_then_points_the_first_from_at_the_boards_minimega(settings, tmp_path):
    wt = wt_with(tmp_path)
    sedded = DOCKERFILE.replace("ghcr.io/sandia-minimega/minimega:${MM}", "minimega:arm64")
    host = FakeHost({"docker image inspect": done(rc=1), "sed -f /r/p.sed": done(sedded)}, settings)
    assert phenix.build_image(host, wt, SHA, MM_IMAGE, "/r/p.sed") == TAG
    assert host.calls[0] == ["docker", "image", "inspect", TAG]
    assert ["sed", "-f", "/r/p.sed", str(wt / "docker" / "Dockerfile")] in host.calls
    text = (wt / "docker" / "Dockerfile.burger").read_text().splitlines()
    assert text[1] == f"FROM {MM_IMAGE} AS minimega"  # first FROM only, stage name kept
    assert "FROM golang:1.24 AS gobuilder" in text
    assert host.calls[-1] == [
        "docker", "build", "--build-arg", f"PHENIX_COMMIT={SHA}",
        "-f", str(wt / "docker" / "Dockerfile.burger"), "-t", TAG, str(wt),
    ]  # fmt: skip


def test_build_image_without_a_sed_or_a_minimega_builds_the_dockerfile_as_written(
    settings, tmp_path
):
    wt = wt_with(tmp_path)
    host = FakeHost({"docker image inspect": done(rc=1)}, settings)
    tag = phenix.build_image(host, wt, SHA, None, None)
    assert tag == "burger/phenix:eeeeeeeeeeee-stock"
    assert (wt / "docker" / "Dockerfile.burger").read_text() == DOCKERFILE
    assert not any(c[0] == "sed" for c in host.calls)


def test_build_image_reuses_an_existing_tag(settings, tmp_path):
    host = FakeHost(settings=settings)  # every tag "exists"
    assert phenix.build_image(host, wt_with(tmp_path), SHA, MM_IMAGE, None) == TAG
    assert host.calls == [["docker", "image", "inspect", TAG]]


# --- swap and restore ---


def test_ensure_recreates_phenix_with_its_own_override_and_labels(settings):
    host = px_host(settings)
    phenix.ensure(host, settings, SHA, TAG)
    override = update.burger_override(settings, "phenix")
    assert override == settings.work / "phenix" / "compose.override.yml"
    assert override != update.burger_override(settings)  # minimega's file is not overwritten
    assert yaml.safe_load(override.read_text()) == {
        "services": {
            "phenix": {
                "image": TAG,
                "pull_policy": "never",
                "labels": {phenix.LABEL: SHA, phenix.IMAGE_LABEL: TAG},
            }
        }
    }
    calls = joined(host)
    assert calls.index(f"{BASE} -f {override} {UP}") < calls.index(
        "docker exec phenix phenix mm version"
    )
    assert phenix.running_sha(host, settings) == SHA


def test_ensure_with_the_running_image_does_nothing(settings):
    host = px_host(settings, SHA, running=["ebwes-x"])
    phenix.ensure(host, settings, SHA, TAG)
    assert not any("compose" in c for c in joined(host))


def test_ensure_none_puts_stock_back_after_a_killed_run(settings):
    host = px_host(settings, SHA)
    phenix.ensure(host, settings, None)
    assert f"{BASE} {UP}" in joined(host)
    assert not any("compose.override.yml" in c for c in joined(host))


def test_ensure_none_on_stock_does_nothing(settings):
    host = px_host(settings)
    phenix.ensure(host, settings, None)
    assert joined(host) == [INSPECT]


def test_ensure_refuses_while_an_experiment_runs(settings):
    host = px_host(settings, running=["ebwes-other"])
    with pytest.raises(BurgerError, match="experiments running: ebwes-other"):
        phenix.ensure(host, settings, SHA, TAG)
    assert not any("compose" in c for c in joined(host))


def test_restore_puts_stock_back_unless_vms_still_run(settings):
    host = px_host(settings, SHA)
    phenix.restore(host, settings)
    assert f"{BASE} {UP}" in joined(host)
    host = px_host(settings, SHA, running=["ebwes-other"])
    phenix.restore(host, settings)
    assert not any("compose" in c for c in joined(host))
    host = px_host(settings)
    phenix.restore(host, settings)
    assert joined(host) == [INSPECT]


def test_restore_fails_when_the_override_comes_back(settings):
    host = px_host(settings, SHA)
    host.sticky = True
    with pytest.raises(BurgerError, match="still runs burger's build after the restore"):
        phenix.restore(host, settings)


def test_no_phenix_container_setting(settings):
    s = dataclasses.replace(settings, ctr="")
    host = FakeHost(settings=s)
    with pytest.raises(BurgerError, match="BURGER_PHENIX_CONTAINER"):
        phenix.ensure(host, s, SHA, TAG)
    phenix.ensure(host, s, None)
    phenix.restore(host, s)
    assert host.calls == []


def test_compose_labels_drop_either_burger_override(settings):
    for service in ("minimega", "phenix"):
        both = f"/srv/phenix/a.yml,{update.burger_override(settings, service)}"
        host = FakeHost(
            {INSPECT: done(json.dumps({**json.loads(labels(settings).stdout), **{
                "com.docker.compose.project.config_files": both}}))},
            settings,
        )  # fmt: skip
        assert update.compose_labels(host, "phenix")["files"] == ["/srv/phenix/a.yml"]


# --- verify row ---


def test_rows_fail_when_the_running_sha_differs(settings):
    man = {"phenix": {"pr": 576, "sha": SHA, "image": TAG, "from": "board"}}
    (row,) = phenix.rows(px_host(settings, SHA), settings, man, "b")
    assert (row.check, row.verdict) == ("phenix container", "ok")
    (row,) = phenix.rows(px_host(settings, "f" * 40), settings, man, "b")
    assert row.verdict == "FAIL" and "burger install b" in row.reason
    (row,) = phenix.rows(px_host(settings), settings, man, "b")  # stock running
    assert row.verdict == "FAIL"
    (row,) = phenix.rows(px_host(settings, SHA), settings, {"phenix": None}, "b")  # leftover swap
    assert row.verdict == "FAIL"
    (row,) = phenix.rows(px_host(settings), settings, {"phenix": None}, "b")
    assert row.verdict == "ok"
    s = dataclasses.replace(settings, ctr="")
    (row,) = phenix.rows(FakeHost(settings=s), s, man, "b")
    assert row.verdict == "skipped"


# --- pipeline ---

PX = {"pr": 576, "sha": SHA, "from": "board"}


def test_build_records_the_phenix_image_in_the_key(settings, monkeypatch):
    rd = settings.work / "baseline"
    rd.mkdir(parents=True)
    mm = {"pr": 1, "sha": "c" * 40, "from": "flag"}
    man = {**MAN, "bennu": None, "minimega": mm, "phenix": PX}
    (rd / "manifest.json").write_text(json.dumps(man))
    monkeypatch.setattr(experiment, "build_iperf", lambda *a: None)
    monkeypatch.setattr(experiment.minimega, "build_image", lambda *a: MM_IMAGE)
    built = []
    monkeypatch.setattr(experiment.phenix, "build_image", lambda *a: built.append(a) or TAG)
    experiment.build(res(settings), settings, FakeHost(settings=settings))
    assert built == [(built[0][0], rd / "phenix", SHA, MM_IMAGE, None)]  # baseline's route: no sed
    assert json.loads((rd / "manifest.json").read_text())["phenix"]["image"] == TAG
    assert json.loads((rd / "out" / "built.json").read_text())["phenix"] == TAG


def test_a_board_without_phenix_keeps_its_built_key():
    assert "phenix" not in experiment._built_key(MAN)


def built_with_phenix(settings):
    board_built(settings, {"pr": 1782, "sha": "c" * 40, "from": "board", "image": MM_IMAGE})
    rd = settings.work / "baseline"
    man = {**json.loads((rd / "manifest.json").read_text()), "phenix": {**PX, "image": TAG}}
    (rd / "manifest.json").write_text(json.dumps(man))
    (rd / "out" / "built.json").write_text(json.dumps(experiment._built_key(man)))


def test_install_swaps_phenix_first_then_the_apps_then_minimega(settings):
    built_with_phenix(settings)
    host = px_host(settings)
    experiment.install(res(settings), settings, host)
    calls = joined(host)
    phenix_up = calls.index(f"{BASE} -f {update.burger_override(settings, 'phenix')} {UP}")
    pip = next(i for i, c in enumerate(calls) if "pip install" in c)
    minimega_up = next(
        i for i, c in enumerate(calls) if c.endswith("up -d --no-deps --force-recreate minimega")
    )
    assert phenix_up < pip < minimega_up


def test_install_without_phenix_restores_stock_before_anything_else(settings):
    board_built(settings, None)
    host = px_host(settings, SHA)
    experiment.install(res(settings), settings, host)
    calls = joined(host)
    assert calls.index(f"{BASE} {UP}") < next(i for i, c in enumerate(calls) if "apps-python" in c)


def test_up_ensures_phenix_before_minimega(settings, monkeypatch):
    built_with_phenix(settings)
    monkeypatch.setattr(experiment, "render", lambda *a: ({}, {}))
    monkeypatch.setattr(experiment, "wait_running", lambda *a: None)
    monkeypatch.setattr(experiment, "fix_routers", lambda *a: None)
    host = px_host(settings)
    experiment.up(res(settings), settings, host, 1)
    calls = joined(host)
    phenix_up = calls.index(f"{BASE} -f {update.burger_override(settings, 'phenix')} {UP}")
    assert phenix_up < next(
        i for i, c in enumerate(calls) if c.endswith("--force-recreate minimega")
    )


def test_down_restores_stock_phenix(settings):
    host = px_host(settings, SHA)
    cli._one(argparse.Namespace(cmd="down"), settings, host, "baseline")
    calls = joined(host)
    assert calls.index(f"{BASE} {UP}") > next(
        i for i, c in enumerate(calls) if "config delete" in c
    )


def test_verify_adds_the_phenix_row(settings, monkeypatch):
    from burger import verify as v
    from tests.test_verify import setup

    r, host = setup(settings, monkeypatch)
    rows = v.verify(r, settings, host)
    assert [x.check for x in rows].count("phenix container") == 1


def test_sync_phenix_pr_fetches_only_the_pr_head(settings, monkeypatch):
    monkeypatch.delenv("BURGER_PHENIX_REPO", raising=False)
    host = FakeHost(settings=settings)
    b = dataclasses.replace(load_switchboard("baseline", settings), phenix={"pr": 576})
    man = sync(resolve(b, settings), settings, host)
    fetch = next(c for c in host.calls if "fetch" in c and "sceptre-phenix.git" in " ".join(c))
    assert fetch[-1] == "+refs/pull/576/head:refs/burger/pr/576"
    assert man["phenix"]["pr"] == 576 and man["phenix"]["from"] == "board"
    assert str(settings.work / "baseline" / "phenix") in [a for c in host.calls for a in c]
    man = sync(res(settings), settings, FakeHost(settings=settings))
    assert man["phenix"] is None


def test_build_image_reads_the_sed_output_it_captured(settings, tmp_path):
    wt = wt_with(tmp_path)
    sedded = DOCKERFILE.replace("RUN a", "RUN sedded")
    host = StrictHost({"docker image inspect": done(rc=1), "sed -f": done(sedded)}, settings)
    phenix.build_image(host, wt, SHA, None, "/r/p.sed")
    assert (wt / "docker" / "Dockerfile.burger").read_text() == sedded
