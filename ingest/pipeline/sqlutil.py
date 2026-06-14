"""Tiny SQL literal/identifier escaping helpers.

DuckDB has no parameter binding for things like file paths inside `COPY ... TO '<path>'`
or `ST_Read('<path>')`, so we build those fragments by hand. Centralizing the escaping
keeps every call site safe against apostrophes in paths and quotes in identifiers.
"""

from __future__ import annotations


def sql_str(value: object) -> str:
    """Escape a value as a single-quoted SQL string literal (handles apostrophes)."""
    return "'" + str(value).replace("'", "''") + "'"


def ident(name: str) -> str:
    """Quote a SQL identifier (handles embedded double-quotes)."""
    return '"' + str(name).replace('"', '""') + '"'
