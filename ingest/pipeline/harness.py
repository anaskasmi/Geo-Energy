"""Ingestion build harness (Service A core).

Orchestrates fetch → clean → reproject → build into a throwaway **staging** directory,
writes the `_SUCCESS` marker last, atomically renames staging into an immutable
`releases/<id>` dir, then **atomically swaps** the `current` symlink (FR-A1). Readers
only ever follow `current`, so they never see a half-built artifact. Idempotent and
re-runnable: every run builds in its own staging dir; the live release is never built
into, renamed over, or deleted, and nothing that runs *after* the swap can take down the
just-committed release. Runs as a one-shot container (`docker compose run --rm ingest`),
never on the request path (FR-A5).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shutil
from pathlib import Path

from . import builder, config, db, fetchers
from .config import Settings
from .fetchers.base import FetchContext, LayerResult
from .logging_setup import get_logger, log_event

_log = get_logger("ingest.harness")

_STAGING_PREFIX = ".staging."


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _generate_build_id() -> str:
    # Timestamp-sortable and unique within a process (microseconds).
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def run(settings: Settings | None = None, *, build_id: str | None = None) -> Path:
    """Run one idempotent build. Returns the path readers should open."""
    cfg = settings or config.from_env()
    build_id = build_id or os.environ.get("BUILD_ID") or _generate_build_id()

    cfg.releases_dir.mkdir(parents=True, exist_ok=True)
    staging = cfg.releases_dir / f"{_STAGING_PREFIX}{build_id}.{os.getpid()}"
    if staging.exists():  # leftover from a previous crash with the same pid — never live
        shutil.rmtree(staging)
    staging.mkdir()

    log_event(_log, "build.start", build_id=build_id, data_dir=str(cfg.data_dir))
    swapped = False
    final: Path | None = None
    try:
        artifact = staging / config.ARTIFACT_NAME
        con = db.connect(artifact, load_httpfs=True, threads=cfg.duckdb_threads)
        try:
            fetchers.load_all()
            chosen = fetchers.iter_fetchers()
            log_event(_log, "fetchers.discovered", count=len(chosen),
                      names=[c.name for c in chosen])

            ctx = FetchContext(work_dir=staging, con=con, settings=cfg, logger=_log)
            results: list[LayerResult] = []
            for cls in chosen:
                fetcher = cls()
                log_event(_log, "layer.start", layer=fetcher.name)
                result = fetcher.fetch(ctx)
                results.append(result)
                log_event(_log, "layer.done", layer=result.name,
                          table=result.table, features=result.feature_count)

            # Convergence point: all fetchers done. Assemble the artifact shell (GEO-12:
            # Hilbert order + R-tree index + validation) then enrich parcels (GEO-13: FR-A4
            # derived columns). Both run on the build connection, before the manifest write.
            assembly = builder.assemble(con, staging, cfg, _log)
            enrichment = builder.enrich(con, staging, cfg, _log)

            _write_manifest(con, staging, build_id, results,
                            assembly=assembly, enrichment=enrichment)
        finally:
            con.close()

        _write_success(staging, build_id)             # marker written last
        final = _promote(cfg, staging, build_id)       # atomic rename into releases/<id>
        _atomic_swap_current(cfg, final)               # atomic symlink swap
        swapped = True

        try:
            _prune_releases(cfg)
        except Exception:  # housekeeping must never take down a committed build
            _log.exception("release prune failed (non-fatal)")

        log_event(_log, "build.success", build_id=build_id, layers=len(results),
                  release=final.name, artifact=str(cfg.current_artifact_path))
        return cfg.current_artifact_path

    except Exception:
        log_event(_log, "build.failed", level=logging.ERROR, build_id=build_id)
        _log.exception("ingestion build failed")
        # Only ever delete dirs that never became live. `current` is left untouched.
        if not swapped:
            shutil.rmtree(staging, ignore_errors=True)
            if final is not None:
                shutil.rmtree(final, ignore_errors=True)
        raise


def _promote(cfg: Settings, staging: Path, build_id: str) -> Path:
    """Atomically rename the staging dir to an immutable releases/<id> (never overwrite)."""
    final = cfg.release_dir(build_id)
    if final.exists():
        # Never rename over an existing release (it may be the live one). Uniquify.
        k = 1
        while (cfg.releases_dir / f"{build_id}__{k}").exists():
            k += 1
        final = cfg.releases_dir / f"{build_id}__{k}"
    os.rename(staging, final)  # atomic within the same filesystem (both under releases/)
    return final


def _write_manifest(
    con,
    release: Path,
    build_id: str,
    results: list[LayerResult],
    *,
    assembly: dict | None = None,
    enrichment: dict | None = None,
) -> None:
    manifest = {
        "build_id": build_id,
        "built_at": _utcnow_iso(),
        "crs": {
            "storage": config.CRS_STORAGE,
            "metric_utm": config.CRS_METRIC_UTM,
            "metric_albers": config.CRS_METRIC_ALBERS,
        },
        "layers": [
            {
                "name": r.name,
                "table": r.table,
                "features": r.feature_count,
                "source": r.source,
            }
            for r in results
        ],
        # Builder provenance (GEO-12 assembly / GEO-13 enrichment summaries).
        "assembly": assembly or {},
        "enrichment": enrichment or {},
    }
    (release / config.MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

    # Mirror into the artifact so readers can introspect provenance without the sidecar.
    con.execute("CREATE TABLE build_manifest (key VARCHAR, value JSON)")
    con.executemany(
        "INSERT INTO build_manifest VALUES (?, ?)",
        [(key, json.dumps(value)) for key, value in manifest.items()],
    )


def _write_success(release: Path, build_id: str) -> None:
    # Written last: its presence means the release is complete.
    (release / config.SUCCESS_MARKER).write_text(
        json.dumps({"build_id": build_id, "completed_at": _utcnow_iso()}) + "\n"
    )


def _atomic_swap_current(cfg: Settings, release: Path) -> None:
    """Atomically repoint `current` → `release` (relative symlink, same filesystem)."""
    current = cfg.current_link
    target = os.path.relpath(release, start=current.parent)  # e.g. "releases/<id>"
    tmp = current.with_name(current.name + ".tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, current)  # atomic rename over the existing symlink


def _prune_releases(cfg: Settings) -> None:
    """Keep the newest `keep_releases` complete releases; drop incomplete zombies.

    Completeness is defined by the `_SUCCESS` marker. The live release (whatever
    `current` resolves to) is never removed. Ordering is by directory mtime, so it is
    correct regardless of the build_id naming scheme.
    """
    if not cfg.releases_dir.exists():
        return
    current_real = cfg.current_link.resolve() if cfg.current_link.exists() else None
    dirs = [p for p in cfg.releases_dir.iterdir() if p.is_dir()]

    def is_live(p: Path) -> bool:
        return current_real is not None and p.resolve() == current_real

    complete, incomplete = [], []
    for p in dirs:
        (complete if (p / config.SUCCESS_MARKER).exists() else incomplete).append(p)

    # Drop incomplete dirs (crash/OOM leftovers, stale staging) that aren't live.
    for p in incomplete:
        if is_live(p):
            continue
        shutil.rmtree(p, ignore_errors=True)
        log_event(_log, "release.pruned", release=p.name, reason="incomplete")

    # Retain newest N complete releases by mtime.
    complete.sort(key=lambda p: p.stat().st_mtime)
    excess = complete[:-cfg.keep_releases] if len(complete) > cfg.keep_releases else []
    for p in excess:
        if is_live(p):
            continue
        shutil.rmtree(p, ignore_errors=True)
        log_event(_log, "release.pruned", release=p.name, reason="retention")
