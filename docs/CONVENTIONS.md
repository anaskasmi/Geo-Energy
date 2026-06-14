# Conventions — CRS, units, artifact layout

These rules are load-bearing. Every workstream (ingest, api, agent, frontend) must
follow them. Spec refs: §1 Architecture, §4 Data, §11 Portability.

## 1. Coordinate Reference Systems (CRS)

| Purpose                     | CRS / EPSG          | Notes                                        |
|-----------------------------|---------------------|----------------------------------------------|
| **Storage & serving**       | `EPSG:4326` (WGS84) | lon/lat degrees. All geometry stored/served here. PMTiles, GeoJSON, API I/O. |
| **Metric compute (local)**  | `EPSG:26911`        | UTM Zone 11N (meters). Default for distance/area/slope in Kern County. |
| **Metric compute (state)**  | `EPSG:3310`         | CA Albers (meters). Use for statewide/cross-zone work. |

### The `always_xy` rule

> **Every `ST_Transform` MUST pass `always_xy := true`.**

DuckDB spatial (and the GeoJSON/GeoParquet we exchange) treat coordinates as
**(longitude, latitude)** = (x, y). Several authorities (incl. EPSG:4326) declare
**lat/lon** axis order. Without `always_xy := true`, `ST_Transform` may silently swap
axes and place Kern County in the Indian Ocean. There is no valid reason to omit it.

```sql
-- 4326 (stored) → 26911 (compute in meters)
ST_Transform(geom, 'EPSG:4326', 'EPSG:26911', always_xy := true)
```

```python
# Python side (pyproj) — same rule:
from pyproj import Transformer
t = Transformer.from_crs("EPSG:4326", "EPSG:26911", always_xy=True)
```

Use the helpers in `ingest/pipeline/crs.py` rather than open-coding transforms, so the
flag can never be forgotten.

## 2. Units

- **Distances**: meters (computed in 26911/3310, never in degrees).
- **Areas**: square meters internally; expose acres/hectares at the API/UI boundary.
- **Slope**: degrees or percent — compute from the DEM in a metric CRS (26911).
- **Angles / coordinates as stored**: decimal degrees (4326).

## 3. Artifact layout (the `data:` volume)

The ingest job is idempotent and swaps atomically. Readers (api, web) **must** read
through the `current/` path; never open a `releases/` path directly.

```
/data/
├── current            → symlink to the live release (atomic swap target)
├── releases/
│   └── <build_id>/     # one immutable directory per successful build
│       ├── site.duckdb         # read-only DuckDB artifact (LOAD spatial)
│       ├── *.pmtiles           # vector tiles (GEO-14+)
│       ├── slope.tif           # DEM-derived slope raster (later)
│       ├── ghi_grid.parquet    # NREL solar grid (later)
│       └── manifest.json       # build metadata (sources, versions, row counts)
└── releases/<id>/_SUCCESS      # written last; absence ⇒ incomplete build
```

- Build into a **temp release dir**, then atomically repoint `current` (`os.replace`
  of a symlink on the same filesystem is atomic — readers never see a half-built set).
- Old releases are pruned (keep last N); a failed build removes its own dir and leaves
  `current` untouched (FR-A1).
- `httpfs` is used **only for remote reads** during ingest — never on the request path.

Resolve paths with `pipeline.config` (e.g. `current_artifact_path()` →
`/data/current/site.duckdb`) so the layout has a single source of truth.

## 4. GeoParquet intermediates

Layer fetchers emit **GeoParquet 1.1** intermediates. DuckDB's Parquet writer emits the
file-level `geo` metadata automatically for a geometry-typed column (WKB encoding,
geometry types, column bbox); CRS is omitted, which per the GeoParquet spec means the
default **OGC:CRS84 == EPSG:4326** lon/lat — our storage CRS. Each intermediate also
carries an explicit `bbox` STRUCT column `{xmin, ymin, xmax, ymax}` so plain SQL can
spatially pre-filter (row-group prune) before geometry is decoded. DuckDB (with spatial
loaded) reads them straight back as a `GEOMETRY` column. See
`ingest/pipeline/geoparquet.py`.

## 5. Naming & misc

- DuckDB tables: `snake_case`, singular layer name (`parcel`, `transmission_line`).
- Geometry column is always named `geom`.
- IDs/keys preserved from source where stable; otherwise a synthetic `id` BIGINT.
- Times are UTC ISO-8601 in manifests and logs.
