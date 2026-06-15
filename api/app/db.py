"""DuckDB connection bootstrap for the read-only API (GEO-15).

The API is a SEPARATE docker build context and cannot import ``pipeline.*`` (the
ingest package), so the small bits we need are replicated here, kept deliberately
in sync with ``ingest/pipeline/db.py`` and ``ingest/pipeline/config.py``:

  - ``ARTIFACT_NAME`` / ``CURRENT_SUBDIR`` / ``DATA_DIR`` default (``/data``)
  - the connection recipe: connect, ``PRAGMA threads``, ``INSTALL/LOAD spatial``
  - the threads-from-env autodetect (``DUCKDB_THREADS`` or ``os.cpu_count()``)

Readers always open the artifact through the ``current/`` symlink so they never
see a half-built release:  ``$DATA_DIR/current/site.duckdb``.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

# --- Mirrored constants (ingest/pipeline/config.py — keep in sync) -------------
ARTIFACT_NAME = "site.duckdb"  # NOT kern.duckdb — the ticket text is wrong
CURRENT_SUBDIR = "current"
DEFAULT_DATA_DIR = "/data"


def data_dir() -> Path:
    """The data root, read from the environment at call time (compose injects it)."""
    return Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR))


def artifact_path() -> Path:
    """Path readers should open: ``$DATA_DIR/current/site.duckdb``.

    Resolved lazily so tests can set ``DATA_DIR`` before app startup.
    """
    return data_dir() / CURRENT_SUBDIR / ARTIFACT_NAME


def resolve_threads() -> int:
    """DuckDB thread count from env, treating unset/blank as autodetect.

    Mirrors ingest's ``from_env``: ``max(1, DUCKDB_THREADS or os.cpu_count() or 4)``.
    """
    raw = os.environ.get("DUCKDB_THREADS", "")
    if raw.strip():
        return max(1, int(raw))
    return max(1, os.cpu_count() or 4)


def connect(
    database: str | Path,
    *,
    read_only: bool = True,
    threads: int | None = None,
) -> "duckdb.DuckDBPyConnection":
    """Open DuckDB the same way ingest does: spatial loaded, threads set.

    The handle/file-lock is never leaked on a bootstrap failure (``con.close()``
    in the except). The API only ever opens ``read_only=True`` (the :ro data mount
    is the load-bearing contract); spatial extensions install to ``~/.duckdb`` (HOME),
    not the DB file, so a read-only data mount is fine.
    """
    if threads is None:
        threads = resolve_threads()
    con = duckdb.connect(database=str(database), read_only=read_only)
    try:
        con.execute(f"PRAGMA threads={int(threads)}")
        con.execute("INSTALL spatial; LOAD spatial;")
    except Exception:
        con.close()  # never leak the handle / file lock if bootstrap fails
        raise
    return con
