import json
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from loguru import logger

from burger.config import BurgerError, Settings


class Host:
    """Everything that touches the machine: commands, the phēnix container, the REST API."""

    def __init__(self, settings: Settings):
        self.s = settings

    def run(self, cmd, check=True, capture=False, **kw):
        cmd = [str(c) for c in cmd]
        logger.debug("+ {}", shlex.join(cmd))
        try:
            res = subprocess.run(cmd, check=False, text=True, capture_output=capture, **kw)
        except FileNotFoundError:
            if check:
                raise BurgerError(f"{cmd[0]} not found on PATH") from None
            return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} not found")
        if check and res.returncode != 0:
            detail = f"\n{res.stdout}{res.stderr}" if capture else ""
            raise BurgerError(f"command failed ({res.returncode}): {shlex.join(cmd)}{detail}")
        return res

    def git(self, repo, *a, **kw):
        return self.run(["git", "-C", repo, *a], **kw)

    def ctr(self, *a, **kw):
        """Run a command where phēnix runs: inside its container, or on the host."""
        return self.run((["docker", "exec", self.s.ctr] if self.s.ctr else []) + list(a), **kw)

    def phenix(self, *a, **kw):
        return self.ctr("phenix", *a, **kw)

    def mm(self, exp, *a):
        """One minimega command in the experiment's namespace, via the container."""
        return self.run(
            ["docker", "exec", self.s.mm_ctr, "minimega", "-e", " ".join(["namespace", exp, *a])],
            check=False,
            capture=True,
        )

    def put(self, src, dst):
        """Copy a host file or directory's contents to dst where phēnix runs."""
        src = Path(src)
        parent = dst if src.is_dir() else str(Path(dst).parent)
        self.ctr("mkdir", "-p", parent)
        if self.s.ctr:
            self.run(["docker", "cp", f"{src}/." if src.is_dir() else src, f"{self.s.ctr}:{dst}"])
        elif src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    def fetch(self, src, dst) -> bool:
        shutil.rmtree(dst, ignore_errors=True)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        if self.s.ctr:
            res = self.run(["docker", "cp", f"{self.s.ctr}:{src}", dst], check=False)
            return res.returncode == 0
        if not Path(src).exists():
            return False
        shutil.copytree(src, dst)
        return True

    def api(self, path):
        req = urllib.request.Request(self.s.api + path)
        if self.s.token:
            req.add_header("X-Phenix-Auth-Token", f"Bearer {self.s.token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 500):  # how phēnix answers a missing name varies
                return None
            raise
