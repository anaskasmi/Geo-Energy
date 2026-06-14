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
