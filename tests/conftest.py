import dataclasses
import os
from pathlib import Path

import pytest

from burger.config import Settings

MAN = {
    "apps": {"head": "a" * 40, "base": "b" * 40, "prs": {}},
    "bennu": {"pr": 58, "sha": "c" * 40, "deb_version": "6.0.1.abc1234"},
    "topologies": "d" * 40,
}


@pytest.fixture
def settings(tmp_path):
    root = Path(__file__).resolve().parents[1]
    return dataclasses.replace(Settings.from_env(root), work=tmp_path)


@pytest.fixture
def topologies():
    path = os.environ.get("BURGER_TOPOLOGIES")
    if not path:
        pytest.skip("BURGER_TOPOLOGIES is not set")
    return Path(path)
