"""GEO-9 — USGS 3DEP slope / terrain (clipped to Kern County).

One fetcher that turns a Digital Elevation Model into a slope (percent-grade) raster:

* `slope` — a single-band float32 GeoTIFF of slope in percent, computed in the metric CRS
  (EPSG:26911) and clipped to the county polygon. Drives the parcel slope Stage-A exclusion
  (> ``SLOPE_MAX_PCT``) and the per-parcel zonal-mean slope in enrichment (GEO-13); the
  zonal sampling itself is done there, not here.

Unlike every other fetcher the primary output is a raster *sidecar* (``slope.tif`` in the
release dir), not a DuckDB table. To keep the one-table-per-fetcher manifest/reader contract
intact, the fetcher also creates a small ``slope_raster`` metadata table (one row per emitted
raster: role / resolution / path / profile / stats) so GEO-13 can locate and describe the
raster from inside the artifact.

DEM acquisition (CONVENTIONS §2; review C11 two-resolution policy):
* The DEM is fetched over the county bbox via ``seamless_3dep.get_dem(bbox, save_dir,
  res=10|30|60)`` → GeoTIFF tiles in EPSG:4326. ``seamless_3dep`` is imported lazily, only on
  the live path (like gridstatus for CAISO), so the offline build/test suite needs neither it
  nor the network. The live 3DEP endpoint is a US-gov source that geo-blocks non-US IPs —
  validate the live path from a US egress.
* A pre-staged DEM GeoTIFF can be supplied via ``GEO_DEM_SOURCE`` for offline/air-gapped runs
  and tests (its CRS is read from the file, or overridden with ``GEO_DEM_SOURCE_CRS``).
* 30 m = the broad SCREENING pass (county-wide, always emitted as ``slope.tif``). 10 m = the
  FINAL pass for top candidates; the candidate set is unknown at ingest time, so the 10 m
  raster is emitted only when an explicit AOI ("west,south,east,north" in 4326) is given via
  ``GEO_SLOPE_FINAL_AOI`` — otherwise the same compute path is reused later per candidate.

Slope is computed in EPSG:26911 (meters) because slope is a metric quantity; computing it in
degrees would be wrong. rasterio + numpy are hard runtime deps of this fetcher and are
imported lazily inside the compute functions so module discovery never fails without them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import clip, config, sources
from ..logging_setup import log_event
from .base import FetchContext, Fetcher, LayerResult, register

_Bbox = tuple[float, float, float, float]


def _county_polygon_geojson(con: Any) -> dict:
    """The county_boundary polygon (EPSG:4326) as a GeoJSON geometry dict (for raster mask)."""
    row = con.execute(
        f"SELECT ST_AsGeoJSON(ST_Union_Agg(geom)) FROM {clip.COUNTY_TABLE}"
    ).fetchone()
    if row is None or row[0] is None:
        raise sources.SourceError(
            f"{clip.COUNTY_TABLE} has no geometry; the county_boundary fetcher (GEO-3) must run first"
        )
    return json.loads(row[0])


def _merge_tifs(tiles: list[Path], dest: Path) -> Path:
    """Merge 3DEP DEM tiles into one GeoTIFF (live path)."""
    import rasterio
    from rasterio.merge import merge

    srcs = [rasterio.open(t) for t in tiles]
    try:
        mosaic, transform = merge(srcs)
        profile = srcs[0].profile
        profile.update(
            height=mosaic.shape[1], width=mosaic.shape[2], transform=transform, count=mosaic.shape[0]
        )
        with rasterio.open(dest, "w", **profile) as dst:
            dst.write(mosaic)
    finally:
        for s in srcs:
            s.close()
    return dest


def _resolve_dem(ctx: FetchContext, *, bbox: _Bbox, res_m: int) -> tuple[Path, int, str]:
    """Return (DEM GeoTIFF path, source CRS, label). Local override wins; else live 3DEP."""
    override = sources.local_override(config.DEM_SOURCE_ENV)
    if override is not None:
        crs_code = int(os.environ.get(config.DEM_SOURCE_CRS_ENV, config.CRS_STORAGE))
        return override, crs_code, f"local:{override.name}"

    try:
        import seamless_3dep
    except ImportError as err:  # neutralized in tests; absent in slim/offline builds
        raise sources.SourceError(
            f"no DEM source: set {config.DEM_SOURCE_ENV} (local GeoTIFF) or install "
            f"seamless-3dep for the live USGS 3DEP fetch"
        ) from err

    save_dir = Path(ctx.work_dir) / "dem_tiles"
    save_dir.mkdir(parents=True, exist_ok=True)
    tiles = seamless_3dep.get_dem(tuple(bbox), save_dir, res=int(res_m))
    tiles = [Path(t) for t in (tiles or [])]
    if not tiles:
        raise sources.SourceError(f"seamless-3dep returned no DEM tiles for bbox {bbox}")
    merged = Path(ctx.work_dir) / f"dem_{res_m}m.tif"
    if len(tiles) == 1:
        return tiles[0], config.CRS_STORAGE, f"3DEP {res_m}m"
    _merge_tifs(tiles, merged)
    return merged, config.CRS_STORAGE, f"3DEP {res_m}m"


def _slope_from_dem(
    dem_path: Path,
    *,
    source_crs: int,
    res_m: int,
    county_geojson: dict,
    out_path: Path,
    metric_crs: int = config.SLOPE_METRIC_CRS,
    nodata: float = config.SLOPE_NODATA,
    window_bbox_4326: _Bbox | None = None,
) -> dict:
    """Reproject the DEM to ``metric_crs`` at ``res_m`` metres, compute slope (percent grade),
    mask to the county polygon, and write a single-band float32 GeoTIFF. Returns a profile/stats
    dict. Slope is computed in the metric CRS so dz/dx is metres-per-metre (CONVENTIONS §2).

    ``window_bbox_4326`` (the final-pass AOI) crops the source DEM to that bbox BEFORE
    reprojection, so a 10 m final pass over a small AOI does not reproject the whole county
    raster (which would be enormous when the staged DEM is county-wide)."""
    import math

    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.features import geometry_mask
    from rasterio.transform import array_bounds
    from rasterio.warp import (
        Resampling,
        calculate_default_transform,
        reproject,
        transform_bounds,
        transform_geom,
    )
    from rasterio.windows import Window
    from rasterio.windows import from_bounds as window_from_bounds

    dst_crs = CRS.from_epsg(int(metric_crs))
    with rasterio.open(dem_path) as src:
        src_crs = src.crs or CRS.from_epsg(int(source_crs))
        if window_bbox_4326 is not None:
            l, b, r, t = transform_bounds("EPSG:4326", src_crs, *window_bbox_4326)
            win = window_from_bounds(l, b, r, t, src.transform)
            c0 = max(0, int(math.floor(win.col_off)))
            r0 = max(0, int(math.floor(win.row_off)))
            c1 = min(src.width, int(math.ceil(win.col_off + win.width)))
            r1 = min(src.height, int(math.ceil(win.row_off + win.height)))
            if c1 <= c0 or r1 <= r0:
                raise sources.SourceError(
                    f"final-pass AOI {window_bbox_4326} does not overlap the DEM extent"
                )
            window = Window(c0, r0, c1 - c0, r1 - r0)
            src_arr = src.read(1, window=window).astype("float32")
            src_transform = src.window_transform(window)
        else:
            src_arr = src.read(1).astype("float32")
            src_transform = src.transform
        src_nodata = src.nodata
        src_h, src_w = src_arr.shape

    src_bounds = array_bounds(src_h, src_w, src_transform)
    transform, width, height = calculate_default_transform(
        src_crs, dst_crs, src_w, src_h, *src_bounds, resolution=float(res_m)
    )
    dem = np.full((height, width), np.nan, dtype="float32")
    reproject(
        source=src_arr,
        destination=dem,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=transform,
        dst_crs=dst_crs,
        src_nodata=src_nodata,
        dst_nodata=float("nan"),
        resampling=Resampling.bilinear,
    )

    if width < 2 or height < 2:
        raise sources.SourceError(
            f"DEM too small to compute slope after reprojection ({width}x{height}); "
            f"check the DEM covers the county at {res_m} m"
        )

    # np.gradient(array, dy, dx) → (d/drow, d/dcol); spacings are pixel size in metres.
    xres = abs(transform.a)
    yres = abs(transform.e)
    gy, gx = np.gradient(dem, yres, xres)
    slope_pct = np.hypot(gx, gy) * 100.0

    # Mask everything outside the county polygon (reprojected into the metric CRS) to nodata.
    geom_metric = transform_geom("EPSG:4326", dst_crs.to_string(), county_geojson)
    outside = geometry_mask([geom_metric], out_shape=slope_pct.shape, transform=transform, invert=False)
    slope_pct[outside] = nodata
    slope_pct[~np.isfinite(slope_pct)] = nodata  # nodata cells + nan that crept in via gradient

    with rasterio.open(
        out_path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs=dst_crs, transform=transform, nodata=nodata, compress="deflate",
    ) as dst:
        dst.write(slope_pct.astype("float32"), 1)

    valid = slope_pct[slope_pct != nodata]
    b = array_bounds(height, width, transform)  # (xmin, ymin, xmax, ymax) in metric_crs
    return {
        "resolution_m": int(res_m),
        "path": out_path.name,
        "crs_epsg": int(metric_crs),
        "width": int(width),
        "height": int(height),
        "nodata": float(nodata),
        "min_slope_pct": float(valid.min()) if valid.size else None,
        "max_slope_pct": float(valid.max()) if valid.size else None,
        "mean_slope_pct": float(valid.mean()) if valid.size else None,
        "valid_cells": int(valid.size),
        "bbox_xmin": float(b[0]), "bbox_ymin": float(b[1]),
        "bbox_xmax": float(b[2]), "bbox_ymax": float(b[3]),
    }


def _parse_aoi(raw: str) -> _Bbox | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    parts = [p for p in raw.replace(" ", "").split(",") if p]
    if len(parts) != 4:
        raise sources.SourceError(
            f"{config.SLOPE_FINAL_AOI_ENV} must be 'west,south,east,north' (4326); got {raw!r}"
        )
    w, s, e, n = (float(p) for p in parts)
    return (w, s, e, n)


@register
class SlopeFetcher(Fetcher):
    name = "slope"
    run_order = 50  # after county_boundary (0) and the vector clip layers (20-42)

    def fetch(self, ctx: FetchContext) -> LayerResult:
        con, log = ctx.con, ctx.logger
        bbox = clip.county_bbox(con)  # validates GEO-3 ran; also the live DEM fetch bbox
        county_geojson = _county_polygon_geojson(con)

        rows: list[dict] = []

        # Screening pass: 30 m, county-wide → slope.tif (always emitted). Resolution is
        # env-overridable (GEO_DEM_RES_M) for the live 3DEP fetch (must be 10/30/60) and so
        # tests can compute on a coarse grid; defaults to SLOPE_SCREENING_RES_M (30 m).
        raw_res = os.environ.get(config.DEM_RES_ENV, "").strip()
        screen_res = int(raw_res) if raw_res else config.SLOPE_SCREENING_RES_M
        dem_path, dem_crs, label = _resolve_dem(ctx, bbox=bbox, res_m=screen_res)
        screen = _slope_from_dem(
            dem_path, source_crs=dem_crs, res_m=screen_res, county_geojson=county_geojson,
            out_path=Path(ctx.work_dir) / config.SLOPE_SCREENING_TIF,
        )
        screen["role"] = "screening"
        screen["source"] = label
        rows.append(screen)
        # Fail loud: a county-wide screening raster that is entirely nodata (no valid in-county
        # cells) is the classic symptom of a DEM/county CRS or extent mismatch — emitting it
        # would let GEO-13 enrichment silently sample nodata for every parcel. (The optional
        # per-AOI final pass below may legitimately be sparse, so this guards screening only;
        # cf. flood.py's 0-feature guard.)
        if screen["valid_cells"] == 0:
            raise sources.SourceError(
                f"slope screening of {label!r} yielded 0 valid in-county cells "
                f"(DEM/county CRS or extent mismatch)"
            )

        # Final pass: 10 m, only for an explicit AOI (top-candidate re-evaluation).
        aoi = _parse_aoi(os.environ.get(config.SLOPE_FINAL_AOI_ENV, ""))
        if aoi is not None:
            final_res = config.SLOPE_FINAL_RES_M
            fdem_path, fdem_crs, flabel = _resolve_dem(ctx, bbox=aoi, res_m=final_res)
            final = _slope_from_dem(
                fdem_path, source_crs=fdem_crs, res_m=final_res, county_geojson=county_geojson,
                out_path=Path(ctx.work_dir) / config.SLOPE_FINAL_TIF,
                window_bbox_4326=aoi,  # crop to the AOI so a staged county-wide DEM isn't
                                       # reprojected whole at 10 m
            )
            final["role"] = "final"
            final["source"] = flabel
            rows.append(final)

        self._write_metadata_table(con, rows)
        n = len(rows)
        log_event(
            log, "slope.built", rasters=n, screening_res_m=screen_res,
            screening_max_pct=screen["max_slope_pct"], source=label,
        )
        return LayerResult(
            name=self.name,
            table=config.SLOPE_TABLE,
            feature_count=n,
            source=label,
            parquet_path=None,
            extra={"rasters": [r["path"] for r in rows], "screening": screen},
        )

    @staticmethod
    def _write_metadata_table(con: Any, rows: list[dict]) -> None:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {config.SLOPE_TABLE} (
                id INTEGER, role VARCHAR, resolution_m INTEGER, path VARCHAR, source VARCHAR,
                crs_epsg INTEGER, width INTEGER, height INTEGER, nodata DOUBLE,
                min_slope_pct DOUBLE, max_slope_pct DOUBLE, mean_slope_pct DOUBLE,
                valid_cells BIGINT,
                bbox_xmin DOUBLE, bbox_ymin DOUBLE, bbox_xmax DOUBLE, bbox_ymax DOUBLE
            )
            """
        )
        for i, r in enumerate(rows, start=1):
            con.execute(
                f"INSERT INTO {config.SLOPE_TABLE} VALUES "
                f"(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    i, r["role"], r["resolution_m"], r["path"], r["source"],
                    r["crs_epsg"], r["width"], r["height"], r["nodata"],
                    r["min_slope_pct"], r["max_slope_pct"], r["mean_slope_pct"],
                    r["valid_cells"],
                    r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"],
                ],
            )
