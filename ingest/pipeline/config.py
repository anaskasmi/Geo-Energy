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
