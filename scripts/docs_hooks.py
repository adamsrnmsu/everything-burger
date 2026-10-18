"""MkDocs hook: generate the reference pages from the repo, so they cannot drift.

reference/items.md and reference/switchboards.md embed every YAML file verbatim;
reference/cli.md is the argparse help of every burger command.
"""

import argparse
import os
from pathlib import Path

import yaml
from mkdocs.structure.files import File

ROOT = Path(__file__).resolve().parents[1]
KINDS = (("components", "SCORCH components"), ("apps", "phēnix apps"), ("bennu", "bennu"))


def _yaml_block(path: Path) -> str:
    return f"```yaml\n{path.read_text().rstrip()}\n```\n"


def _items() -> str:
    out = [
        "# Items\n",
        "Every item file in the repo, generated at build time. Turn one on in a "
        "switchboard with `<ref>: on`.\n",
    ]
    for folder, title in KINDS:
        out.append(f"## {title}\n")
        for path in sorted((ROOT / folder).glob("*.yaml")):
            doc = yaml.safe_load(path.read_text()) or {}
            ref = path.stem if folder == "components" else f"{folder}/{path.stem}"
            routes = ", ".join(doc.get("routes") or []) or "validate-only"
            flags = [f for f in ("advisory", "background", "holds") if doc.get(f)]
            out.append(f"### `{ref}`\n")
            out.append(f"{(doc.get('about') or '').strip()}\n")
            meta = f"**Routes:** {routes}"
            if flags:
                meta += f" · **Flags:** {', '.join(flags)}"
            out.append(meta + "\n")
            out.append(f'??? note "{folder}/{path.name}"\n')
            out.append("    " + _yaml_block(path).replace("\n", "\n    ").rstrip() + "\n")
    return "\n".join(out)


def _switchboards() -> str:
    out = ["# Switchboards\n", "Every switchboard in the repo, generated at build time.\n"]
    for path in sorted((ROOT / "switchboards").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        out.append(f"## `{path.stem}`\n")
        out.append(f"{(doc.get('about') or '').strip()} (route `{doc.get('route')}`)\n")
        out.append(f"Run it: `./burger run {path.stem}`\n")
        out.append(_yaml_block(path))
    return "\n".join(out)


def _cli() -> str:
    from burger.cli import _parser

    os.environ["COLUMNS"] = "88"  # stable wrapping in the help text
    parser = _parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    out = ["# CLI\n", "`./burger --help` and each command's help, generated at build time.\n"]
    out.append(f"```text\n{parser.format_help().rstrip()}\n```\n")
    seen = set()
    for name, cmd in sub.choices.items():
        if id(cmd) in seen:  # `round` is an alias of `run`
            continue
        seen.add(id(cmd))
        out.append(f"## `{name}`\n")
        out.append(f"```text\n{cmd.format_help().rstrip()}\n```\n")
    return "\n".join(out)


def on_files(files, config):
    for uri, make in (
        ("reference/items.md", _items),
        ("reference/switchboards.md", _switchboards),
        ("reference/cli.md", _cli),
    ):
        files.append(File.generated(config, uri, content=make()))
    return files
