"""Curated zoning_rules.csv (FR-A2): (zone_code × use_case) → permission lookup.

The reference table lives at ``pipeline/data/zoning_rules.csv`` — version-controlled and
reviewable. At build time the zoning fetcher (GEO-5) loads it, then for every base zone code
actually present in the Kern County zoning layer it emits a row per use case, filling any
(code, use) the curated table does not cover with a conservative default
(``config.ZONING_DEFAULT_PERMISSION``) so the scoring engine never silently treats an
unmapped district as by-right. The effective CSV is written into each build output.

Permissions are one of ``config.ZONING_PERMISSIONS``; use cases are
``config.ZONING_USE_CASES``. The curated values are a documented best-effort reading of the
Kern County Zoning Ordinance (Title 19) and are marked [CONFIRM] pending planning-department
validation — see the ``basis`` column.
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import config

RULES_PATH = Path(__file__).parent / "data" / config.ZONING_RULES_CSV
FIELDNAMES = ("zone_code", "zone_name", "use_case", "permission", "basis")
DEFAULT_BASIS = "DEFAULT — no curated rule for this district; conservative fallback ([CONFIRM])"


def load_rules(path: str | Path = RULES_PATH) -> tuple[dict, dict]:
    """Load the curated reference CSV.

    Returns ``(rules, names)`` where ``rules[(zone_code, use_case)] = {permission, basis}``
    and ``names[zone_code] = zone_name``. Raises ValueError on an unknown permission or use
    case, or a duplicate (code, use) row, so a malformed reference fails the build loudly.
    """
    rules: dict[tuple[str, str], dict] = {}
    names: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for lineno, row in enumerate(csv.DictReader(fh), start=2):
            code = (row.get("zone_code") or "").strip()
            use = (row.get("use_case") or "").strip()
            perm = (row.get("permission") or "").strip()
            if not code or not use:
                raise ValueError(f"{path}:{lineno}: empty zone_code or use_case")
            if perm not in config.ZONING_PERMISSIONS:
                raise ValueError(f"{path}:{lineno}: permission {perm!r} not in {config.ZONING_PERMISSIONS}")
            if use not in config.ZONING_USE_CASES:
                raise ValueError(f"{path}:{lineno}: use_case {use!r} not in {config.ZONING_USE_CASES}")
            if (code, use) in rules:
                raise ValueError(f"{path}:{lineno}: duplicate rule for ({code}, {use})")
            rules[(code, use)] = {"permission": perm, "basis": (row.get("basis") or "").strip()}
            names.setdefault(code, (row.get("zone_name") or "").strip())
    return rules, names


def effective_rows(
    distinct_codes, rules: dict, names: dict, *, default: str | None = None
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Build one row per (present zone_code × use_case), filling gaps with the default.

    Returns ``(rows, missing)`` where ``missing`` is the list of (code, use) pairs not found
    in the curated table (each filled with the default permission and flagged in ``basis``).
    """
    default = default or config.ZONING_DEFAULT_PERMISSION
    rows: list[dict] = []
    missing: list[tuple[str, str]] = []
    for code in sorted(set(distinct_codes)):
        for use in config.ZONING_USE_CASES:
            rule = rules.get((code, use))
            if rule is None:
                missing.append((code, use))
                rows.append({
                    "zone_code": code, "zone_name": names.get(code, ""), "use_case": use,
                    "permission": default, "basis": DEFAULT_BASIS,
                })
            else:
                rows.append({
                    "zone_code": code, "zone_name": names.get(code, ""), "use_case": use,
                    "permission": rule["permission"], "basis": rule["basis"],
                })
    return rows, missing


def write_csv(rows, out_path: str | Path) -> Path:
    """Write effective rules to a CSV with the canonical column order."""
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return out_path
