"""Fetcher package — interface, registry, and auto-discovery.

Drop a new module in this package that defines a `@register`-ed Fetcher subclass and it
is picked up automatically by `load_all()`; no central list to edit (FR-A3).
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

from .base import (
    FetchContext,
    Fetcher,
    LayerResult,
    clear_registry,
    iter_fetchers,
    register,
)

__all__ = [
    "Fetcher",
    "FetchContext",
    "LayerResult",
    "register",
    "iter_fetchers",
    "clear_registry",
    "load_all",
]


def load_all() -> None:
    """Import every public fetcher submodule so its `@register` runs.

    Reload-safe: a module already in sys.modules is reloaded so `@register` re-fires
    after a `clear_registry()` (otherwise the cached import is a no-op and discovery
    silently yields nothing). Private modules (``_``-prefixed) and ``base`` are skipped.
    """
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "base" or mod.name.startswith("_"):
            continue
        qualified = f"{__name__}.{mod.name}"
        existing = sys.modules.get(qualified)
        if existing is not None:
            importlib.reload(existing)
        else:
            importlib.import_module(qualified)
