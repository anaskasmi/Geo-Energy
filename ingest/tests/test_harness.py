"""Harness: atomic swap, idempotency, manifest/success markers, failure safety."""

import dataclasses
import json

import pytest

from pipeline import config, harness
from pipeline.config import Settings
from pipeline.fetchers import base
from pipeline.fetchers.base import Fetcher, FetchContext, LayerResult, register

duckdb = pytest.importorskip("duckdb")  # the harness builds a real DuckDB artifact


def make_settings(tmp_path, keep=3):
    return Settings(
        data_dir=tmp_path,
        keep_releases=keep,
        log_level="INFO",
        nrel_api_key="",
        duckdb_threads=2,
    )


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    base.clear_registry()
    # The harness discovers on-disk fetchers via load_all(); these tests exercise harness
    # mechanics with their own synthetic fetchers, so neutralize auto-discovery here.
    monkeypatch.setattr(harness.fetchers, "load_all", lambda: None)
    yield
    base.clear_registry()


def _manifest_table(artifact):
    con = duckdb.connect(str(artifact), read_only=True)
    try:
        return dict(con.execute("SELECT key, value FROM build_manifest").fetchall())
    finally:
        con.close()


def test_empty_build_is_valid_and_swapped(tmp_path):
    cfg = make_settings(tmp_path)
    out = harness.run(cfg, build_id="20260101T000000_000001Z")

    assert out == cfg.current_artifact_path
    assert cfg.current_link.is_symlink()
    assert out.exists()

    release = cfg.current_link.resolve()
    assert (release / config.SUCCESS_MARKER).exists()
    manifest = json.loads((release / config.MANIFEST_NAME).read_text())
    assert manifest["layers"] == []
    assert manifest["crs"]["storage"] == 4326
    assert "build_id" in _manifest_table(out)


def test_build_with_fetcher_records_layer(tmp_path):
    @register
    class Dummy(Fetcher):
        name = "dummy"
        run_order = 0

        def fetch(self, ctx: FetchContext) -> LayerResult:
            ctx.con.execute("CREATE TABLE dummy AS SELECT * FROM (VALUES (1), (2), (3)) t(id)")
            n = ctx.con.execute("SELECT count(*) FROM dummy").fetchone()[0]
            return LayerResult(name="dummy", table="dummy", feature_count=n, source="test")

    cfg = make_settings(tmp_path)
    out = harness.run(cfg, build_id="20260101T000000_000002Z")

    con = duckdb.connect(str(out), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM dummy").fetchone()[0] == 3
    finally:
        con.close()

    manifest = json.loads((cfg.current_link.resolve() / config.MANIFEST_NAME).read_text())
    assert manifest["layers"] == [
        {"name": "dummy", "table": "dummy", "features": 3, "source": "test"}
    ]


def test_idempotent_rerun_and_prune(tmp_path):
    cfg = make_settings(tmp_path, keep=2)
    ids = [f"20260101T0000{i:02d}_000000Z" for i in range(4)]
    for build_id in ids:
        harness.run(cfg, build_id=build_id)

    assert cfg.current_link.resolve().name == ids[-1]
    releases = sorted(p.name for p in cfg.releases_dir.iterdir() if p.is_dir())
    assert releases == ids[-2:]  # pruned to keep=2, newest kept


def test_failed_build_leaves_current_untouched(tmp_path):
    cfg = make_settings(tmp_path)
    good = harness.run(cfg, build_id="20260101T000000_000010Z")
    good_target = cfg.current_link.resolve()

    @register
    class Boom(Fetcher):
        name = "boom"

        def fetch(self, ctx):
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        harness.run(cfg, build_id="20260101T000000_000011Z")

    assert cfg.current_link.resolve() == good_target
    assert not cfg.release_dir("20260101T000000_000011Z").exists()
    assert good.exists()


def test_same_build_id_rerun_never_clobbers_the_live_release(tmp_path):
    """Re-running with a build_id equal to the live one must not delete/rebuild in place."""
    cfg = make_settings(tmp_path)
    out1 = harness.run(cfg, build_id="dup")
    assert out1.exists()  # readable before the rerun

    out2 = harness.run(cfg, build_id="dup")
    assert out2.exists()  # readable after the rerun

    names = sorted(p.name for p in cfg.releases_dir.iterdir() if p.is_dir())
    assert names == ["dup", "dup__1"]  # second build got a fresh dir, did not overwrite
    assert cfg.current_link.resolve().name == "dup__1"


def test_failed_rerun_against_live_id_leaves_current_valid(tmp_path):
    """A failed rerun using the LIVE build_id must leave current pointing at good data."""
    cfg = make_settings(tmp_path)
    good = harness.run(cfg, build_id="live")
    good_target = cfg.current_link.resolve()

    @register
    class Boom(Fetcher):
        name = "boom"

        def fetch(self, ctx):
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        harness.run(cfg, build_id="live")  # same id as the live release

    assert cfg.current_link.resolve() == good_target
    assert good.exists()
    # no half-built staging/clobber left behind for the failed run
    assert sorted(p.name for p in cfg.releases_dir.iterdir() if p.is_dir()) == ["live"]


def test_prune_removes_incomplete_zombie(tmp_path):
    cfg = make_settings(tmp_path, keep=5)
    zombie = cfg.releases_dir / "zombie"
    zombie.mkdir(parents=True)
    (zombie / "site.duckdb").write_text("partial")  # no _SUCCESS marker

    harness.run(cfg, build_id="good")

    assert not zombie.exists()  # pruned as incomplete
    assert cfg.current_link.resolve().name == "good"


def test_prune_never_deletes_live_even_when_not_newest(tmp_path):
    cfg = make_settings(tmp_path, keep=5)
    for bid in ("b1", "b2", "b3"):
        harness.run(cfg, build_id=bid)

    # Point current at the OLDEST release, then prune with keep=1.
    harness._atomic_swap_current(cfg, cfg.release_dir("b1"))
    harness._prune_releases(dataclasses.replace(cfg, keep_releases=1))

    remaining = sorted(p.name for p in cfg.releases_dir.iterdir() if p.is_dir())
    assert "b1" in remaining  # live release survives despite not being newest
    assert "b2" not in remaining  # non-live excess pruned
