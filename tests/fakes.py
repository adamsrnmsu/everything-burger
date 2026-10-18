import subprocess

from burger.config import Settings
from burger.shell import Host


class FakeHost(Host):
    """Records commands; answers by the first response key that prefixes the joined command.

    Everything above run/api (ctr, phenix, mm, put, fetch) is the real Host code,
    so tests see the commands it would issue.
    """

    def __init__(
        self,
        responses: dict[str, subprocess.CompletedProcess] | None = None,
        settings: Settings | None = None,
        site=None,
    ):
        super().__init__(settings or Settings.from_env(), site)
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.api_responses: dict[str, dict | None] = {}

    def run(self, cmd, check=True, capture=False, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        line = " ".join(cmd)
        for key, res in self.responses.items():
            if line.startswith(key):
                return res
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def api(self, path):
        return self.api_responses.get(path)
