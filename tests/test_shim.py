import shutil
import subprocess
from pathlib import Path

SHIM = Path(__file__).resolve().parents[1] / "burger"


def test_shim_without_venv(tmp_path):
    shim = tmp_path / "burger"
    shutil.copy(SHIM, shim)
    r = subprocess.run(["bash", str(shim), "--version"], capture_output=True, text=True)
    assert r.returncode == 1
    assert "pip install -e ." in r.stderr
