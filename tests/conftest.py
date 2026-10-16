import pytest

from burger.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env(tmp_path)
