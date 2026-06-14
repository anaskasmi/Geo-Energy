"""Source acquisition: local override + HTTP download with retry.

All ingest network I/O is isolated here (and in arcgis.py) so fetcher transform logic
stays pure and testable offline. Two ways to acquire a source:

* `local_override(env)` — a pre-staged local file named by an env var. Used by tests and
  air-gapped runs; takes precedence over downloading.
* `http_download(url, dest)` — stream a URL to a file with bounded retries.

Network/`httpfs` is used only during ingest, never on the request path (FR-A5).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from .logging_setup import get_logger, log_event

_log = get_logger("ingest.sources")


class SourceError(RuntimeError):
    """A layer source could not be acquired (missing override or all downloads failed)."""


def local_override(env_var: str) -> Path | None:
    """Return the local file named by `env_var`, or None if unset/blank.

    Raises SourceError if the var is set but points at a path that does not exist — a
    misconfigured override should fail loudly, not silently fall through to a download.
    """
    value = os.environ.get(env_var, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists():
        raise SourceError(f"{env_var}={value!r} but that path does not exist")
    return path


def http_download(
    url: str,
    dest: str | Path,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    backoff: float = 1.0,
    headers: dict | None = None,
    logger=None,
) -> Path:
    """Stream `url` to `dest` with bounded retries; atomic on success.

    Bytes land in a sibling `.part` file that is `os.replace`d into place only after the
    full body is written, so a crash/timeout never leaves a truncated source behind.
    `transport` is injectable for offline tests (httpx.MockTransport).
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    log = logger or _log
    last_err: Exception | None = None
    try:
        for attempt in range(1, retries + 1):
            try:
                with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as client:
                    with client.stream("GET", url, headers=headers) as resp:
                        resp.raise_for_status()
                        with open(part, "wb") as fh:
                            for chunk in resp.iter_bytes():
                                fh.write(chunk)
                os.replace(part, dest)
                log_event(log, "source.downloaded", url=url, dest=str(dest), bytes=dest.stat().st_size)
                return dest
            except Exception as err:  # noqa: BLE001 — retry any transport/HTTP failure
                last_err = err
                log_event(log, "source.download_retry", url=url, attempt=attempt, error=str(err))
                if attempt < retries:
                    time.sleep(backoff * attempt)
        raise SourceError(f"failed to download {url} after {retries} attempts: {last_err}") from last_err
    finally:
        # Drop any half-written staging file. A successful attempt already os.replace'd it
        # into `dest`, so this only fires on the failure path.
        part.unlink(missing_ok=True)
