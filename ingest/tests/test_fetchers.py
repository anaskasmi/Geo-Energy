"""Fetcher registry contract: ordering, guards, and auto-discovery (FR-A3)."""

import importlib
import sys
from pathlib import Path

import pytest

from pipeline import fetchers
from pipeline.fetchers import base
from pipeline.fetchers.base import Fetcher, iter_fetchers, register

FETCHERS_DIR = Path(base.__file__).parent


@pytest.fixture(autouse=True)
def clean_registry():
    base.clear_registry()
    yield
    base.clear_registry()


def test_iter_fetchers_orders_by_run_order_then_name():
    @register
    class B(Fetcher):
        name = "b_layer"
        run_order = 0

        def fetch(self, ctx): ...

    @register
    class A(Fetcher):
        name = "a_layer"
        run_order = 0

        def fetch(self, ctx): ...

    @register
    class C(Fetcher):
        name = "c_layer"
        run_order = 5

        def fetch(self, ctx): ...

    assert [c.name for c in iter_fetchers()] == ["a_layer", "b_layer", "c_layer"]


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):

        @register
        class Nameless(Fetcher):
            def fetch(self, ctx): ...


def test_register_rejects_duplicate_name():
    @register
    class One(Fetcher):
        name = "dup"

        def fetch(self, ctx): ...

    with pytest.raises(ValueError):

        @register
        class Two(Fetcher):
            name = "dup"

            def fetch(self, ctx): ...


def test_load_all_discovers_and_is_reload_safe():
    """A dropped-in module is discovered; discovery re-fires after clear_registry()."""
    mod_name = "demo_layer_fetcher"
    mod_path = FETCHERS_DIR / f"{mod_name}.py"
    qualified = f"pipeline.fetchers.{mod_name}"
    mod_path.write_text(
        "from pipeline.fetchers.base import Fetcher, register\n\n"
        "@register\n"
        "class DemoLayer(Fetcher):\n"
        "    name = 'demo_layer'\n"
        "    run_order = 0\n"
        "    def fetch(self, ctx):\n"
        "        raise NotImplementedError\n"
    )
    importlib.invalidate_caches()
    try:
        fetchers.load_all()
        assert "demo_layer" in [c.name for c in iter_fetchers()]

        # After a clear, a second load_all() must re-register (not a cached no-op).
        base.clear_registry()
        assert [c.name for c in iter_fetchers()] == []
        fetchers.load_all()
        assert "demo_layer" in [c.name for c in iter_fetchers()]
    finally:
        mod_path.unlink(missing_ok=True)
        sys.modules.pop(qualified, None)
