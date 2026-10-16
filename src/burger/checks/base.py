from dataclasses import dataclass
from pathlib import Path

from burger.items import Item
from burger.routes import Route


@dataclass
class CheckContext:
    item: Item
    dirs: list[Path]  # the item component's run-N/<component>/loop-* directories
    route: Route
    manifest: dict
    work: Path


@dataclass
class Row:
    item: str
    check: str
    verdict: str  # ok | FAIL | WARN | skipped | advisory-FAIL
    reason: str
    paths: list[str]


def row(ctx: CheckContext, check: str, ok: bool, reason: str, paths=()) -> Row:
    return Row(ctx.item.name, check, "ok" if ok else "FAIL", reason, [str(p) for p in paths])


def find(spec: dict, ctx: CheckContext) -> list[Path]:
    """Files matching spec['glob'] under each loop dir, sorted."""
    return sorted(p for d in ctx.dirs for p in d.glob(spec["glob"]) if p.is_file())


def missing(spec: dict, ctx: CheckContext, check: str) -> Row:
    where = ", ".join(f"{d}/{spec['glob']}" for d in ctx.dirs) or f"(no loop dirs)/{spec['glob']}"
    return row(ctx, check, False, f"no output found at {where}")
