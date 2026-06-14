"""Per-layer fetcher plugin interface + registry (FR-A3).

A *fetcher* is responsible for one source layer end-to-end: fetch → clean → reproject to
EPSG:4326 → load a table into the build DuckDB (optionally via a GeoParquet intermediate,
see geoparquet.py). The harness discovers registered fetchers and runs them in
`run_order` (ascending; ties broken by `name`), so e.g. the county boundary (run_order=0)
can run before layers that clip to it.

Define a layer (GEO-3+):

    from pipeline.fetchers.base import Fetcher, FetchContext, LayerResult, register

    @register
    class CountyBoundary(Fetcher):
        name = "county_boundary"
        run_order = 0

        def fetch(self, ctx: FetchContext) -> LayerResult:
            # use ctx.con (spatial + httpfs loaded) and ctx.work_dir for intermediates;
            # httpfs only for remote reads, never on the request path.
            ctx.con.execute("CREATE TABLE county_boundary AS SELECT ...")
            n = ctx.con.execute("SELECT count(*) FROM county_boundary").fetchone()[0]
            return LayerResult(name=self.name, table="county_boundary",
                               feature_count=n, source="TIGER/Line 2023")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FetchContext:
    """Everything a fetcher needs for one build."""

    work_dir: Path                 # scratch dir for intermediates (this release dir)
    con: Any                       # build DuckDB connection (spatial + httpfs loaded)
    settings: Any                  # pipeline.config.Settings
    logger: Any                    # structured logger


@dataclass
class LayerResult:
    """What a fetcher reports back to the harness for the manifest."""

    name: str
    table: str
    feature_count: int = 0
    source: str = ""
    parquet_path: Path | None = None
    extra: dict = field(default_factory=dict)


class Fetcher(ABC):
    name: str = ""
    run_order: int = 100           # lower runs earlier

    @abstractmethod
    def fetch(self, ctx: FetchContext) -> LayerResult:
        ...


_REGISTRY: dict[str, type[Fetcher]] = {}


def register(cls: type[Fetcher]) -> type[Fetcher]:
    """Class decorator: register a concrete fetcher by its `name`."""
    name = getattr(cls, "name", "")
    if not name:
        raise ValueError(f"{cls.__name__} must set a non-empty `name` to be registered")
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(f"Duplicate fetcher name {name!r}: {existing.__name__} vs {cls.__name__}")
    _REGISTRY[name] = cls
    return cls


def iter_fetchers() -> list[type[Fetcher]]:
    """Registered fetcher classes in run order (run_order asc, then name)."""
    return [_REGISTRY[n] for n in sorted(_REGISTRY, key=lambda n: (_REGISTRY[n].run_order, n))]


def clear_registry() -> None:
    """Reset the registry (used by tests)."""
    _REGISTRY.clear()
