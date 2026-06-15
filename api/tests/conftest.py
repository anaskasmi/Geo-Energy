"""Shared test fixtures for the API.

We build a tiny REAL ``site.duckdb`` (spatial loaded, a couple of trivial tables)
under ``<tmp>/current/`` — the same path readers follow in production — and point
``DATA_DIR`` at the tmp root so the app's lifespan opens it for real.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest


def build_artifact(data_dir: Path) -> Path:
    """Create ``<data_dir>/current/site.duckdb`` with spatial + a manifest table."""
    current = data_dir / "current"
    current.mkdir(parents=True, exist_ok=True)
    artifact = current / "site.duckdb"

    con = duckdb.connect(database=str(artifact), read_only=False)
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        # Mirror the harness: a build_manifest(key, value JSON) table, plus a trivial
        # table so a normal request path has something to read.
        con.execute("CREATE TABLE build_manifest (key VARCHAR, value JSON)")
        con.execute("INSERT INTO build_manifest VALUES ('schema_version', '1')")
        con.execute("CREATE TABLE sites (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO sites VALUES (1, 'alpha'), (2, 'beta')")
    finally:
        con.close()
    return artifact


@pytest.fixture
def healthy_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """A tmp DATA_DIR containing a real, openable artifact."""
    build_artifact(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def empty_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """A tmp DATA_DIR with NO artifact (tolerant-startup / unhealthy path)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path
