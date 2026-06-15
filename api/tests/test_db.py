"""DuckDB connect() resource-limit knobs (GEO-35 hardening): memory_limit + temp_directory."""

from __future__ import annotations

from pathlib import Path

from app import db
from tests.conftest import build_scored_artifact


def _memory_limit(con) -> str:
    return con.execute("SELECT current_setting('memory_limit')").fetchone()[0]


def _temp_directory(con) -> str:
    return con.execute("SELECT current_setting('temp_directory')").fetchone()[0]


def test_memory_limit_applied(scored_data_dir, monkeypatch):
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "512MiB")
    con = db.connect(db.artifact_path(), read_only=True)
    try:
        # Binary unit round-trips; the cap is far below the ~80%-of-host-RAM default.
        assert "512" in _memory_limit(con).replace(" ", "")
    finally:
        con.close()


def test_temp_directory_applied_and_created(scored_data_dir, tmp_path, monkeypatch):
    spill = tmp_path / "spill"
    monkeypatch.setenv("DUCKDB_TEMP_DIR", str(spill))
    con = db.connect(db.artifact_path(), read_only=True)
    try:
        assert spill.is_dir()  # makedirs ran
        assert str(spill) in _temp_directory(con)
    finally:
        con.close()


def test_malformed_limits_are_ignored(scored_data_dir, monkeypatch):
    """A bad value is logged + swallowed; connect still succeeds (hardening is best-effort)."""
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "not-a-size; DROP TABLE parcels")
    monkeypatch.setenv("DUCKDB_TEMP_DIR", "/bad path/'; DROP")
    con = db.connect(db.artifact_path(), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM parcels").fetchone()[0] > 0
    finally:
        con.close()


def test_no_limits_by_default(scored_data_dir, monkeypatch):
    monkeypatch.delenv("DUCKDB_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("DUCKDB_TEMP_DIR", raising=False)
    con = db.connect(db.artifact_path(), read_only=True)
    try:
        assert con.execute("SELECT 1").fetchone()[0] == 1
    finally:
        con.close()
