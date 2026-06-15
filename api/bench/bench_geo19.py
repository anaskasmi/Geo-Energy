"""GEO-19 query-perf benchmark: R-tree vs H3 candidate selection, parcel-count sweep,
thread scaling, and concurrent-request throughput.

Run:  python -m bench.bench_geo19            (from the api/ dir; needs duckdb + the h3 community
                                              extension, which installs on first run)
Env:  BENCH_N=150000  BENCH_REPEATS=5  H3_RES=9  BENCH_COUNTS=50000,150000,400000  BENCH_CONC=16

Why synthetic data: the real Kern parcels come from the offline ingest, whose sources are
US-geoblocked from this host (see memory). So we generate N realistic-shaped parcels (small
buffered points scattered across the Kern County bbox) with the SAME enriched schema the scoring
engine reads, an R-tree on geom, an H3 cell per parcel, plus ART indexes. The R-tree-vs-H3
conclusion depends on parcel COUNT and query polygon SIZE — this script varies BOTH (a count sweep
+ three polygon sizes) so the numbers, not just the prose, show the dependence.

Writes a human-readable report to stdout; the headline numbers feed docs/GEO-19-query-perf.md.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time

import duckdb

from app import scoring

KERN_BBOX = (-120.20, 34.79, -117.62, 35.81)
N = int(os.environ.get("BENCH_N", "150000"))
REPEATS = int(os.environ.get("BENCH_REPEATS", "5"))
H3_RES = int(os.environ.get("H3_RES", "9"))
COUNTS = [int(x) for x in os.environ.get("BENCH_COUNTS", "50000,150000,400000").split(",")]
CONC = int(os.environ.get("BENCH_CONC", "16"))


def _bbox_poly(cx: float, cy: float, half: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [cx - half, cy - half], [cx + half, cy - half],
            [cx + half, cy + half], [cx - half, cy + half], [cx - half, cy - half],
        ]],
    }


QUERIES = {
    "small (~2km box)": _bbox_poly(-119.0, 35.30, 0.01),
    "medium (~20km box)": _bbox_poly(-119.0, 35.30, 0.10),
    "large (~quarter county)": _bbox_poly(-119.0, 35.30, 0.50),
}
MEDIUM = json.dumps(QUERIES["medium (~20km box)"])


def build(con: duckdb.DuckDBPyConnection, n: int) -> None:
    con.execute("INSTALL spatial; LOAD spatial; INSTALL h3 FROM community; LOAD h3;")
    span_x = KERN_BBOX[2] - KERN_BBOX[0]
    span_y = KERN_BBOX[3] - KERN_BBOX[1]
    # Deterministic pseudo-random scatter via hash(); ~80 m square parcels (ST_Buffer of a point).
    con.execute(
        f"""
        CREATE TABLE parcels AS
        WITH base AS (
          SELECT i AS id,
            {KERN_BBOX[0]} + {span_x} * ((hash(i * 2654435761) % 1000000) / 1000000.0) AS lng,
            {KERN_BBOX[1]} + {span_y} * ((hash(i * 40503 + 7) % 1000000) / 1000000.0) AS lat,
            (hash(i * 17 + 3) % 1000) AS r
          FROM range({n}) t(i)
        )
        SELECT id,
          format('{{:09d}}', id) AS apn, format('{{:09d}}', id) AS apn_norm,
          (5.0 + (r % 700)) * 4046.8564224 AS area_sqm,
          5.0 + (r % 700) AS acres,
          ST_Buffer(ST_Point(lng, lat), 0.0008) AS geom,
          NULL AS centroid_26911, ST_Point(lng, lat) AS centroid_4326,
          (r % 25)::DOUBLE AS mean_slope_pct, NULL AS mean_slope_pct_final,
          4.8 + (r % 18) / 10.0 AS ghi,
          (r * 31 % 25000)::DOUBLE AS dist_tx_m, (r * 13 % 25000)::DOUBLE AS dist_sub_m,
          CASE WHEN r % 7 = 0 THEN NULL ELSE (115 + (r % 4) * 115)::DOUBLE END AS nearest_sub_kv,
          CASE WHEN r % 5 = 0 THEN (r % 2000)::DOUBLE ELSE NULL END AS poi_competition_mw,
          CASE WHEN r % 5 = 0 THEN (r % 6)::BIGINT ELSE NULL END AS poi_competition_n,
          (r % 23 = 0) AS sfha_flag,
          CASE r % 4 WHEN 0 THEN 'A' WHEN 1 THEN 'M-1' WHEN 2 THEN 'M-2' ELSE 'E' END AS zoning_class,
          FALSE AS excl_protected_area, FALSE AS excl_open_water, FALSE AS excl_built_up,
          (r * 7 % 12000)::DOUBLE AS eia_nearest_m,
          h3_latlng_to_cell(lat, lng, {H3_RES}) AS h3_cell
        FROM base
        """
    )
    con.execute("CREATE INDEX parcels_geom_rtree ON parcels USING RTREE (geom)")
    con.execute("CREATE INDEX parcels_h3 ON parcels (h3_cell)")
    con.execute("ANALYZE")


def _time(con, sql: str, params: dict, repeats: int = REPEATS) -> tuple[float, int]:
    n = con.execute(sql, params).fetchone()[0]  # warmup
    samples = []
    for _ in range(repeats):
        t = time.perf_counter()
        con.execute(sql, params).fetchone()
        samples.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(samples), n


def candidate_sqls():
    rtree = "SELECT count(*) FROM parcels WHERE ST_Intersects(geom, ST_GeomFromGeoJSON($poly))"
    h3 = (
        "WITH cells AS (SELECT unnest("
        f"h3_polygon_wkt_to_cells(ST_AsText(ST_GeomFromGeoJSON($poly)), {H3_RES})) AS c) "
        "SELECT count(*) FROM parcels p WHERE p.h3_cell IN (SELECT c FROM cells) "
        "AND ST_Intersects(p.geom, ST_GeomFromGeoJSON($poly))"
    )
    return {"rtree": rtree, "h3": h3}


def _score_sql():
    weights = scoring.resolve_weights("utility_solar", None)
    thresholds = scoring.resolve_thresholds("utility_solar", None)
    sql, params = scoring.build_score_sql(
        weights=weights, thresholds=thresholds, prohibited=["E"], polygon=True, limit=200, offset=0
    )
    params["poly"] = MEDIUM
    return sql, params


def main() -> None:
    con = duckdb.connect()
    print(f"duckdb {duckdb.__version__}  N={N:,}  H3_RES={H3_RES}  repeats={REPEATS}")
    t = time.perf_counter()
    build(con, N)
    print(f"built {N:,} parcels + R-tree + H3 index in {time.perf_counter()-t:.1f}s\n")

    print("== Candidate selection by polygon size (median ms over repeats) ==")
    print(f"{'query':<26}{'method':<8}{'cand':>8}{'ms':>10}")
    for label, poly in QUERIES.items():
        pj = json.dumps(poly)
        for method, sql in candidate_sqls().items():
            ms, cnt = _time(con, sql, {"poly": pj})
            print(f"{label:<26}{method:<8}{cnt:>8}{ms:>10.2f}")
        print()

    # EXPLAIN ANALYZE confirms RTREE_INDEX_SCAN (renders as 'RTREE INDEX SCAN' under ANALYZE).
    plan = "\n".join(
        r[1] for r in con.execute(
            "EXPLAIN ANALYZE SELECT count(*) FROM parcels "
            "WHERE ST_Intersects(geom, ST_GeomFromGeoJSON($poly))", {"poly": MEDIUM}
        ).fetchall()
    )
    print("== EXPLAIN ANALYZE (R-tree candidate, medium) ==")
    print("R-tree index scan present:", ("RTREE INDEX SCAN" in plan) or ("RTREE_INDEX_SCAN" in plan))
    for line in plan.splitlines():
        if any(k in line for k in ("RTREE", "Total Time", "SEQ_SCAN", "parcels")):
            print("   ", line.strip())
    print()

    # Full scoring query latency vs PRAGMA threads (single query, medium polygon).
    sql, params = _score_sql()
    print("== Full scoring query latency vs PRAGMA threads (single query, medium) ==")
    for threads in (1, 2, 4, 8):
        con.execute(f"PRAGMA threads={threads}")
        ms, _ = _time(con, sql, dict(params))
        print(f"   threads={threads:<3} {ms:>8.2f} ms")
    print()

    # Concurrent requests: CONC queries on independent cursors (the per-request cursor pattern),
    # comparing wall time at threads pinned low (2) vs over the box (8) to show oversubscription.
    print(f"== Concurrency: {CONC} simultaneous score queries (own cursor each), wall ms ==")

    def run_one():
        cur = con.cursor()
        try:
            cur.execute(sql, dict(params)).fetchall()
        finally:
            cur.close()

    for threads in (2, 8):
        con.execute(f"PRAGMA threads={threads}")
        run_one()  # warm
        t0 = time.perf_counter()
        workers = [threading.Thread(target=run_one) for _ in range(CONC)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        concurrent_ms = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        for _ in range(CONC):
            run_one()
        serial_ms = (time.perf_counter() - t0) * 1000.0
        print(f"   threads={threads:<3} concurrent={concurrent_ms:>8.1f} ms   serial={serial_ms:>8.1f} ms")
    print()

    # Parameterised reuse: same SQL, swap $poly, no re-parse.
    con.execute("PRAGMA threads=4")
    print("== Parameterised reuse (same SQL, swap $poly) ==")
    for label in ("small (~2km box)", "large (~quarter county)"):
        ms, _ = _time(con, sql, {**params, "poly": json.dumps(QUERIES[label])})
        print(f"   {label:<26}{ms:>8.2f} ms")
    print()
    con.close()

    # Parcel-COUNT sweep: rebuild at each N, measure the R-tree candidate + full score (medium).
    print("== Parcel-count sweep (medium polygon, median ms; fresh build per N) ==")
    print(f"{'N':>10}{'cand':>9}{'rtree ms':>10}{'score ms':>10}")
    rtree_sql = candidate_sqls()["rtree"]
    for n in COUNTS:
        c = duckdb.connect()
        build(c, n)
        c.execute("PRAGMA threads=4")
        cand_ms, cand = _time(c, rtree_sql, {"poly": MEDIUM})
        s_sql, s_params = _score_sql()
        score_ms, _ = _time(c, s_sql, dict(s_params))
        print(f"{n:>10,}{cand:>9}{cand_ms:>10.2f}{score_ms:>10.2f}")
        c.close()


if __name__ == "__main__":
    main()
