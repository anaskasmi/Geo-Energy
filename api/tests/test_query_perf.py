"""GEO-19 — query-perf regression guards.

The full benchmark (R-tree vs H3, thread sweep) lives in ``bench/bench_geo19.py`` and is run by
hand; see docs/GEO-19-query-perf.md for the decision (keep the R-tree). These fast tests pin the
two correctness-critical properties so they can't silently regress:
  * the production scoring query still hits the R-tree under EXPLAIN ANALYZE (it executes), and
  * the parameterised candidate prefilter is reusable — the same SQL, swapped ``$poly``, returns the
    right candidates (so weights/thresholds inlining doesn't break plan reuse).
"""

from __future__ import annotations

import json

import pytest

from app import db, scoring
from tests.conftest import EMPTY_POLYGON, SCORED_POLYGON


@pytest.fixture
def con(scored_data_dir):
    c = db.connect(db.artifact_path(), read_only=True)
    try:
        yield c
    finally:
        c.close()


def _score_sql(limit=200):
    weights = scoring.resolve_weights("utility_solar", None)
    thresholds = scoring.resolve_thresholds("utility_solar", None)
    return scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=["E"], polygon=True, limit=limit, offset=0
    )


def test_explain_analyze_uses_rtree(con):
    """EXPLAIN ANALYZE (which executes) shows the R-tree index scan on the scoring query.

    EXPLAIN ANALYZE renders it as 'RTREE INDEX SCAN' (spaces); plain EXPLAIN uses underscores —
    accept either, but require it names our index.
    """
    sql, params = _score_sql()
    params["poly"] = json.dumps(SCORED_POLYGON)
    plan = "\n".join(r[1] for r in con.execute("EXPLAIN ANALYZE " + sql, params).fetchall())
    assert ("RTREE INDEX SCAN" in plan) or ("RTREE_INDEX_SCAN" in plan)
    assert "parcels_geom_rtree" in plan
    assert "SEQ_SCAN" not in plan  # the spatial prefilter must not fall back to a full scan


def test_parameterised_polygon_reuse(con):
    """The same compiled SQL, run with two different $poly values, returns the right rows."""
    sql, params = _score_sql()
    hit = con.execute(sql, {**params, "poly": json.dumps(SCORED_POLYGON)}).fetchall()
    miss = con.execute(sql, {**params, "poly": json.dumps(EMPTY_POLYGON)}).fetchall()
    assert len(hit) >= 1   # SCORED_POLYGON covers parcels 1-7 (survivors ranked)
    assert len(miss) == 0  # EMPTY_POLYGON intersects nothing — reuse didn't leak the prior result
