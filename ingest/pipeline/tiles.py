"""GEO-14 — parcels.pmtiles generation via tippecanoe.

Tiles the parcels GeoJSON the parcels fetcher (GEO-4) emits into a PMTiles archive for the
SPA (served later over HTTP byte-range by nginx, GEO-34). Zoom-based simplification builds a
lightweight base layer; only the base attributes (parcel id, APN, acreage) are carried into
the tiles. Independent of the DuckDB builder (FR-A6) — run it after/in parallel with the
artifact build (`make tiles` / `python -m pipeline.tiles`).

tippecanoe is a native binary, not a Python dependency: it is resolved on PATH or via the
GEO_TIPPECANOE_BIN env var. Bundling it into the ingest image is deployment work (GEO-33).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import config
from .logging_setup import get_logger, log_event
from .sources import SourceError

_log = get_logger("ingest.tiles")


def tippecanoe_bin() -> str:
    return os.environ.get(config.TIPPECANOE_BIN_ENV, "").strip() or "tippecanoe"


def build_command(
    geojson_path: str | Path,
    out_path: str | Path,
    *,
    layer: str = config.PARCELS_TILE_LAYER,
    minzoom: int = config.PARCELS_TILE_MINZOOM,
    maxzoom: int = config.PARCELS_TILE_MAXZOOM,
    simplification: int = config.PARCELS_TILE_SIMPLIFICATION,
    attrs: tuple[str, ...] = config.PARCELS_TILE_ATTRS,
    tippecanoe: str = "tippecanoe",
) -> list[str]:
    """The tippecanoe argv. `-y` keeps ONLY the named attributes; `--drop-densest-as-needed`
    keeps dense tiles under the size limit; `--simplification` simplifies geometry below the
    max zoom (zoom-based)."""
    cmd = [
        tippecanoe,
        "-o", str(out_path),
        "--force",                       # overwrite an existing archive (idempotent re-run)
        "-l", layer,                     # single named source-layer the SPA references
        f"-Z{int(minzoom)}", f"-z{int(maxzoom)}",
        "--simplification", str(int(simplification)),
        "--drop-densest-as-needed",      # respect per-tile size limits at low zoom
    ]
    for attr in attrs:                   # -y NAME → include only these attributes
        cmd += ["-y", attr]
    cmd.append(str(geojson_path))
    return cmd


def build_parcels_pmtiles(
    geojson_path: str | Path,
    out_path: str | Path,
    *,
    layer: str = config.PARCELS_TILE_LAYER,
    minzoom: int = config.PARCELS_TILE_MINZOOM,
    maxzoom: int = config.PARCELS_TILE_MAXZOOM,
    simplification: int = config.PARCELS_TILE_SIMPLIFICATION,
    attrs: tuple[str, ...] = config.PARCELS_TILE_ATTRS,
    tippecanoe: str | None = None,
    logger=None,
) -> Path:
    """Run tippecanoe to produce `out_path` (a .pmtiles archive) from `geojson_path`."""
    geojson_path = Path(geojson_path)
    out_path = Path(out_path)
    log = logger or _log
    if not geojson_path.exists():
        raise SourceError(f"parcels GeoJSON not found for tiling: {geojson_path}")
    tbin = tippecanoe or tippecanoe_bin()
    if shutil.which(tbin) is None:
        raise SourceError(
            f"tippecanoe not found on PATH (set {config.TIPPECANOE_BIN_ENV}): {tbin!r}"
        )
    cmd = build_command(
        geojson_path, out_path, layer=layer, minzoom=minzoom, maxzoom=maxzoom,
        simplification=simplification, attrs=attrs, tippecanoe=tbin,
    )
    log_event(log, "tiles.tippecanoe_start", cmd=" ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SourceError(
            f"tippecanoe failed (exit {proc.returncode}): {proc.stderr.strip()[:2000]}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SourceError(f"tippecanoe produced no output at {out_path}")
    log_event(log, "tiles.built", out=str(out_path), bytes=out_path.stat().st_size)
    return out_path


def tile_current_release(settings=None) -> Path:
    """Tile the parcels GeoJSON in the live release (`/data/current/parcels.geojson`) into
    `/data/current/parcels.pmtiles`. Readers go through the `current/` symlink (CONVENTIONS §3)."""
    cfg = settings or config.from_env()
    current = cfg.current_link
    geojson = current / config.PARCELS_GEOJSON_NAME
    out = current / config.PARCELS_PMTILES_NAME
    return build_parcels_pmtiles(geojson, out)


def main() -> None:
    from . import logging_setup

    cfg = config.from_env()
    logging_setup.configure(cfg.log_level)
    out = tile_current_release(cfg)
    print(out)


if __name__ == "__main__":
    main()
