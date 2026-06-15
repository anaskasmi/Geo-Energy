"""GEO-16 scoring engine tests, run against a REAL enriched parcels artifact.

Verifies Stage-A exclusions (slope/acres/sfha/zoning), Stage-B weighted ranking, the SQL score
matching the Python mirror (no drift between /score ranking and /explain breakdown), NULL
neutral imputation, and the weight/threshold/zoning resolution helpers.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from app import db, scoring
from tests.conftest import SCORED_POLYGON, build_scored_artifact


@pytest.fixture
def con(scored_data_dir):
    """Read-only handle on the built scored artifact (spatial loaded)."""
    c = db.connect(db.artifact_path(), read_only=True)
    yield c
    c.close()


def _run_score(con, use_case="utility_solar", weight_overrides=None, threshold_overrides=None, zoning_rules=None):
    weights = scoring.resolve_weights(use_case, weight_overrides)
    thresholds = scoring.resolve_thresholds(use_case, threshold_overrides)
    prohibited = scoring.prohibited_codes(zoning_rules, use_case, None)
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=prohibited, polygon=True, limit=100, offset=0
    )
    params["poly"] = json.dumps(SCORED_POLYGON)
    cols = [c[0] for c in con.execute(sql, params).description]
    rows = [dict(zip(cols, r)) for r in con.execute(sql, params).fetchall()]
    return rows, weights


def test_rtree_prefilter_used(con):
    """The candidate ST_Intersects must hit the R-tree index, not a full scan."""
    sql, params = scoring.build_score_sql(
        weights=scoring.resolve_weights("utility_solar", None),
        thresholds=scoring.resolve_thresholds("utility_solar", None),
        prohibited=[], polygon=True, limit=100,
    )
    params["poly"] = json.dumps(SCORED_POLYGON)
    plan = "\n".join(r[1] for r in con.execute("EXPLAIN " + sql, params).fetchall())
    assert "RTREE_INDEX_SCAN" in plan
    assert "parcels_geom_rtree" in plan


def test_stage_a_exclusions_utility_solar(con):
    """utility_solar (min_acres=20): excludes slope>15 (P3), acres<20 (P4), sfha (P5), zoning E (P6)."""
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    rows, _ = _run_score(con, "utility_solar", zoning_rules=rules)
    kept = sorted(r["id"] for r in rows)
    assert kept == [1, 2, 7], f"unexpected survivors: {kept}"


def test_stage_a_exclusions_data_center(con):
    """data_center (min_acres=5): P4 now survives (10>=5); P3 slope, P5 sfha, P6 zoning still out."""
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    rows, _ = _run_score(con, "data_center", zoning_rules=rules)
    kept = sorted(r["id"] for r in rows)
    assert kept == [1, 2, 4, 7], f"unexpected survivors: {kept}"


def test_unknown_slope_not_excluded_but_neutral(con):
    """P7 has NULL slope: it survives Stage A and its slope factor imputes neutral 0.5."""
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    rows, weights = _run_score(con, "utility_solar", zoning_rules=rules)
    p7 = next(r for r in rows if r["id"] == 7)
    assert p7["mean_slope_pct"] is None
    assert scoring.factor_norm(scoring.FACTORS["slope"], None) == 0.5


def test_sql_score_matches_python_mirror(con):
    """The SQL Stage-B score must equal the Python score_value() to ~0.1 (rounding)."""
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    rows, weights = _run_score(con, "utility_solar", zoning_rules=rules)
    assert rows
    for r in rows:
        expected = round(scoring.score_value(weights, r), 1)
        assert abs(r["score"] - expected) <= 0.1, f"parcel {r['id']}: sql={r['score']} py={expected}"


def test_ranking_order_solar(con):
    """Best solar parcel (P1: high GHI, close grid, has kV) ranks first; results are sorted."""
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    rows, _ = _run_score(con, "utility_solar", zoning_rules=rules)
    assert rows[0]["id"] == 1
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_weight_override_renormalizes(con):
    """Partial weight overrides are merged and renormalised to sum 1.0."""
    w = scoring.resolve_weights("utility_solar", {"ghi": 1.0, "slope": 1.0})
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0
    # ghi and slope dominate now; both equal.
    assert pytest.approx(w["ghi"], abs=1e-9) == w["slope"]


def test_zoning_override_changes_exclusions(con):
    """A request prohibited-zoning override is honoured over the build rules."""
    # Override REPLACES the prohibited set with {'A'} (P1/P4/P5 use 'A'). E is no longer
    # prohibited, so P6 (zoning E, otherwise valid) survives; P2 (M-1) survives; P7 (NULL zoning)
    # survives (NULL is never prohibited). P1/P4/P5 drop on zoning/sfha/acres.
    thresholds = scoring.resolve_thresholds("utility_solar", None)
    weights = scoring.resolve_weights("utility_solar", None)
    prohibited = scoring.prohibited_codes(None, "utility_solar", ["A"])
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=prohibited, polygon=True, limit=100
    )
    params["poly"] = json.dumps(SCORED_POLYGON)
    kept = sorted(r[0] for r in con.execute(sql, params).fetchall())
    assert kept == [2, 6, 7]


def test_null_zoning_not_dropped_with_prohibited_list(con):
    """Regression: a parcel with NULL zoning_class must survive a non-empty prohibited list.

    ``zoning_class IN (...)`` over NULL yields NULL (SQL 3-valued logic); without the NULL guard
    the combined ``excluded`` becomes NULL and ``NOT(excluded)`` silently drops the parcel. P7 has
    NULL zoning_class, so it must remain in the survivors even though 'E' is prohibited.
    """
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    assert scoring.prohibited_codes(rules, "utility_solar", None) == ["E"]  # non-empty
    rows, _ = _run_score(con, "utility_solar", zoning_rules=rules)
    kept = sorted(r["id"] for r in rows)
    assert 7 in kept, "NULL-zoning parcel was dropped by the prohibited-list NULL bug"
    p7 = next(r for r in rows if r["id"] == 7)
    assert p7["zoning_class"] is None and p7["excl_zoning"] is False


def test_load_zoning_rules(scored_data_dir):
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    assert rules["solar"]["prohibited"] == ["E"]
    assert "M-1" in rules["data_center"]["by_right"]
    assert scoring.prohibited_codes(rules, "utility_solar", None) == ["E"]
    assert scoring.prohibited_codes(rules, "data_center", None) == ["E"]


def test_missing_zoning_rules_is_empty(tmp_path, monkeypatch):
    """A build with no zoning_rules.csv yields no zoning filter (graceful)."""
    build_scored_artifact(tmp_path, with_zoning=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rules = scoring.load_zoning_rules(db.zoning_rules_path())
    assert rules == {}
    assert scoring.prohibited_codes(rules, "utility_solar", None) == []


def test_invalid_inputs_raise():
    with pytest.raises(scoring.ScoringError):
        scoring.resolve_weights("bogus", None)
    with pytest.raises(scoring.ScoringError):
        scoring.resolve_weights("utility_solar", {"nope": 1.0})
    with pytest.raises(scoring.ScoringError):
        scoring.resolve_weights("utility_solar", {"ghi": -1.0})
    with pytest.raises(scoring.ScoringError):
        scoring.prohibited_codes(None, "utility_solar", ["DROP TABLE parcels;"])


def test_empty_candidate_set(con):
    """A polygon intersecting no parcels returns no rows (NULL-safe)."""
    weights = scoring.resolve_weights("utility_solar", None)
    thresholds = scoring.resolve_thresholds("utility_solar", None)
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=[], polygon=True, limit=100
    )
    from tests.conftest import EMPTY_POLYGON
    params["poly"] = json.dumps(EMPTY_POLYGON)
    assert con.execute(sql, params).fetchall() == []
