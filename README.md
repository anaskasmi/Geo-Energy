# Geo-Energy — A Geospatial Decision-Support System for Renewable & Data-Center Site Selection

> A reproducible, public-data pipeline and scoring engine that ranks land parcels for utility-scale
> solar and data-center development, paired with a map-first web app and an LLM assistant that drives
> the analysis in natural language (text **and** voice).

> ⚠️ The U.S. government data APIs (Census, FEMA, HIFLD, USGS 3DEP, NREL) geo-block non-U.S. IPs — run the ingest from a U.S. host or connect through a U.S. VPN first.

---

## Abstract

Siting renewable-energy and data-center facilities is a multi-criteria spatial optimization problem:
the ideal parcel is large, flat, sunny (for solar), close to high-voltage transmission and substation
capacity, outside flood and zoning exclusions, uncongested by existing interconnection queues, and
affordable. **Geo-Energy** operationalizes this as a two-stage scoring model over a unified parcel
database assembled entirely from authoritative public sources. The system is built as four decoupled
services: an **offline ingestion pipeline** that fetches, cleans, reprojects, and spatially enriches
~420k parcels into a read-only columnar artifact; a **FastAPI scoring engine** that ranks any
user-drawn area in milliseconds; a **React/MapLibre single-page app**; and a **Pydantic-AI assistant**
(Google Gemini by default) that orchestrates the engine through a parity-enforced tool registry and
narrates the results. The reference deployment covers **Kern County, California** (GEOID `06029`) — one
of the densest renewable-development markets in the U.S. — but every geographic constant is isolated
behind configuration, so the same architecture generalizes to any county, state, or country with
comparable open data (see [§9 Scalability](#9-scalability)).

---

## 1. Introduction

Land suitability for energy infrastructure is conventionally assessed with GIS overlays maintained by
hand. Geo-Energy reframes that workflow as a **reproducible data product**: a single offline job
materializes an immutable, versioned artifact from public sources, and all downstream serving reads
that artifact read-only. This separation yields three properties that matter for a decision-support
tool: **reproducibility** (the artifact is rebuilt from declarative source URLs and a curated rules
table), **determinism** (every spatial join uses deterministic tie-breaks; scores are pure functions of
the artifact and the weight vector), and **safety** (the heavy pipeline never runs on the request path,
so user-facing latency is bounded by an in-process columnar query).

### Design tenets

1. **Offline build, online read.** Ingestion is a one-shot job; it builds into a staging directory and
   *atomically swaps* `data:/current` so readers never see a partial artifact.
2. **CRS discipline.** Everything is stored and served in EPSG:4326; all distance/area/slope math is
   done in a projected metric CRS (EPSG:26911 UTM 11N), with `always_xy := true` on every transform.
3. **The engine owns the numbers.** The LLM never invents geometry, parcel IDs, or scores — it only
   calls tools and narrates their output.
4. **Graceful degradation.** Missing API keys, absent tile archives, or unreachable external services
   degrade to a clean disabled state rather than an error.

---

## 2. System Architecture

Four containers share one named volume holding the build artifact.

```
                         ┌─────────────────────────────┐
   public data sources ─▶│  ingest  (Service A)        │  one-shot, offline
   (Census, HIFLD,       │  fetch → clean → reproject  │  `compose run --rm ingest`
    FEMA, 3DEP, NREL,     │   → enrich → build (DuckDB) │
    CAISO, EIA)          └──────────────┬──────────────┘
                                        │ writes (rw), atomic swap
                              ┌─────────▼─────────┐
                              │  named volume     │  data:/current/  (read-only artifact)
                              │  site.duckdb,     │  parcels.pmtiles, slope.tif,
                              │  *.pmtiles, …     │  ghi_grid.parquet, manifest.json, _SUCCESS
                              └────┬─────────┬────┘
                          ro mount │         │ ro mount
                       ┌───────────▼──┐   ┌──▼─────────────┐
                       │ api          │   │ web (nginx)    │  :8080
                       │ FastAPI      │◀──│  / → SPA       │
                       │ scoring +    │   │  /api → api    │
                       │ Gemini agent │   │  *.pmtiles     │
                       └──────┬───────┘   └──▲─────────────┘
              live calls:     │              │ serves dist/
        FRED · Census ·       │    ┌─────────┴────────┐
        OpenAI Realtime       │    │ frontend (build) │  vite build → web_dist:
                              ▼    │ React + MapLibre │  `compose run --rm frontend`
                       Gemini API  └──────────────────┘
```

| Service    | Role                                            | Lifecycle              | Reads        | Writes       | UID    |
|------------|-------------------------------------------------|------------------------|--------------|--------------|--------|
| `ingest`   | Offline data pipeline (Service A)               | one-shot (`run --rm`)  | sources      | `data:` (rw) | 10001  |
| `tiles`    | Tippecanoe PMTiles build                        | one-shot (`run --rm`)  | `data:`      | `data:` (rw) | 10001  |
| `api`      | FastAPI scoring engine + LLM agent              | long-running           | `data:` (ro) | —            | 10002  |
| `web`      | nginx: SPA + `/api` proxy + `.pmtiles`          | long-running           | `data:` (ro) | —            | 101    |
| `frontend` | React/MapLibre SPA build                        | build-only (`run --rm`)| sources      | `web_dist:`  | 10003  |

The `api` container is capped at **1 GB / 2 CPU**, `web` at **256 MB / 1 CPU**; both `restart:
unless-stopped` with rotated json-file logs (10 MB × 3). The artifact is laid out as
`data/releases/<build_id>/…` with a `current` symlink swapped via `os.replace()`, and a configurable
retention policy (`KEEP_RELEASES`, default 3) prunes old complete releases while never deleting the
live one.

---

## 3. Data Sources

All inputs are authoritative public datasets. Live fetches can be replaced by pre-staged local files via
`GEO_*_SOURCE` environment overrides, which makes builds fully reproducible and the test suite hermetic.

| Layer (GEO-#) | Provider | Endpoint / item | Format | Geo-blocked? |
|---|---|---|---|---|
| County boundary (3) | U.S. Census TIGER | `www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip` | Shapefile (NAD83) | yes (US) |
| Parcels (4) | Kern County GEODAT | ArcGIS `Assessor_Parcels_Land_2025/FeatureServer/0` (item `31379b8b48ae455ea5972ce02a54cbb8`) | ArcGIS REST GeoJSON | no |
| Zoning / General Plan / Specific Plans (5) | Kern County GIS | `Kern_County_Zoning`, `kc_general_plan`, `specific_plans` FeatureServers | ArcGIS REST GeoJSON | no |
| Transmission lines (6) | HIFLD | `Electric_Power_Transmission_Lines/FeatureServer/0` | ArcGIS REST GeoJSON | yes (US) |
| Substations (6) | HIFLD | `Electric_Substations/FeatureServer/0` | ArcGIS REST GeoJSON | yes (US) |
| Interconnection queue (7) | CAISO (via `gridstatus`) | `CAISO().get_interconnection_queue()` | CSV | yes (US) |
| Flood SFHA (8) | FEMA NFHL | `hazards.fema.gov/arcgis/.../NFHL/MapServer/28` | ArcGIS REST GeoJSON | yes (US) |
| Slope (9) | USGS 3DEP | `seamless-3dep.get_dem(...)` (10/30/60 m) | GeoTIFF | yes (US) |
| Solar GHI (10) | NREL Solar Resource API | `developer.nrel.gov/api/solar/solar_resource/v1.json` | JSON/CSV | yes (US) |
| EIA-860 generators (11, optional) | U.S. EIA | pre-staged CSV | CSV | yes (US) |
| Exclusion overlays (11, optional) | PAD-US / NHD / NLCD | pre-staged GeoJSON | GeoJSON | yes (US) |
| Affordability — price trend | FHFA HPI via **FRED** | series `ATNHPIUS06029A` | JSON API | **no** (global) |
| Affordability — home value | U.S. **Census ACS** 5-yr | variable `B25077_001E` | JSON API | **no** (global) |

> The U.S.-government layers (Census, HIFLD, FEMA, 3DEP, NREL, EIA) reject non-U.S. IPs at the WAF.
> FRED and Census ACS — the only two services called on the *request* path (affordability) — are
> globally reachable and free.

---

## 4. Ingestion Pipeline (Service A)

### 4.1 Fetch

ArcGIS layers are pulled with offset/record pagination (`resultOffset`/`resultRecordCount`, page size
2000, ≤10 000 pages, 3 retries with exponential backoff), with server-side `where` filtering (e.g. SFHA
= `(FLD_ZONE LIKE 'A%' OR FLD_ZONE LIKE 'V%') AND FLD_ZONE <> 'AREA NOT INCLUDED'`) and bounding-box
envelope filtering for national layers. National HIFLD/FEMA layers are clipped to the county. CAISO,
3DEP, and `gridstatus` clients are **lazily imported** so offline/test builds never touch the network.
The NREL client throttles to 1 000 req/hr and caches responses on disk (atomic `.tmp` + `os.replace`).

### 4.2 Parse · clean · reproject

- **CRS policy:** store in **EPSG:4326**; compute distance/area/slope in **EPSG:26911** (UTM 11N), with
  `always_xy := true` on every `ST_Transform`. (Statewide work can use EPSG:3310 CA Albers.)
- **Geometry repair:** `CASE WHEN ST_IsValid(geom) THEN geom ELSE ST_MakeValid(geom) END`; clipped
  collections reduced to their intended dimension via `ST_CollectionExtract` (lines → 2, polygons → 3).
- **APN normalization:** `regexp_replace(upper(trim(apn)), '[^A-Z0-9]', '', 'g')`; APN field auto-resolved
  from a candidate list (`APN`, `APN9`, `ParcelID`, …).
- **Acreage:** computed from metric area, `area_sqm / 4046.8564224`.
- **Voltage cleaning:** sentinels `-999999`/`-999998` and non-positive values nulled.
- **Zoning codes:** leading token extracted before `(` or space (drops lot-size suffixes), uppercased,
  fallback `"OTHER"`.

### 4.3 Spatial enrichment (the join that makes scoring O(1) at request time)

Each parcel is enriched **once, offline** with ~16 derived columns, all computed in EPSG:26911 and stored
back in EPSG:4326:

| Column | Meaning | Method |
|---|---|---|
| `mean_slope_pct` / `_final` | Zonal mean slope (30 m / 10 m) | rasterio burn-in + bincount over the DEM |
| `ghi` | Nearest solar-resource grid point | `arg_min(distance)` over the GHI grid |
| `dist_tx_m`, `dist_sub_m` | Distance to nearest transmission line / substation | `arg_min` aggregate (deterministic tie-break) |
| `nearest_sub_kv` | Voltage of nearest substation | carried from the `arg_min` match |
| `poi_competition_mw/_n` | Active queued MW / project count within 10 km of the matched POI | CAISO queue ↔ substation name-match + radius |
| `sfha_flag` | Inside a FEMA flood zone | `ST_Intersects` with SFHA polygons |
| `zoning_class` | Code of smallest containing zoning polygon | point-in-polygon on the stored centroid |
| `excl_*` | Protected / open-water / built-up overlaps | `EXISTS` against optional exclusion table |

The nearest-neighbor joins use DuckDB's `arg_min({distance, id})` struct aggregate rather than window
functions, which keeps the enrichment **streaming** (one running minimum per parcel per layer) instead
of materializing hundreds of millions of candidate rows — the difference between an OOM kill and a clean
build. A `DUCKDB_MEMORY_LIMIT` forces spill-to-disk under tight container memory. Parcels are
Hilbert-ordered and an R-tree index (`parcels_geom_rtree`) is built afterward for fast request-time
`ST_Intersects` prefiltering.

### 4.4 Zoning rules knowledge base

`ingest/pipeline/data/zoning_rules.csv` is the one **curated, source-controlled** dataset (everything
else is fetched). Columns: `zone_code, zone_name, use_case, permission, basis`. `use_case ∈ {solar,
wind, storage, data_center}`; `permission ∈ {by_right, conditional, prohibited}`. For every distinct
zone code present in the source data, the loader emits one row per use case; any (code, use) pair not
covered by the curated table falls back to the conservative default `conditional` and is flagged
`[CONFIRM]`. The CSV is validated at build time (unknown permissions/use-cases or duplicates fail loud).

### 4.5 Build & publish

The harness builds into `.staging.<build_id>.<pid>`, runs fetchers in dependency order (county first),
assembles + enriches, writes `manifest.json` and a `_SUCCESS` marker **last**, then promotes via atomic
rename and swaps the `current` symlink. Parcel vector tiles are produced independently by **tippecanoe**
(`-Z8 -z16 --simplification 10 --drop-densest-as-needed`, attributes `id, apn, acres`) into
`parcels.pmtiles`. **Pinned tools:** `duckdb==1.1.3` (spatial + httpfs extensions; pinned for reader
ABI parity), `pyproj`, `rasterio`, `numpy`, `httpx`, `gridstatus`, `seamless-3dep`, plus the native
`tippecanoe` binary.

---

## 5. The Scoring Model

A **two-stage** model: hard exclusions, then a weighted-sum suitability score on the survivors.

### 5.1 Stage A — hard exclusions

A parcel is dropped (NULL-safe — *unknown never excludes*) if any of: `acres < min_acres`;
`mean_slope_pct > max_slope_pct`; `sfha_flag` (when `exclude_sfha`); `zoning_class ∈ prohibited`; or it
overlaps an optional protected/water/built-up overlay (when enabled).

### 5.2 Stage B — weighted suitability (0–100)

Seven factors, each linearly normalized to [0, 1] over a domain (clamped), oriented by direction, then
combined: `score = 100 · Σ wᵢ · normᵢ`. Unknown factors impute the neutral value **0.5**.

| Factor | Column | Direction | Domain | Unit | Utility-Solar w | Data-Center w |
|---|---|---|---|---|---|---|
| GHI | `ghi` | higher | [4.5, 6.5] | kWh/m²/day | **0.25** | 0.00 |
| Slope | `mean_slope_pct` | lower | [0, 15] | % | **0.20** | 0.15 |
| TX distance | `dist_tx_m` | lower | [0, 20 000] | m | **0.20** | **0.25** |
| Substation distance | `dist_sub_m` | lower | [0, 20 000] | m | 0.15 | 0.20 |
| Substation capacity | `nearest_sub_kv` | higher | [0, 500] | kV | 0.05 | 0.20 |
| Acreage | `acres` | higher | [0, 640] | acres | 0.15 | 0.10 |
| Competition | `poi_competition_mw` | lower | [0, 2000] | MW | 0.00 | 0.10 |
| **Stage-A:** `min_acres` / `max_slope_pct` | | | | | 20 ac / 15 % | 5 ac / 15 % |

Caller-supplied partial weight overrides are re-normalized to sum 1.0. The composite is computed
**unrounded in SQL** (so ranking reflects true suitability) and rounded to one decimal only in the final
projection. Because all factor inputs were precomputed in metric CRS during ingest, the request path runs
**no `ST_Transform`** — it intersects the drawn polygon against the R-tree, scores the candidates, applies
Stage-A, orders by raw score, and serializes geometry only for the returned page.

### 5.3 Affordability blend (optional)

A county-level affordability signal (Census ACS median home value, normalized over [\$150k, \$600k] so
*cheaper = higher*; FHFA HPI year-over-year trend) is blended **order-preservingly** post-query:
`blended = (1 − w)·score + w·100·affordability`, default `w = 0.12`. Bands: ≥0.6 affordable, ≥0.35
moderate, else expensive.

### 5.4 Score → color

Scores map to a **10-stop viridis ramp** (`#440154` → `#fde725`), interpolated in linear sRGB, with label
color flipped black/white by WCAG contrast. Viridis is colorblind-safe and monotonic in lightness; score
is **never conveyed by color alone** (ranked list + numeric chip + rank accompany it).

---

## 6. API Layer

FastAPI 0.115 / Uvicorn 0.34 / DuckDB 1.1.3 on Python ≥3.12. The artifact opens **once, read-only** at
startup (tolerant: the app boots even if it's absent); each request gets a fresh cursor and blocking
queries run in a threadpool. Idempotent reads carry weak ETags + gzip; `/api/score` results are served
from a 256-entry LRU (`X-Cache: HIT|MISS`).

| Endpoint | Purpose |
|---|---|
| `POST /api/score` | Rank parcels in a drawn polygon → GeoJSON FeatureCollection (score, rank, factors, meta) |
| `GET /api/explain/{parcel_id}` | Per-factor breakdown: raw, normalized, weight, contribution, exclusions |
| `GET /api/layer/{transmission\|substations\|flood}` | Static overlay GeoJSON |
| `GET /api/context` | CAISO Kern interconnection-queue summary |
| `GET /api/affordability` | Live county affordability signal (FRED + Census) |
| `POST /api/agent` | Streaming agent (SSE) |
| `POST /api/realtime/session` | Mint ephemeral OpenAI Realtime secret for voice |
| `GET /api/health` | Liveness/readiness (artifact + spatial extension) |

---

## 7. The AI Assistant

### 7.1 Framework

Built on **Pydantic AI**. Default model **`google:gemini-3.5-flash`**, selected at request time from
`AGENT_MODEL` — switch to `anthropic:…` or `openai:…` with no code change. The agent is cached per model
id (`lru_cache`), built with `defer_model_check=True` (importable without keys), `retries=1`,
`parallel_tool_calls=False`, and provider-specific thinking minimization (Gemini 3.x `thinking_level:
LOW`).

### 7.2 Two modes

- **Text (`POST /api/agent`, SSE).** Events: `step` (tool start), `token` (narration), `result`
  (structured tool output for the UI), `error`, `done`. The ranked FeatureCollection is captured from the
  `score_parcels` tool *result event* — never parsed from model text — so the UI always renders exact
  engine output. The run is wrapped in `asyncio.timeout`, guarded by a process-wide semaphore, and
  watches for client disconnect; failures surface as an `error` event, never a 500.
- **Voice (OpenAI Realtime over WebRTC, GEO-40).** `/api/realtime/session` mints a short-lived ephemeral
  secret server-side (the real `OPENAI_API_KEY` never reaches the browser and is never logged); the
  browser then negotiates SDP directly with OpenAI and streams audio peer-to-peer (no audio touches our
  backend). Default `gpt-realtime` / voice `marin`. Absent key → tidy disabled mic.

### 7.3 Tools (parity-enforced)

A single `REGISTRY` is the source of truth; a test (GEO-42) asserts the live Gemini tool schema, the
voice `voiceTools.json`, and the TypeScript executor union all agree (names, params, required sets,
enums) — **every tool is available to both text and voice**.

| Tool | Key params | Effect |
|---|---|---|
| `resolve_area` | `text` | Place/bbox/point → opaque `area_ref` token |
| `score_parcels` | `area_ref`, `use_case`, `min_acres?`, `max_slope_pct?`, `limit?`, `affordability_score?` | Rank parcels → FeatureCollection |
| `check_affordability` | `area_ref`, `use_case` | Live FRED+Census affordability signal (the one request-path network call) |
| `explain_parcel` | `parcel_id`, `use_case` | Per-factor breakdown |
| `grid_context` | — | CAISO queue summary |
| `focus_parcel` | `parcel_id` | Fly map to + select a parcel |
| `export_pdf` | `parcel_ids` | Generate downloadable PDF report |
| `set_map_view` | `show?`, `hide?`, `basemap?` | Toggle layers / switch basemap |
| `zoom_map` | `direction`, `percent` | Relative zoom by % |
| *(voice composites)* `find_sites`, `focus_map` | `place`, `use_case?` | One-shot resolve+score / resolve+fly |

### 7.4 Safety caps & knowledge base

`AGENT_TIMEOUT_S=60`, `AGENT_MAX_MESSAGE_CHARS=4000`, `AGENT_MAX_CONCURRENCY=4` — mirrored by nginx
limits on `/api/agent` (1 r/s, burst 3, max 2 concurrent SSE, 256 k body, 300 s proxy timeout) to protect
the metered key. The system prompt bakes in the domain: *"a site-selection assistant for renewable-energy
projects in Kern County… you NEVER invent geometry, coordinates, parcel ids, or suitability scores"*,
plus the use cases and weights, the Stage-A exclusions, the layer color legend, the tool etiquette
(resolve→score), and explicit data provenance (GEODAT parcels/zoning, 3DEP slope, NREL GHI, HIFLD
transmission/substations, FEMA flood, CAISO queue, FHFA+Census affordability).

---

## 8. Frontend & Infrastructure

**SPA:** React 18 + Vite 6 + TypeScript 5, **MapLibre GL 4** with a **deck.gl 9** `GeoJsonLayer` for the
score overlay, **terra-draw** for AOI drawing (undo/redo), **PMTiles 4** byte-range parcel tiles, Zustand
map store, and URL-hash share-state (`#s=`). Basemaps: Mapbox Light/Dark when `MAPBOX_TOKEN` is set, else
token-less CARTO/Esri. Layout is a resizable 3-pane desktop shell (docked closable assistant · map ·
results, with a parcel-detail takeover) and a non-modal bottom-sheet on mobile, light/dark themed. The
design system (`docs/DESIGN-SYSTEM.md`) fixes an **azure** chrome accent (kept off the viridis score
ramp to avoid collision), Inter/JetBrains-Mono variable fonts, lucide icons via a single `Icon` wrapper,
and a never-hardcoded `scoreTextColor`.

**nginx:** serves the SPA (history fallback), reverse-proxies `/api`, range-serves `*.pmtiles`
(immutable cache, permissive CORS), hides the rest of `/data`, and enforces per-IP rate/connection
limits plus a CSP tuned for MapLibre/deck.gl + the Mapbox/CARTO/Esri/OpenAI origins. **TLS edge:**
optional Caddy for auto-HTTPS + HSTS + HTTP→HTTPS (HSTS only at the edge). **CI/CD:** GitHub Actions runs
API tests + frontend build + a docker build-proof on every push/PR; deploy builds/pushes images to GHCR
and SSH-rolls the host, gated behind a `production` environment. All containers run **non-root**; secrets
live only in `.env` (git-ignored, never baked into an image).

---

## 9. Scalability

The reference build is Kern County, but the architecture is deliberately **county-agnostic and
horizontally extensible**. Scaling proceeds along three axes:

### 9.1 More geography (same data shape)

Geographic identity is isolated to a handful of constants (`KERN_STATE_FIPS`, `KERN_COUNTY_FIPS`,
`KERN_GEOID`) and the per-layer source URLs/overrides; the metric CRS is the only Kern-specific compute
choice. To add a county:

1. Point the county-boundary fetcher at the target FIPS (the Census TIGER file is already national).
2. Swap the parcels/zoning FeatureServer URLs (or pre-stage local files) and confirm the APN/zone fields.
3. Choose the correct **metric CRS** for the region (e.g. another UTM zone, or EPSG:3310 statewide).
4. Extend `zoning_rules.csv` with that jurisdiction's districts (uncovered codes auto-default to
   conservative `conditional`).

The national layers (HIFLD transmission/substations, FEMA flood, 3DEP, NREL) need **no change** — they
are already country-wide and merely clipped to the active boundary. Because each build is an immutable,
versioned release, **a state could be served as N county artifacts or one merged multi-county artifact**;
DuckDB + PMTiles handle tens of millions of parcels with the same streaming-enrichment and Hilbert/R-tree
strategy already in place. The same model scales to **all U.S. counties and states**.

### 9.2 More data (pluggable pipelines)

The ingestion layer is a registry of independent fetchers that each emit a GeoParquet/DuckDB table and a
manifest entry. **New signals are added by writing a new fetcher + (optionally) a new scoring factor** —
e.g. land cost rasters, wind resource, water rights, fiber/network proximity, wildfire risk, grid
congestion prices, or protected-species habitat. Because scoring is a weighted sum over normalized
factors with declared domains and directions, a new factor is a few lines (column, domain, direction,
weight) and the assistant's parity test keeps every surface in sync.

### 9.3 Beyond the U.S.

Nothing in the engine is U.S.-specific; only the *sources* are. Any country with open parcel/cadastre,
zoning, transmission, terrain (e.g. Copernicus DEM), solar (e.g. Global Solar Atlas), and flood data can
be onboarded by building source-appropriate fetchers that normalize into the same table shape and
choosing the local metric CRS. The affordability blend would swap FRED/Census for a local price index.
**The pipeline is the integration surface — add a parser, inject a layer, declare a factor.**

> Because the U.S. government APIs geo-block non-U.S. IPs, building the U.S. reference dataset from
> outside the U.S. requires a U.S. egress (VPN/host). International datasets are typically not so
> restricted.

---

## 10. Coordinate Reference System Policy

Load-bearing — see [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md):

- **Store & serve** in **EPSG:4326** (lon/lat WGS84).
- **Compute** distance/area/slope in a **projected metric CRS**: **EPSG:26911** (UTM 11N) locally, or
  **EPSG:3310** (CA Albers) statewide.
- **Every `ST_Transform` passes `always_xy := true`** — DuckDB spatial and GeoJSON are lon/lat (x,y)
  ordered; relying on an authority's declared axis order silently swaps coordinates.
- Distances in meters, areas internally in m² (acres/hectares at the boundary), slope as percent grade
  (never degrees).

---

## 11. Quick Start

```bash
cp .env.example .env          # fill in keys (GOOGLE_API_KEY, NREL_API_KEY, …); never commit .env
make build                    # build all service images
make ingest                   # one-shot pipeline → data:/current   (needs U.S. egress; see warning above)
make tiles                    # parcels.geojson → parcels.pmtiles (tippecanoe bundled in the ingest image)
make up                       # start api + web → http://localhost:8080
```

The map degrades gracefully if `parcels.pmtiles` is absent (the base parcel layer just doesn't render);
scoring, the agent, and the transmission/substation/flood overlays don't depend on it. See the
[`Makefile`](Makefile) for all entrypoints and the deploy helpers (`tls-up`, `verify`, `ci`, …).

For production hardening (Caddy TLS edge, host firewall/SSH, CI/CD), see
[`docs/GEO-36-host-security.md`](docs/GEO-36-host-security.md),
[`docs/GEO-37-runtime-protection.md`](docs/GEO-37-runtime-protection.md), and
[`docs/GEO-38-cicd-cdn.md`](docs/GEO-38-cicd-cdn.md).

---

## 12. Repository Layout

```
geo-energy/
├── docker-compose.yml        # 4 services + named volumes (data:, web_dist:)
├── .env.example              # secret/config template (real .env is git-ignored)
├── Makefile                  # build / ingest / tiles / up entrypoints + deploy helpers
├── docs/CONVENTIONS.md       # CRS, units, artifact layout, naming
├── docs/DESIGN-SYSTEM.md     # color/type/spacing tokens, map design, a11y
├── ingest/                   # Service A — offline pipeline (fetchers, enrichment, zoning rules)
├── api/                      # FastAPI scoring engine + Pydantic-AI agent + realtime voice
├── web/                      # nginx reverse proxy + static host + rate limits + CSP
├── frontend/                 # React + MapLibre + deck.gl SPA
└── deploy/                   # Caddy TLS edge, firewall/SSH hardening scripts
```

---

## License & Data Attribution

Open source. Data remains the property of its providers — U.S. Census Bureau, Kern County GEODAT, HIFLD,
CAISO, FEMA, USGS, NREL, EIA, FHFA, and basemap providers (Mapbox/CARTO/Esri) — and is subject to their
respective terms. This project is a research/decision-support tool; suitability scores are screening
signals, not engineering, legal, or land-use determinations.
