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

import logging
import os
import re
from pathlib import Path

import duckdb

log = logging.getLogger("api.db")

# Operator-set DuckDB resource knobs (validated before inlining into PRAGMA/SET).
_MEM_LIMIT_RE = re.compile(r"^\d+(\.\d+)?\s*(B|K|KB|M|MB|G|GB|T|TB|KiB|MiB|GiB|TiB)?$", re.IGNORECASE)
_TEMP_DIR_RE = re.compile(r"^[A-Za-z0-9_./\-]+$")

# --- Mirrored constants (ingest/pipeline/config.py — keep in sync) -------------
ARTIFACT_NAME = "site.duckdb"  # NOT kern.duckdb — the ticket text is wrong
CURRENT_SUBDIR = "current"
DEFAULT_DATA_DIR = "/data"
ZONING_RULES_NAME = "zoning_rules.csv"  # curated (zone_code × use_case) → permission, per build


def data_dir() -> Path:
    """The data root, read from the environment at call time (compose injects it)."""
    return Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR))


def artifact_path() -> Path:
    """Path readers should open: ``$DATA_DIR/current/site.duckdb``.

    Resolved lazily so tests can set ``DATA_DIR`` before app startup.
    """
    return data_dir() / CURRENT_SUBDIR / ARTIFACT_NAME


def zoning_rules_path() -> Path:
    """The build's curated zoning rules CSV: ``$DATA_DIR/current/zoning_rules.csv``.

    Emitted alongside the artifact by the ingest harness; the scoring engine loads it once at
    startup to derive prohibited zoning per use case. May be absent (then zoning is not a filter).
    """
    return data_dir() / CURRENT_SUBDIR / ZONING_RULES_NAME


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
    _apply_resource_limits(con)
    return con


def _apply_resource_limits(con: "duckdb.DuckDBPyConnection") -> None:
    """Apply container-aware DuckDB limits from the environment (best-effort, never fatal).

    - ``DUCKDB_MEMORY_LIMIT`` caps the buffer pool. DuckDB otherwise sizes it to ~80% of the
      HOST RAM (cgroup memory limits are not reliably detected), so a capped container can
      OOM-kill + restart-loop without this; compose sets it below ``mem_limit``.
    - ``DUCKDB_TEMP_DIR`` gives spilling queries a WRITABLE directory. The default temp dir sits
      next to the DB file, which lives on the read-only ``:ro`` data mount, so a spill would
      fail; point it at a writable path (e.g. under HOME).

    Failures are logged and swallowed — these are hardening knobs, not correctness-critical, and
    must not take the read path down on a misconfig.
    """
    mem = os.environ.get("DUCKDB_MEMORY_LIMIT", "").strip()
    if mem:
        if _MEM_LIMIT_RE.match(mem):
            try:
                con.execute(f"PRAGMA memory_limit='{mem}'")
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warning("could not apply DUCKDB_MEMORY_LIMIT=%r: %s", mem, exc)
        else:
            log.warning("ignoring malformed DUCKDB_MEMORY_LIMIT=%r", mem)
    tmp = os.environ.get("DUCKDB_TEMP_DIR", "").strip()
    if tmp:
        if _TEMP_DIR_RE.match(tmp):
            try:
                os.makedirs(tmp, exist_ok=True)
                con.execute(f"SET temp_directory='{tmp}'")
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warning("could not set DUCKDB_TEMP_DIR=%r: %s", tmp, exc)
        else:
            log.warning("ignoring malformed DUCKDB_TEMP_DIR=%r", tmp)
