"""Ingestion configuration: CRS policy (static) + runtime settings (from env).

See docs/CONVENTIONS.md. Runtime values are read from the environment via `from_env()`
so the harness and tests can also construct an explicit `Settings` for isolation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ── CRS policy (static; never environment-dependent) ───────────────────────────
CRS_STORAGE = 4326          # WGS84 lon/lat — store & serve
CRS_METRIC_UTM = 26911      # UTM 11N (m) — local metric compute (Kern County)
CRS_METRIC_ALBERS = 3310    # CA Albers (m) — statewide metric compute
DEFAULT_METRIC_CRS = CRS_METRIC_UTM

# ── Artifact layout (relative to the data volume) ──────────────────────────────
ARTIFACT_NAME = "site.duckdb"
SUCCESS_MARKER = "_SUCCESS"
MANIFEST_NAME = "manifest.json"
RELEASES_SUBDIR = "releases"
CURRENT_SUBDIR = "current"

# ── Study area: Kern County, CA (fixed project scope) ──────────────────────────
KERN_STATE_FIPS = "06"      # California
KERN_COUNTY_FIPS = "029"    # Kern
KERN_GEOID = "06029"        # STATEFP + COUNTYFP

# Exact survey acre: 1 acre = 4046.8564224 m² (international acre).
SQ_METERS_PER_ACRE = 4046.8564224

# ── Layer sources (GEO-3 county boundary, GEO-4 parcels) ───────────────────────
# Network is used only during ingest, never on the request path (FR-A5). Any source can
# be fed a pre-staged local file via its *_SOURCE env var (offline/air-gapped runs and
# tests); otherwise it is downloaded from the URLs below (also env-overridable).

# County boundary — the Census cartographic boundary file that pygris wraps. We fetch it
# directly (DuckDB ST_Read via GDAL) to keep the image slim (no geopandas/pygris stack).
COUNTY_CB_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"
COUNTY_CB_SHP = "cb_2023_us_county_500k.shp"  # member to read inside the zip (/vsizip/)
COUNTY_CB_CRS = 4269                          # Census ships NAD83; we reproject to 4326
COUNTY_URL_ENV = "GEO_COUNTY_URL"             # override the download URL
COUNTY_SOURCE_ENV = "GEO_COUNTY_SOURCE"       # pre-staged local file (assumed 4326)
COUNTY_SOURCE_CRS_ENV = "GEO_COUNTY_SOURCE_CRS"

# Parcels — GEODAT "Assessor Parcels Land 2025" primary, optional Shafter mirror fallback.
PARCELS_GEODAT_ITEM = "31379b8b48ae455ea5972ce02a54cbb8"  # GEODAT item id (for ops reference)
# Confirmed 2026-06-15: KernGIS hosted Feature Service, access=public (token-free), 421,684
# polygon features, maxRecordCount=2000, supportsPagination, serves f=geojson in EPSG:4326.
# APN attribute is "APN" (also "APN9" = 9-digit no-dash, "APN_LABEL"); we still compute
# acreage from geometry per spec rather than trusting the service's SHAPE_ACRE.
PARCELS_GEODAT_URL = (
    "https://services5.arcgis.com/Y8jwjGUWbRjuqpG5/arcgis/rest/services/"
    "Assessor_Parcels_Land_2025/FeatureServer/0"
)
PARCELS_SHAFTER_URL = ""  # optional Shafter mirror fallback; set GEO_PARCELS_SHAFTER_URL if needed
PARCELS_GEODAT_URL_ENV = "GEO_PARCELS_GEODAT_URL"
PARCELS_SHAFTER_URL_ENV = "GEO_PARCELS_SHAFTER_URL"
PARCELS_SOURCE_ENV = "GEO_PARCELS_SOURCE"        # pre-staged local file (GeoJSON, 4326)
PARCELS_SOURCE_CRS_ENV = "GEO_PARCELS_SOURCE_CRS"
PARCELS_APN_FIELD_ENV = "GEO_PARCELS_APN_FIELD"  # force the APN attribute name
# Candidate APN attribute names, tried case-insensitively in order (GEODAT uses "APN").
PARCELS_APN_FIELDS = ("APN", "APN9", "ParcelID", "PARCEL_ID", "PARCELID", "APN_LABEL", "AIN")

# ── Transmission lines + substations (GEO-6, HIFLD national layers) ─────────────
# These are national datasets, so we prefilter server-side to the county bbox (the ArcGIS
# envelope filter, arcgis.py) and then clip precisely to the county polygon in DuckDB.
# Both confirmed public / token-free 2026-06-15 and served as f=geojson in EPSG:4326.
# Voltage uses HIFLD's documented "not available" sentinel (-999999); the transmission
# re-host is already clean (100-1000 kV) while the substations re-host encodes unknown as
# 0, so we null out both the sentinel and any non-positive voltage (0 kV is not a real value).
TRANSMISSION_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/"
    "Electric_Power_Transmission_Lines/FeatureServer/0"
)
SUBSTATIONS_URL = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "Electric_Substations/FeatureServer/0"
)
TRANSMISSION_URL_ENV = "GEO_TRANSMISSION_URL"
SUBSTATIONS_URL_ENV = "GEO_SUBSTATIONS_URL"
TRANSMISSION_SOURCE_ENV = "GEO_TRANSMISSION_SOURCE"   # pre-staged local file (GeoJSON, 4326)
SUBSTATIONS_SOURCE_ENV = "GEO_SUBSTATIONS_SOURCE"
TRANSMISSION_SOURCE_CRS_ENV = "GEO_TRANSMISSION_SOURCE_CRS"
SUBSTATIONS_SOURCE_CRS_ENV = "GEO_SUBSTATIONS_SOURCE_CRS"
# Voltage attribute candidates (resolved case-insensitively, first match wins).
TRANSMISSION_VOLTAGE_FIELDS = ("VOLTAGE", "VOLT_KV", "KV")
SUBSTATIONS_VOLTAGE_FIELDS = ("MAX_VOLT", "MAX_VOLTAG", "VOLTAGE", "MAX_KV")
SUBSTATIONS_MIN_VOLTAGE_FIELDS = ("MIN_VOLT", "MIN_VOLTAG", "MIN_KV")
# HIFLD "not available" sentinels for numeric attributes (nulled along with v <= 0).
VOLTAGE_NULL_SENTINELS = (-999999, -999998)

# ── Zoning / land-use (GEO-5, Kern County GEODAT) ──────────────────────────────
# Kern County's own GIS layers (already county-scoped, so no clip needed). Primary
# zoning-district code field is `Zn_Cd1` (confirmed 2026-06-15); general plan carries the
# land-use designation in `GP_DESIG`/`LU_DESC`; specific plans carry a plan name.
ZONING_URL = (
    "https://services5.arcgis.com/Y8jwjGUWbRjuqpG5/arcgis/rest/services/"
    "Kern_County_Zoning/FeatureServer/0"
)
GENERAL_PLAN_URL = (
    "https://services5.arcgis.com/Y8jwjGUWbRjuqpG5/arcgis/rest/services/"
    "kc_general_plan/FeatureServer/0"
)
SPECIFIC_PLANS_URL = (
    "https://services5.arcgis.com/Y8jwjGUWbRjuqpG5/arcgis/rest/services/"
    "specific_plans/FeatureServer/0"
)
ZONING_URL_ENV = "GEO_ZONING_URL"
GENERAL_PLAN_URL_ENV = "GEO_GENERAL_PLAN_URL"
SPECIFIC_PLANS_URL_ENV = "GEO_SPECIFIC_PLANS_URL"
ZONING_SOURCE_ENV = "GEO_ZONING_SOURCE"               # pre-staged local file (GeoJSON, 4326)
GENERAL_PLAN_SOURCE_ENV = "GEO_GENERAL_PLAN_SOURCE"
SPECIFIC_PLANS_SOURCE_ENV = "GEO_SPECIFIC_PLANS_SOURCE"
ZONING_SOURCE_CRS_ENV = "GEO_ZONING_SOURCE_CRS"
GENERAL_PLAN_SOURCE_CRS_ENV = "GEO_GENERAL_PLAN_SOURCE_CRS"
SPECIFIC_PLANS_SOURCE_CRS_ENV = "GEO_SPECIFIC_PLANS_SOURCE_CRS"
ZONING_CODE_FIELDS = ("Zn_Cd1", "ZONE", "ZONING", "ZONE_CODE", "ZONECODE", "ZONE_CD")
ZONING_DESC_FIELDS = ("Dscrptn", "DESCRIPTION", "ZONE_DESC", "DESC")
ZONING_COMBINED_FIELDS = ("Comb_Zn", "COMBINED", "COMB_ZONE")
GENERAL_PLAN_DESIG_FIELDS = ("GP_DESIG", "DESIG", "GP_CODE", "GPDESIG")
GENERAL_PLAN_LU_FIELDS = ("LU_DESC", "LU_DESCRIP", "LANDUSE", "DESCRIPTION")
SPECIFIC_PLANS_NAME_FIELDS = ("SP_NAME_1", "SP_NAME", "NAME", "PLAN_NAME")
# Curated code → permission lookup (FR-A2). Checked into the repo, emitted to each build,
# and validated for coverage against the zoning codes actually present in the data.
ZONING_RULES_CSV = "zoning_rules.csv"
ZONING_USE_CASES = ("solar", "wind", "storage", "data_center")
ZONING_PERMISSIONS = ("by_right", "conditional", "prohibited")
# Safe default when a zone code has no curated rule (degrade to "needs review", never
# silently by-right). Surfaced as a warning by the fetcher.
ZONING_DEFAULT_PERMISSION = "conditional"

# ── Flood SFHA (GEO-8, FEMA National Flood Hazard Layer) ───────────────────────
# NFHL "Flood Hazard Zones" polygon layer (S_FLD_HAZ_AR), national, so prefiltered
# server-side to the county bbox + the SFHA where-clause, then clipped precisely to the
# county polygon in DuckDB. Special Flood Hazard Areas are FLD_ZONE A%/V% (the SFHA_TF='T'
# rows); these drive the parcel `sfha_flag` (a Stage-A exclusion) in enrichment (GEO-13).
# Confirmed live 2026-06-15 (from a US egress; the host geo-blocks non-US IPs at its WAF):
# layer 28 = "Flood Hazard Zones" (polygon), maxRecordCount=2000, fields FLD_ZONE/ZONE_SUBTY/
# SFHA_TF/FLD_AR_ID all present. NB the public REST root is /arcgis/rest/services, NOT
# /gis/nfhl/rest/services (the latter 404s via FEMA's WebSEAL gateway). Endpoint
# env-overridable; a pre-staged local file (GeoJSON, 4326) can be supplied via
# GEO_FLOOD_SOURCE for offline/air-gapped runs and tests.
FLOOD_NFHL_URL = (
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
)
FLOOD_URL_ENV = "GEO_FLOOD_URL"
FLOOD_SOURCE_ENV = "GEO_FLOOD_SOURCE"               # pre-staged local file (GeoJSON, 4326)
FLOOD_SOURCE_CRS_ENV = "GEO_FLOOD_SOURCE_CRS"
FLOOD_ZONE_FIELDS = ("FLD_ZONE", "ZONE", "FLD_ZONE_1", "FLDZONE")
FLOOD_SUBTYPE_FIELDS = ("ZONE_SUBTY", "ZONE_SUBTYPE", "SUBTYPE")
FLOOD_SFHA_TF_FIELDS = ("SFHA_TF", "SFHA")
FLOOD_ID_FIELDS = ("FLD_AR_ID", "OBJECTID", "OBJECTID_1", "DFIRM_ID", "GFID")
# SFHA selection: FEMA defines Special Flood Hazard Areas as zones beginning A or V — EXCEPT
# the FLD_ZONE value 'AREA NOT INCLUDED' (an unmapped area, NOT an SFHA, SFHA_TF='F'), which
# the bare A%/V% prefix would wrongly catch. Pushed server-side (fewer features pulled) AND
# re-applied in DuckDB (so the local-override path, which reads the whole file, filters
# identically); the DuckDB pass additionally honours SFHA_TF='F' as authoritative-non-SFHA.
FLOOD_ANI_VALUE = "AREA NOT INCLUDED"  # FLD_ZONE sentinel that is A%-prefixed but not SFHA
FLOOD_SFHA_WHERE = (
    "(FLD_ZONE LIKE 'A%' OR FLD_ZONE LIKE 'V%') AND FLD_ZONE <> 'AREA NOT INCLUDED'"
)

# ── CAISO interconnection queue + POI geolocation (GEO-7) ──────────────────────
# `gridstatus.CAISO().get_interconnection_queue()` returns the published CAISO queue as a
# pandas DataFrame (~2,274 rows). We materialize it to CSV (so the network path and the
# local-override path share one DuckDB read path), filter to Kern County, geolocate each
# project's POI by name-matching to the HIFLD substations (GEO-6) — which also supplies the
# POI voltage (the queue itself carries no voltage column) — and precompute POI competition.
# Standardized gridstatus column names (confirmed against the gridstatus CAISO source):
# raw "Station or Transmission Line" → "Interconnection Location" (POI), "Utility" →
# "Transmission Owner" (PTO), "Net MWs to Grid" → "Capacity (MW)". A pre-staged CSV can be
# supplied via GEO_CAISO_QUEUE_SOURCE for offline/air-gapped runs and tests (gridstatus is
# imported lazily only on the live path, so the suite needs neither the library nor network).
CAISO_QUEUE_SOURCE_ENV = "GEO_CAISO_QUEUE_SOURCE"   # pre-staged CSV (gridstatus schema)
# Candidate column names (resolved case-insensitively, first match wins) — robust to
# gridstatus version drift and to a raw-export CSV that skipped standardization.
CAISO_QUEUE_ID_FIELDS = ("Queue ID", "Queue Position", "queue_id")
CAISO_NAME_FIELDS = ("Project Name", "Project Name - Confidential", "name")
CAISO_TYPE_FIELDS = ("Generation Type", "Type-1", "type")
CAISO_FUEL_FIELDS = ("Fuel-1", "Fuel", "fuel")
CAISO_STATUS_FIELDS = ("Status", "Application Status", "status")
CAISO_MW_FIELDS = ("Capacity (MW)", "Net MWs to Grid", "MW-1", "mw")
CAISO_COUNTY_FIELDS = ("County", "county")
CAISO_STATE_FIELDS = ("State", "state")
CAISO_PTO_FIELDS = ("Transmission Owner", "Utility", "PTO")
CAISO_POI_FIELDS = (
    "Interconnection Location", "Station or Transmission Line",
    "Point of Interconnection", "POI",
)
# The queue has no voltage column; this is a defensive fallback if a source ever adds one.
# Otherwise POI voltage is inherited from the matched substation.
CAISO_POI_VOLTAGE_FIELDS = ("Voltage (kV)", "POI Voltage (kV)", "Voltage", "kV")
CAISO_QUEUE_DATE_FIELDS = ("Queue Date", "queue_date")
CAISO_COMPLETION_FIELDS = ("Proposed Completion Date", "Current On-line Date")
# County the app scopes to (the CAISO `County` column is a plain string).
KERN_COUNTY_NAME = "Kern"
# A queue project is "active" (counts toward competition) unless its status matches one of
# these (case-insensitive substring) terminal/withdrawn states.
CAISO_INACTIVE_STATUS_PATTERNS = (
    "withdraw", "complete", "in service", "operational", "suspend", "deactiv", "cancel",
)
# POI competition is aggregated within this radius (meters, computed in EPSG:26911) of each
# POI point: queued MW stacked on the same/nearby grid injection point.
POI_COMPETITION_RADIUS_M = 10_000.0
# Minimum normalized-name length for a *containment* (non-exact) POI↔substation match, so a
# short generic token (e.g. a 1–3 char station name) can't false-match many unrelated POIs.
# Exact matches are always allowed regardless of length.
POI_MATCH_MIN_TOKEN_LEN = 4

# ── Slope / terrain (GEO-9, USGS 3DEP DEM → slope raster) ──────────────────────
# DEM → slope (percent grade) raster. The DEM is acquired over the county bbox via the
# `seamless-3dep` package (`get_dem(bbox, save_dir, res=10|30|60)` → GeoTIFF tiles in
# EPSG:4326; lazy-imported only on the live path, like gridstatus). It is then reprojected
# to the metric CRS (EPSG:26911) and slope is computed THERE (CONVENTIONS §2: slope is a
# metric quantity — compute from the DEM in 26911, never in degrees), clipped to the county
# polygon, and written as a single-band float32 GeoTIFF sidecar in the release dir. A small
# `slope_raster` metadata table (one row per emitted raster: role/resolution/path/profile)
# is created in the artifact so the manifest/reader contract (one table per fetcher) holds
# and GEO-13 enrichment can locate the raster to sample per-parcel zonal slope.
#
# Two-resolution policy (review C11): 30 m is the broad SCREENING pass (county-wide,
# always emitted as slope.tif). 10 m is the FINAL pass used to re-evaluate top candidates;
# the candidate set is not known at ingest time, so the 10 m raster is emitted ONLY for an
# explicit area-of-interest supplied via GEO_SLOPE_FINAL_AOI ("west,south,east,north" in
# 4326) — otherwise the same code path is reused later (enrichment/scoring) per candidate.
#
# Live 3DEP fetch is a US-gov source and geo-blocks non-US IPs (see the substations/flood
# notes); validate the live path from a US egress. The offline/test path reads a pre-staged
# DEM GeoTIFF from GEO_DEM_SOURCE (no network, no seamless-3dep needed).
DEM_SOURCE_ENV = "GEO_DEM_SOURCE"            # pre-staged DEM GeoTIFF (CRS read from the file)
DEM_SOURCE_CRS_ENV = "GEO_DEM_SOURCE_CRS"    # override CRS when the staged DEM lacks one
DEM_RES_ENV = "GEO_DEM_RES_M"                # override the live 3DEP fetch resolution (10/30/60)
SLOPE_FINAL_AOI_ENV = "GEO_SLOPE_FINAL_AOI"  # "west,south,east,north" (4326) → also emit 10 m final
SLOPE_SCREENING_RES_M = 30                   # broad screening pass (county-wide slope.tif)
SLOPE_FINAL_RES_M = 10                       # final candidate re-evaluation pass
SLOPE_METRIC_CRS = CRS_METRIC_UTM            # compute & store slope in EPSG:26911 (meters)
SLOPE_NODATA = -9999.0                       # GeoTIFF nodata for masked / off-county cells
# Stage-A exclusion threshold (percent grade): parcels steeper than this are excluded in
# enrichment (GEO-13). Defined here so the slope artifact and the scorer agree on one value.
SLOPE_MAX_PCT = 15.0
SLOPE_SCREENING_TIF = "slope.tif"            # canonical county-wide screening raster
SLOPE_FINAL_TIF = "slope_final.tif"          # 10 m final-pass raster (when an AOI is given)
SLOPE_TABLE = "slope_raster"                 # metadata table (one row per emitted raster)

# ── Solar resource (GEO-10, NREL Solar Resource API → GHI grid) ────────────────
# NREL "Solar Resource Data" v1 (developer.nlr.gov/api/solar/solar_resource/v1.json):
# per lat/lon it returns annual + monthly avg_ghi (kWh/m²/day), avg_dni and avg_lat_tilt.
# NB the old developer.nrel.gov host was RETIRED 2026-05-29 → developer.nlr.gov (fronted by
# api.data.gov / cloud.gov); endpoint + schema confirmed live 2026-06-15 (DEMO_KEY).
# We sample a regular grid over the county bbox (clipped to the polygon), query each point
# (throttled to the 1,000 req/hr key limit, on-disk cached so re-runs don't re-query), and
# persist a `ghi_grid` points table + ghi_grid.parquet. GHI is sampled PER PARCEL from this
# grid in enrichment (GEO-13) — never queried per parcel (review C10). The API key is
# Settings.nrel_api_key (env NREL_API_KEY), the project's one secret (kept out of repr/logs).
# A pre-staged CSV (lon,lat,avg_ghi,avg_dni,avg_lat_tilt; 4326) via GEO_NREL_GHI_SOURCE feeds
# the offline/test path. NREL needs a free api_key — get one at https://developer.nlr.gov/signup/
# (instant, free; DEMO_KEY works for light testing). The HTTP client lives in pipeline/nrel.py
# with an injectable transport.
NREL_SOLAR_RESOURCE_URL = "https://developer.nlr.gov/api/solar/solar_resource/v1.json"
NREL_GHI_URL_ENV = "GEO_NREL_GHI_URL"
NREL_GHI_SOURCE_ENV = "GEO_NREL_GHI_SOURCE"   # pre-staged CSV (lon,lat,avg_ghi,avg_dni,avg_lat_tilt)
NREL_GHI_CACHE_ENV = "GEO_NREL_CACHE_DIR"     # response cache dir (default <data_dir>/.cache/nrel)
NREL_GHI_GRID_SPACING_DEG = 0.1               # ~11 km sample spacing over the county bbox
NREL_RATE_PER_HOUR = 1000                     # API key limit; throttle live calls to stay under it
# CSV column candidates (resolved case-insensitively, first match wins).
NREL_GHI_FIELDS = ("avg_ghi", "ghi", "annual_ghi")
NREL_DNI_FIELDS = ("avg_dni", "dni", "annual_dni")
NREL_LAT_TILT_FIELDS = ("avg_lat_tilt", "lat_tilt", "annual_lat_tilt")
NREL_LON_FIELDS = ("lon", "longitude", "x")
NREL_LAT_FIELDS = ("lat", "latitude", "y")
GHI_GRID_TABLE = "ghi_grid"
GHI_GRID_PARQUET = "ghi_grid.parquet"

# ── Supplemental / optional layers (GEO-11) ────────────────────────────────────
# OPTIONAL and explicitly OFF the critical path (review C8): these fetchers NEVER fail the
# build when their source is unconfigured — they create an empty table (logged at WARNING)
# instead of raising, so the core pipeline (and artifact assembly, which does not depend on
# them) is unaffected. Two concerns:
#  (1) EIA-860/860M generators (plant lat/lon, capacity, fuel/tech, status) — a points layer
#      for cross-checking the CAISO queue. Tabular source → pre-staged CSV via
#      GEO_EIA860_SOURCE (lon/lat/…); a CSV-mirror URL can be set via GEO_EIA860_URL. The
#      live EIA-860 download (a zip of spreadsheets) is deferred — default URL is empty.
#  (2) Exclusion overlay polygons (protected areas / open water / built-up) so the §5 Stage-A
#      OPTIONAL exclusions become implementable. Unioned into one `exclusions` table with a
#      `kind` column; each kind ingests only when its GEO_EXCLUSION_<KIND>_SOURCE (or _URL) is
#      configured. Real national sources (PAD-US, NHD, NLCD) are large and their endpoints are
#      deferred, so default URLs are empty — stage a clipped GeoJSON to enable a kind.
# Spatial-join flags (parcel × exclusion, generator cross-checks) are computed in enrichment
# (GEO-13); scoring wires the optional exclusions behind a flag.
EIA860_SOURCE_ENV = "GEO_EIA860_SOURCE"      # pre-staged CSV (lon,lat,capacity,fuel,status,…)
EIA860_URL_ENV = "GEO_EIA860_URL"            # optional CSV-mirror URL (live zip download deferred)
EIA860_URL = ""                              # deferred (no default live endpoint)
EIA860_PLANT_ID_FIELDS = ("Plant Code", "plant_id", "Plant ID", "plant_code", "EIA_ID")
EIA860_NAME_FIELDS = ("Plant Name", "plant_name", "name")
EIA860_CAPACITY_FIELDS = ("Nameplate Capacity (MW)", "capacity_mw", "nameplate_mw", "MW")
EIA860_FUEL_FIELDS = ("Technology", "technology", "Energy Source 1", "prime_mover", "fuel")
EIA860_STATUS_FIELDS = ("Status", "status", "operating_status")
EIA860_COUNTY_FIELDS = ("County", "county")
EIA860_LON_FIELDS = ("lon", "longitude", "Longitude", "x")
EIA860_LAT_FIELDS = ("lat", "latitude", "Latitude", "y")
EIA_GENERATORS_TABLE = "eia_generators"
EIA_GENERATORS_PARQUET = "eia_generators.parquet"
# Exclusion overlay layers: (kind, SOURCE env, SOURCE_CRS env, URL env, default URL).
EXCLUSION_LAYERS = (
    ("protected_area", "GEO_EXCLUSION_PROTECTED_SOURCE", "GEO_EXCLUSION_PROTECTED_SOURCE_CRS",
     "GEO_EXCLUSION_PROTECTED_URL", ""),
    ("open_water", "GEO_EXCLUSION_WATER_SOURCE", "GEO_EXCLUSION_WATER_SOURCE_CRS",
     "GEO_EXCLUSION_WATER_URL", ""),
    ("built_up", "GEO_EXCLUSION_BUILTUP_SOURCE", "GEO_EXCLUSION_BUILTUP_SOURCE_CRS",
     "GEO_EXCLUSION_BUILTUP_URL", ""),
)
EXCLUSION_NAME_FIELDS = ("name", "NAME", "Unit_Nm", "GAP_Sts", "GNIS_NAME", "label", "TYPE")
EXCLUSION_ID_FIELDS = ("id", "OBJECTID", "FID", "OBJECTID_1", "GFID", "Source_PAID")
EXCLUSIONS_TABLE = "exclusions"
EXCLUSIONS_PARQUET = "exclusions.parquet"

# ── Parcel vector tiles (GEO-14, tippecanoe → parcels.pmtiles) ─────────────────
# The parcels fetcher (GEO-4) emits parcels.geojson (4326) into the release dir; this step
# tiles it into parcels.pmtiles for the SPA (served later via HTTP byte-range by nginx,
# GEO-34). Independent of the DuckDB builder (only needs parcels) so it can run in parallel.
# tippecanoe is a native binary (not a pip dep) — resolved on PATH or via GEO_TIPPECANOE_BIN.
# See pipeline/tiles.py (`python -m pipeline.tiles`, `make tiles`).
PARCELS_GEOJSON_NAME = "parcels.geojson"     # produced by the parcels fetcher (GEO-4)
PARCELS_PMTILES_NAME = "parcels.pmtiles"
PARCELS_TILE_LAYER = "parcels"               # vector source-layer name (the SPA references this)
PARCELS_TILE_MINZOOM = 8                      # parcels visible from county-overview zoom
PARCELS_TILE_MAXZOOM = 16                     # full detail
PARCELS_TILE_SIMPLIFICATION = 10             # Douglas-Peucker tolerance below max zoom (zoom-based)
PARCELS_TILE_ATTRS = ("id", "apn", "acres")  # base attributes carried into the tiles
TIPPECANOE_BIN_ENV = "GEO_TIPPECANOE_BIN"    # override the tippecanoe executable path

# ── DuckDB artifact assembly + parcel enrichment (GEO-12 / GEO-13) ─────────────
# The builder runs once after every fetcher has loaded its table, the convergence point
# where all core layers are present. assemble() (GEO-12) finalizes the parcels table —
# Hilbert-orders it on write for spatial locality, builds an R-tree index on the geometry,
# and verifies the GeoParquet intermediates carry their bbox struct. enrich() (GEO-13,
# FR-A4) computes the derived per-parcel columns (centroids, zonal slope, GHI, nearest
# tx/substation distances + kV, POI competition, SFHA/zoning/exclusion flags) IN PLACE
# (ALTER ADD COLUMN + UPDATE ... FROM) so the index + Hilbert order survive. Every metric
# computation goes through crs.to_metric_sql (EPSG:26911, always_xy:=true) — the CRS hotspot.
# NB: the GEO-13 ticket text assumes DuckDB's 1.3+ auto spatial-join optimizer, but the repo
# deliberately pins duckdb==1.1.3 (spatial ABI); the joins are written to be correct without
# it (the county-clipped candidate sets are small, so nested-loop nearest-neighbor is fine).
PARCELS_TABLE = "parcels"
PARCELS_GEOM_INDEX = "parcels_geom_rtree"    # R-tree index name on parcels.geom
# Core fetcher tables that MUST exist before assembly (the convergence contract: parcels,
# zoning, tx/sub, CAISO, flood, slope, GHI + the shared county_boundary). Missing one means a
# core fetcher did not run — fail the build loudly rather than ship a half-assembled artifact.
BUILDER_REQUIRED_TABLES = (
    "county_boundary", "parcels", "zoning", "transmission_lines", "substations",
    "caiso_queue", "flood_sfha", "slope_raster", "ghi_grid",
)


@dataclass(frozen=True)
class Settings:
    """Runtime settings for one ingestion build."""

    data_dir: Path
    keep_releases: int
    log_level: str
    nrel_api_key: str = field(repr=False)  # secret — keep out of repr()/logs
    duckdb_threads: int

    @property
    def releases_dir(self) -> Path:
        return self.data_dir / RELEASES_SUBDIR

    @property
    def current_link(self) -> Path:
        return self.data_dir / CURRENT_SUBDIR

    def release_dir(self, build_id: str) -> Path:
        return self.releases_dir / build_id

    @property
    def current_artifact_path(self) -> Path:
        """The path readers (api, web) should open: /data/current/site.duckdb."""
        return self.current_link / ARTIFACT_NAME


def _int_env(name: str, default: int) -> int:
    """Read an int env var, treating unset/blank (e.g. `KEY=` in .env) as the default."""
    raw = os.environ.get(name, "")
    return int(raw) if raw.strip() else default


def from_env() -> Settings:
    """Build Settings from environment variables (compose injects these)."""
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        keep_releases=max(1, _int_env("KEEP_RELEASES", 3)),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        nrel_api_key=os.environ.get("NREL_API_KEY", ""),
        duckdb_threads=max(1, _int_env("DUCKDB_THREADS", os.cpu_count() or 4)),
    )
