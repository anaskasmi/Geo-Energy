"""Shared test helpers: fixture paths, Settings factory, and a FetchContext factory."""

from pathlib import Path

import pytest

from pipeline.config import Settings
from pipeline.fetchers.base import FetchContext
from pipeline.logging_setup import get_logger

FIXTURES = Path(__file__).parent / "fixtures"


def make_settings(tmp_path, keep=3):
    return Settings(
        data_dir=tmp_path,
        keep_releases=keep,
        log_level="INFO",
        nrel_api_key="",
        duckdb_threads=2,
    )


@pytest.fixture
def mem_con():
    """An in-memory DuckDB connection with spatial loaded (closed after the test)."""
    pytest.importorskip("duckdb")
    from pipeline import db

    con = db.connect(":memory:", threads=2)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture
def ctx_factory(tmp_path, mem_con):
    """Build a FetchContext sharing one in-memory con and `tmp_path` as the work dir."""

    def make(**settings_kw):
        cfg = make_settings(tmp_path, **settings_kw)
        return FetchContext(work_dir=tmp_path, con=mem_con, settings=cfg, logger=get_logger("test"))

    return make
