import importlib.util
from collections.abc import Callable

from burger.checks.base import CheckContext, Row, row
from burger.config import BurgerError
from burger.items import Item


def load(item: Item, function: str) -> Callable[[CheckContext], tuple[bool, str]]:
    """Import the item's sibling .py by path and return its function. Trusted repo code."""
    path = item.path.with_suffix(".py")
    if not path.is_file():
        raise BurgerError(f"{item.path}: python check needs {path}")
    spec = importlib.util.spec_from_file_location(f"burger_item_{item.kind}_{item.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, function, None)
    if not callable(fn):
        raise BurgerError(f"{path}: no function '{function}'")
    return fn


def python(spec, ctx: CheckContext) -> Row:
    function = spec if isinstance(spec, str) else spec["function"]
    ok, message = load(ctx.item, function)(ctx)
    return row(ctx, "python", bool(ok), str(message), ctx.dirs)
