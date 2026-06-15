# Site-Selection App — Kern County, CA (GEOID 06029)

Renewable / data-center site-selection app for Kern County. An **offline ingestion
job** builds a **read-only DuckDB artifact + PMTiles** from public data sources; a
**FastAPI scoring engine** serves polygon scoring; an **Anthropic agent** drives a
natural-language workflow; a **React / MapLibre SPA** is the UI. Everything ships via
`docker compose` behind nginx.

## Architecture (4 services + 1 shared artifact)

```
                         ┌─────────────────────────────┐
   public data sources ─▶│  ingest  (Service A)        │  one-shot, offline
   (NREL, county GIS,    │  fetch → clean → reproject  │  `compose run --rm ingest`
    DEM, etc.)           │       → build (DuckDB)      │
                         └──────────────┬──────────────┘
                                        │ writes
                              ┌─────────▼─────────┐
                              │  named volume     │  data:/data   (read-only artifact)
                              │  /data/current/   │  site.duckdb, *.pmtiles, slope.tif…
                              └────┬─────────┬────┘
                          ro mount │         │ ro mount
                       ┌───────────▼──┐   ┌──▼─────────────┐
                       │ api          │   │ web (nginx)    │  :8080
                       │ FastAPI      │◀──│  / → SPA       │
                       │ scoring +    │   │  /api → api    │
                       │ agent        │   │  *.pmtiles     │
                       └──────────────┘   └──▲─────────────┘
                                             │ serves dist/
                                   ┌─────────┴────────┐
                                   │ frontend (build) │  vite build → web_dist:
                                   │ React + MapLibre │  `compose run --rm frontend`
                                   └──────────────────┘
```

| Service    | Role                                   | Lifecycle              | Reads        | Writes       |
|------------|----------------------------------------|------------------------|--------------|--------------|
| `ingest`   | Offline data pipeline (Service A)      | one-shot (`run --rm`)  | sources      | `data:` (rw) |
| `api`      | FastAPI scoring engine + agent         | long-running           | `data:` (ro) | —            |
| `web`      | nginx: SPA + `/api` proxy + `.pmtiles` | long-running           | `data:` (ro) | —            |
| `frontend` | React/MapLibre SPA build               | build-only (`run --rm`)| sources      | `web_dist:`  |

The **ingest job never runs on the request path** (FR-A5). It builds into a temp release
directory and **atomically swaps** `data:/current` so readers always see a complete
artifact (FR-A1).

## Coordinate Reference System (CRS) policy

This is load-bearing — see [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md). In short:

- **Store & serve in EPSG:4326** (lon/lat WGS84).
- **Compute** distance / area / slope in a **projected metric CRS**: **EPSG:26911**
  (UTM 11N — local accuracy) or **EPSG:3310** (CA Albers — statewide).
- **Every `ST_Transform` passes `always_xy := true`** (DuckDB spatial uses lon/lat axis
  order; never rely on the authority's default axis order).

## Quick start

```bash
cp .env.example .env          # then fill in API keys (never commit .env)
make build                    # build all service images
make ingest                   # run the one-shot ingestion job → data:/current
make up                       # start api + web (SPA on http://localhost:8080)
```

After `make ingest`, generate the parcel vector tiles (needs `tippecanoe` on PATH):

```bash
make tiles                    # parcels.geojson → parcels.pmtiles in data:/current (GEO-14)
```

See [`Makefile`](Makefile) for all entrypoints (`build`, `ingest`, `frontend`, `tiles`,
`up`, `down`, `logs`, `config`, `ps`).

## Repository layout

```
geo-energy/
├── docker-compose.yml        # 4 services + named volumes (data:, web_dist:)
├── .env.example              # secret/config template (real .env is git-ignored)
├── Makefile                  # build / ingest / up entrypoints
├── docs/CONVENTIONS.md       # CRS, units, artifact layout, naming conventions
├── ingest/                   # Service A — offline pipeline (GEO-2+)
├── api/                      # FastAPI scoring engine + agent (GEO-15+)
├── web/                      # nginx reverse proxy + static host (GEO-34)
└── frontend/                 # React + MapLibre SPA (GEO-22)
```

## Status

- **`ingest/`** — real pipeline (GEO-2). Layers: county boundary (GEO-3), parcels (GEO-4),
  zoning (GEO-5), HIFLD transmission/substations (GEO-6), CAISO queue + POI (GEO-7), FEMA
  flood SFHA (GEO-8), **3DEP slope raster (GEO-9)**, **NREL solar GHI grid (GEO-10)**, and
  **optional EIA-860 generators + Stage-A exclusion overlays (GEO-11)**. **Parcel PMTiles via
  tippecanoe (GEO-14)** — `make tiles`. (US-gov sources — 3DEP/NREL/EIA, like FEMA/CAISO —
  geo-block non-US IPs; live ingest of those needs a US egress. Offline runs use pre-staged
  `GEO_*_SOURCE` files; the test suite is fully hermetic.)
- **`api/`** — real FastAPI skeleton (GEO-15): opens `data:/current/site.duckdb` read-only at
  startup, per-request cursor, `run_in_threadpool`, `GET /api/health`. Scoring/agent are GEO-16+.
- **`frontend/`** — real React + Vite + MapLibre SPA scaffold (GEO-22): PMTiles parcels layer,
  responsive 3-pane/​bottom-sheet shell, light/dark theming. Score rendering (deck.gl) is GEO-24.
- **`web/`** — still a placeholder nginx skeleton; production proxy + `.pmtiles` byte-range
  serving is GEO-34. Production Dockerfiles/hardening are GEO-33.
