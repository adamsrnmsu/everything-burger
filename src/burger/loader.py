import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from burger.config import BurgerError

_VAR = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def load_yaml(
    path: Path, allowed: set[str] | None = None, env: Mapping[str, str] | None = None
) -> tuple[dict, list[str]]:
    """Parse path with ${VAR} / ${VAR:-default} expanded; return (doc, names left unset)."""
    env = os.environ if env is None else env
    unset: list[str] = []

    def expand(m):
        val = env.get(m[1], m[2])
        if val is None:
            unset.append(m[1])
            return m[0]
        return val

    try:
        doc = yaml.safe_load(_VAR.sub(expand, Path(path).read_text())) or {}
    except (OSError, yaml.YAMLError) as e:
        raise BurgerError(f"{path}: {e}") from None
    if not isinstance(doc, dict):
        raise BurgerError(f"{path}: expected a mapping at the top level")
    if allowed is not None:
        for key in doc:
            if key not in allowed:
                raise BurgerError(
                    f"{path}: unknown field '{key}' (allowed: {', '.join(sorted(allowed))})"
                )
    return doc, unset
