"""Every burger module imports on its own, whatever was imported before it."""

import pkgutil
import subprocess
import sys

import pytest

import burger

MODULES = sorted(
    m.name
    for m in pkgutil.walk_packages(burger.__path__, "burger.")
    if not m.name.endswith("__main__")
)


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_alone(module):
    got = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr.strip().splitlines()[-1]
