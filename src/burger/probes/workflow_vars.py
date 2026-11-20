"""workflow-vars: phenix PR 576's `${NAME}` placeholders in config files, driven through
`phenix workflow render` and `phenix config create` in the phēnix container on a temp
directory that is removed afterwards. Nothing here touches the experiment."""

import os
import time

from burger.checks import Row
from burger.config import BurgerError

ITEM = "workflow-vars"

# Run in the phēnix container with $1 = temp dir, $2 = config name. `needs/` holds a
# placeholder with no value and no default; `dotenv/` is `plain/` plus a .env.
SETUP = r"""
set -e
for d in plain dotenv needs; do mkdir -p "$1/$d/phenix-configs"; done
for d in plain dotenv; do cat > "$1/$d/phenix-configs/topology.yml" <<'EOF'
apiVersion: phenix.sandia.gov/v1
kind: Topology
metadata:
  name: burger-wfvars
spec:
  a: ${BV_A:none}
  b: ${BV_B:none}
  dflt: ${BV_DEF:fallback}
  esc: $${BV_ESC}
EOF
done
printf 'BV_B=dotenv\n' > "$1/dotenv/.env"
H='apiVersion: phenix.sandia.gov/v1\nkind: Topology\n'
printf "${H}x: \${BV_NEED}\n" > "$1/needs/phenix-configs/x.yml"
printf 'BV_A=from-file\nBV_B=file-b\n' > "$1/x.env"
printf "${H}metadata:\n  name: $2\nspec:\n  cfg: \${BV_CFG}\n" > "$1/cfg.yml"
"""


def run(host, s, res, man) -> list[Row]:
    if not man.get("phenix"):
        return [Row(ITEM, "probe", "skipped", "needs a phēnix with PR 576", [])]
    name = f"burger-wfvars-{os.getpid()}-{int(time.time())}"
    tmp = f"/tmp/{name}"
    rows: list[Row] = []

    def render(d, *args, shell=()):
        env = ["env", *shell] if shell else []
        return host.ctr(
            *env, "phenix", "workflow", "render", f"{tmp}/{d}", *args, check=False, capture=True
        )

    def row(check, ok, got):
        why = f"rc={got.returncode}: {(got.stdout or got.stderr).strip()[-200:]}"
        rows.append(Row(ITEM, check, "ok" if ok else "FAIL", "" if ok else why, []))

    def has(check, line, got):
        # render prints each file as written, so the value sits on an indented line
        lines = [x.strip() for x in got.stdout.splitlines()]
        row(check, got.returncode == 0 and line in lines, got)

    env = f"{tmp}/x.env"
    try:
        setup = host.ctr("sh", "-c", SETUP, "sh", tmp, name, check=False, capture=True)
        if setup.returncode:
            raise BurgerError(
                f"cannot write the test files: {(setup.stderr or setup.stdout).strip()}"
            )
        has("-e substitutes", "a: from-e", render("plain", "-e", "BV_A=from-e"))
        has("env file read", "a: from-file", render("plain", "--env-file", env))
        has("-e beats env file", "b: cli", render("plain", "--env-file", env, "-e", "BV_B=cli"))
        has(
            "env file beats shell",
            "a: from-file",
            render("plain", "--env-file", env, shell=["BV_A=shell"]),
        )
        has("dir .env read", "b: dotenv", render("dotenv"))
        has("${NAME:default} gives the default", "dflt: fallback", render("plain"))
        has("$${NAME} stays literal", "esc: ${BV_ESC}", render("plain"))
        got = render("needs")
        row(
            "missing variable fails",
            got.returncode != 0 and "BV_NEED" in got.stdout + got.stderr,
            got,
        )
        made = host.phenix(
            "config",
            "create",
            f"{tmp}/cfg.yml",
            "-e",
            "BV_CFG=stored",
            "--skip-validation",
            check=False,
            capture=True,
        )
        got = host.phenix(
            "config", "get", f"topology/{name}", "-o", "yaml", check=False, capture=True
        )
        row(
            "config create -e stores the value",
            made.returncode == 0 and "cfg: stored" in got.stdout,
            made if made.returncode else got,
        )
    finally:
        host.phenix("config", "delete", f"topology/{name}", check=False, capture=True)
        host.ctr("rm", "-rf", tmp, check=False, capture=True)
    return rows
