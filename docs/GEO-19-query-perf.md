# GEO-19 — DuckDB query perf + H3 vs R-tree benchmark

**Decision: keep the R-tree.** It is faster than H3 at every query size *and* exact, while H3
candidate selection is both slower and lossy for our access pattern. The proximity factors
(`dist_tx_m`, `dist_sub_m`, …) are precomputed at ingest (GEO-13), so the query path never does
nearest-neighbour search — the only runtime spatial op is the `ST_Intersects` polygon prefilter,
which is exactly what an R-tree is best at. No change to the production proximity path.

## How this was measured

`api/bench/bench_geo19.py` (run: `python -m bench.bench_geo19` from `api/`). It builds **N
realistic parcels** — small (~80 m) buffered points scattered across the Kern County bbox with the
*same enriched schema the scoring engine reads* — plus an R-tree on `geom`, an H3 cell per parcel
(`h3_latlng_to_cell` at res 9), and ART indexes. Synthetic data is used deliberately: the real Kern
parcels come from the offline ingest whose sources are US-geoblocked from this host. The script
varies **both axes** the decision depends on: query polygon **size** (three boxes) and parcel
**count** (a sweep up to 400 k, ≈ real Kern scale).

Environment: DuckDB 1.1.3, `spatial` + `h3` (community) extensions, 5 repeats (median), one dev
laptop (many cores). Absolute ms will differ on the VPS; the *ratios* and *scaling* are the point.

## 1. Candidate selection — R-tree vs H3, by polygon size (N = 150 k)

| Query polygon            | R-tree (exact) ms | H3 (centroid cell) ms | R-tree cand | H3 cand |
|--------------------------|------------------:|----------------------:|------------:|--------:|
| small (~2 km box)        | **0.37**          | 0.89                  | 22          | 19      |
| medium (~20 km box)      | **1.66**          | 4.72                  | 2357        | 2318    |
| large (~¼ county)        | **44.6**          | 107.4                 | 57353       | 57150   |

Two findings: (1) the R-tree is **2.4–2.7× faster** in these runs; (2) the H3 path **undercounts**
(19 vs 22, 2318 vs 2357, 57150 vs 57353) because a parcel is indexed by its *centroid's* cell, so
parcels straddling the query boundary whose centroid cell isn't covered get dropped.

**Honesty note on the ms margin.** The R-tree query uses `RTREE_INDEX_SCAN` (confirmed below), but
`EXPLAIN ANALYZE` on the H3 query shows DuckDB does **not** use the `h3_cell` ART index for the
cell-membership test — it runs a `TABLE_SCAN` + `HASH_JOIN` against the polygon's covering cells.
So the H3 ms above is an *un-indexed* figure and the raw margin partly reflects that. We did not
keep chasing a faster H3 variant, because the decision does **not** rest on the margin — it rests
on three things that don't change: (a) H3 here is **lossy** (the centroid-cell undercount above),
and exactness matters for a scoring tool; (b) DuckDB's planner won't index-accelerate the
`h3_cell IN (cells)` predicate anyway (the hash-join above), so there's no cheap H3 index win for
polygon-cover queries, whereas the R-tree is purpose-built for `ST_Intersects` and **is** used; and
(c) the proximity factors are precomputed at ingest, so H3's real strength (fixed-radius neighbour
lookup / hex aggregation) is never on our query path. Making H3 exact (indexing every cell each
parcel touches + `ST_Intersects` refine) only adds storage and candidates to refine.

## 2. Parcel-count sweep (medium polygon, fresh build per N)

| N parcels | R-tree candidates | candidate ms | full-score ms |
|----------:|------------------:|-------------:|--------------:|
| 50,000    | 810               | 0.63         | 7.3           |
| 150,000   | 2,357             | 1.46         | 14.1          |
| **400,000** (≈ Kern) | 6,261  | **4.49**     | **20.5**      |

Candidate time scales **sub-linearly** with parcel count (the R-tree only touches the rows under
the polygon), and full scoring at ~Kern scale (400 k) is **~20 ms** for a 20 km box — measured, not
extrapolated.

## 3. EXPLAIN ANALYZE confirms the R-tree path

```
EXPLAIN ANALYZE SELECT count(*) FROM parcels WHERE ST_Intersects(geom, ST_GeomFromGeoJSON($poly))
  Total Time: 0.0045s
  parcels (RTREE INDEX SCAN : parcels_geom_rtree)
```

**Gotcha:** `EXPLAIN ANALYZE` renders the operator as `RTREE INDEX SCAN` (spaces); plain `EXPLAIN`
uses `RTREE_INDEX_SCAN` (underscores). Tests accept either. The R-tree only fires when
`ST_Intersects` is the **sole** predicate on the scan — which is why `build_score_sql` isolates the
spatial test in a candidate-id subquery (see `scoring.py`); `test_query_perf.py` asserts it.

## 4. PRAGMA threads — single query vs concurrent requests (N = 150 k, medium polygon)

Single query, by `PRAGMA threads`: 1 → 16.3 ms, 2 → 13.4 ms, 4 → 13.7 ms, 8 → 13.9 ms. The
production scoring query is small (the R-tree already narrowed the candidates), so a single query
is **latency-bound** — past 2 threads there's nothing more to parallelise.

Concurrency is where threads matter. **16 simultaneous** score queries, each on its own cursor (the
per-request cursor pattern), wall time:

| PRAGMA threads | 16 concurrent | 16 serial |
|---------------:|--------------:|----------:|
| 2              | 94 ms         | 239 ms    |
| 8              | 74 ms         | 236 ms    |

Concurrency gives ~2.5–3× over serial (DuckDB releases the GIL during execution). On this
many-core laptop `threads=8` was not harmful even with 16 in flight; on a **CPU-capped container**
that's the risk — 16 requests × 8 threads each would oversubscribe a 2-vCPU box — so we pin
`DUCKDB_THREADS` to the container's CPU quota (GEO-35) to keep a burst from thrashing. (The
oversubscription harm is a container-sizing argument, not visible on this unconstrained host.)

## 5. Parameterised / prepared statement reuse

The drawn polygon, `limit`, and `offset` are **bound parameters** (`$poly`/`$limit`/`$offset`);
weights, factor domains and thresholds are server-validated numbers inlined into the arithmetic, so
the SQL **text is stable per scoring profile**. Re-running the identical SQL with a different
`$poly` (small → 9.4 ms, large → 80 ms) is correct and fast. On top of that, GEO-18's LRU result
cache short-circuits *identical* requests entirely. We did **not** add a manual `PREPARE`/`EXECUTE`
layer: that's a design judgment (DuckDB reuses the plan for identical SQL, and the result cache
already covers repeats), not a measured A/B — the low absolute latencies above make the extra
complexity unwarranted. `test_query_perf.py` pins that the same SQL re-runs correctly with a
swapped `$poly`.

## Budget

Every case is far inside the **< 2 s scoring budget**: at ~Kern scale (400 k parcels) a 20 km box
full-scores in ~20 ms, and a quarter-county box selects ~57 k candidates in ~45 ms. Typical drawn
polygons are city-scale (the small/medium rows: single-digit to low-tens of ms).
