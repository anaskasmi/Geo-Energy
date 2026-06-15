"""DuckDB connection bootstrap — spatial loaded, threads set, httpfs optional.

Centralizes the connection pattern so both the ingest build (read-write) and the API
(read-only, GEO-15) open DuckDB the same way. `httpfs` is loaded only when remote reads
are needed (fetchers); it is never used on the request path.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import duckdb

_log = logging.getLogger("ingest.db")

# DuckDB byte-size grammar (e.g. "4GB", "512MB") and an absolute path for the spill directory.
_MEM_LIMIT_RE = re.compile(r"^\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE)
_TEMP_DIR_RE = re.compile(r"^/[\w./-]+$")


def _apply_resource_limits(con: "duckdb.DuckDBPyConnection") -> None:
    """Cap DuckDB's buffer pool + give spilling queries a writable temp dir (best-effort).

    DuckDB sizes its buffer pool to ~80% of the HOST RAM and does NOT reliably read the
    container's cgroup memory limit, so a build that exceeds the container's allotment is
    SIGKILL'd by the kernel (exit 137) BEFORE DuckDB ever throttles or spills. The 421k-parcel
    enrichment (per-parcel nearest substation / transmission line / GHI point — large
    cross-joins with window functions) is the hot spot. Setting ``DUCKDB_MEMORY_LIMIT`` below the
    container's memory makes DuckDB spill to ``temp_directory`` instead of OOM-ing.

    For the ingest the DB file lives on the writable ``/data`` volume, so DuckDB's default temp
    (next to the file) already works; ``DUCKDB_TEMP_DIR`` only overrides it. Both knobs are
    best-effort — a misconfig is logged and swallowed, never fatal.
    """
    mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "").strip()
    if mem:
        if _MEM_LIMIT_RE.match(mem):
            try:
                con.execute(f"PRAGMA memory_limit='{mem}'")
            except Exception as exc:  # noqa: BLE001 — best-effort hardening, not correctness
                _log.warning("could not apply DUCKDB_MEMORY_LIMIT=%r: %s", mem, exc)
        else:
            _log.warning("ignoring malformed DUCKDB_MEMORY_LIMIT=%r", mem)
    tmp = os.environ.get("DUCKDB_TEMP_DIR", "").strip()
    if tmp:
        if _TEMP_DIR_RE.match(tmp):
            try:
                os.makedirs(tmp, exist_ok=True)
                con.execute(f"SET temp_directory='{tmp}'")
            except Exception as exc:  # noqa: BLE001 — best-effort
                _log.warning("could not set DUCKDB_TEMP_DIR=%r: %s", tmp, exc)
        else:
            _log.warning("ignoring malformed DUCKDB_TEMP_DIR=%r", tmp)


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
        # Container-aware memory cap + spill dir so a big build spills instead of OOM-killing.
        _apply_resource_limits(con)
    except Exception:
        con.close()  # never leak the handle / file lock if bootstrap fails
        raise
    return con
