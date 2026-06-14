"""DuckDB connection bootstrap — spatial loaded, threads set, httpfs optional.

Centralizes the connection pattern so both the ingest build (read-write) and the API
(read-only, GEO-15) open DuckDB the same way. `httpfs` is loaded only when remote reads
are needed (fetchers); it is never used on the request path.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def connect(
    database: str | Path = ":memory:",
    *,
    read_only: bool = False,
    load_httpfs: bool = False,
    threads: int = 4,
) -> "duckdb.DuckDBPyConnection":
    con = duckdb.connect(database=str(database), read_only=read_only)
    try:
        con.execute(f"PRAGMA threads={int(threads)}")
        con.execute("INSTALL spatial; LOAD spatial;")
        if load_httpfs:
            con.execute("INSTALL httpfs; LOAD httpfs;")
    except Exception:
        con.close()  # never leak the handle / file lock if bootstrap fails
        raise
    return con
