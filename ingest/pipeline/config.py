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

# Parcels — GEODAT "Assessor Parcels Land 2025" primary, Shafter mirror fallback (both
# ArcGIS FeatureServers). [CONFIRM] the exact endpoints/fields; set via env until then.
PARCELS_GEODAT_ITEM = "31379b8b48ae455ea5972ce02a54cbb8"  # GEODAT item id (for ops reference)
PARCELS_GEODAT_URL = ""   # [CONFIRM] FeatureServer/<layer> URL; set GEO_PARCELS_GEODAT_URL
PARCELS_SHAFTER_URL = ""  # [CONFIRM] Shafter mirror URL; set GEO_PARCELS_SHAFTER_URL
PARCELS_GEODAT_URL_ENV = "GEO_PARCELS_GEODAT_URL"
PARCELS_SHAFTER_URL_ENV = "GEO_PARCELS_SHAFTER_URL"
PARCELS_SOURCE_ENV = "GEO_PARCELS_SOURCE"        # pre-staged local file (GeoJSON, 4326)
PARCELS_SOURCE_CRS_ENV = "GEO_PARCELS_SOURCE_CRS"
PARCELS_APN_FIELD_ENV = "GEO_PARCELS_APN_FIELD"  # force the APN attribute name
# Candidate APN attribute names, tried case-insensitively in order ([CONFIRM] at endpoint).
PARCELS_APN_FIELDS = ("APN", "ParcelID", "PARCEL_ID", "PARCELID", "APN_LABEL", "APN_D", "AIN")


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
