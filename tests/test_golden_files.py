import json
from pathlib import Path

import pytest

FILES = sorted((Path(__file__).parent / "golden").glob("*.json"))


def test_golden_files_exist():
    assert len(FILES) >= 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_golden_files_load(path):
    assert json.loads(path.read_text())["metadata"]["name"].startswith("ebwes-")
