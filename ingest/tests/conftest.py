"""Shared test helpers: fixture paths, Settings factory, and a FetchContext factory."""

from pathlib import Path

import pytest

from pipeline import config
from pipeline.config import Settings
from pipeline.fetchers.base import FetchContext
from pipeline.logging_setup import get_logger

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _hermetic_sources(monkeypatch):
    """Keep the suite offline. The live fetcher endpoints now have real defaults, so blank
    them for every test: a test that forgets to stage a *_SOURCE fixture then fails with a
    SourceError instead of making a network call. Tests set their own source explicitly.
    """
    monkeypatch.setenv(config.PARCELS_GEODAT_URL_ENV, "")
    monkeypatch.setenv(config.PARCELS_SHAFTER_URL_ENV, "")
    monkeypatch.setenv(config.COUNTY_URL_ENV, "")
    monkeypatch.setenv(config.TRANSMISSION_URL_ENV, "")
    monkeypatch.setenv(config.SUBSTATIONS_URL_ENV, "")
    monkeypatch.setenv(config.ZONING_URL_ENV, "")
    monkeypatch.setenv(config.GENERAL_PLAN_URL_ENV, "")
    monkeypatch.setenv(config.SPECIFIC_PLANS_URL_ENV, "")


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
