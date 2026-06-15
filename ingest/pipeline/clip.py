"""County clip helpers for "clip to county" layer fetchers (GEO-6 transmission/substations,
and later flood/slope/GHI).

The county_boundary table (GEO-3, run_order=0) is built before these fetchers run, in the
same build connection, so they read its polygon and bbox directly. The bbox feeds a coarse
server-side prefilter (the ArcGIS envelope filter) and the polygon feeds the precise
per-feature clip in DuckDB. Geometry is in storage CRS (4326).
"""

from __future__ import annotations

from typing import Any

from .sources import SourceError

COUNTY_TABLE = "county_boundary"


def county_bbox(con: Any) -> tuple[float, float, float, float]:
    """Return the county bounding box (xmin, ymin, xmax, ymax) in 4326.

    Raises SourceError if the county_boundary table is missing or empty — these fetchers
    depend on GEO-3 having run first (enforced by run_order), and failing loudly beats
    silently fetching the whole nation with no envelope filter.
    """
    try:
        row = con.execute(
            f"SELECT bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax FROM {COUNTY_TABLE}"
        ).fetchone()
    except Exception as err:  # noqa: BLE001 — table missing → not yet built
        raise SourceError(
            f"cannot read {COUNTY_TABLE}; the county_boundary fetcher (GEO-3) must run first: {err}"
        ) from err
    if row is None or any(v is None for v in row):
        raise SourceError(f"{COUNTY_TABLE} has no usable bbox; county_boundary fetcher must run first")
    return (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
