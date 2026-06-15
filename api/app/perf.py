"""API performance helpers (GEO-18): ETag/304 for idempotent GETs + an LRU result cache.

- :class:`ETagMiddleware` buffers GET responses, sets a strong ``ETag``, and returns ``304 Not
  Modified`` when the client's ``If-None-Match`` matches — cutting repeat payload for
  /api/explain and /api/context (and /, /api/health).
- :class:`ResultCache` is a small thread-safe LRU keyed by the scoring request hash, so an
  identical /api/score (same polygon + use_case + weights + thresholds + page) is served from
  memory. Gzip itself is Starlette's GZipMiddleware, wired in main.

The artifact is opened once at startup and never mutated, so cached results never go stale within
a process; a new build means a restart, which empties the cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("api.access")


def _opaque(tag: str) -> str:
    """The opaque-tag of an entity-tag for weak comparison (strip ``W/`` and surrounding ws)."""
    tag = tag.strip()
    return tag[2:] if tag.startswith("W/") else tag


def if_none_match(header: str | None, etag: str) -> bool:
    """RFC 9110 13.1.2 If-None-Match: ``*`` matches anything; else weak-compare against the list."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    target = _opaque(etag)
    return any(_opaque(part) == target for part in header.split(","))


class ETagMiddleware(BaseHTTPMiddleware):
    """Add a WEAK ETag to GET responses and honour ``If-None-Match`` with a 304.

    The validator is computed over the UNCOMPRESSED body (this middleware is inner; GZip is
    outer), so it is intentionally WEAK — it identifies the resource, not the byte stream, and so
    stays valid across content-codings (RFC 9110 8.8.1). The 304 always carries
    ``Vary: Accept-Encoding`` (the gzip layer is always present) per RFC 9110 15.4.5; the 200's
    Vary is added by GZipMiddleware downstream when it actually compresses.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method != "GET" or response.status_code != 200:
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        etag = 'W/"' + hashlib.sha1(body).hexdigest() + '"'  # noqa: S324 (non-crypto cache tag)
        headers = dict(response.headers)
        headers["etag"] = etag

        if if_none_match(request.headers.get("if-none-match"), etag):
            not_modified = Response(status_code=304)
            not_modified.headers["etag"] = etag
            not_modified.headers["vary"] = "Accept-Encoding"
            if "cache-control" in headers:
                not_modified.headers["cache-control"] = headers["cache-control"]
            return not_modified

        rebuilt = Response(
            content=body,
            status_code=response.status_code,
            media_type=response.media_type,
        )
        # Preserve original headers (content-type, etc.) + the new ETag; drop content-length
        # (Response recomputes it for the buffered body).
        for key, value in headers.items():
            if key.lower() != "content-length":
                rebuilt.headers[key] = value
        return rebuilt


class RequestTimingMiddleware:
    """Pure-ASGI middleware (GEO-37): log ONE structured INFO line per HTTP request.

    Emits method, route template (low-cardinality, e.g. ``/api/explain/{parcel_id}`` not
    ``/api/explain/123``; falls back to the raw path), status, duration_ms, and ``X-Cache`` when
    present. Added OUTERMOST in main so the duration covers the whole handler + inner middleware
    (ETag/GZip) without double counting. Deliberately cheap: it only inspects the response-start
    message and never buffers the body or touches request bodies/secrets.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        info: dict[str, Any] = {"status": 0, "x_cache": None}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                info["status"] = message["status"]
                for key, value in message.get("headers", []):
                    if key.lower() == b"x-cache":
                        info["x_cache"] = value.decode("latin-1")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            route = scope.get("route")
            path = getattr(route, "path", None) or scope.get("path", "")
            method = scope.get("method", "-")
            cache = f" x_cache={info['x_cache']}" if info["x_cache"] else ""
            log.info(
                "request method=%s path=%s status=%d duration_ms=%.1f%s",
                method, path, info["status"], duration_ms, cache,
            )


class ResultCache:
    """A bounded, thread-safe LRU mapping a request hash -> response dict."""

    def __init__(self, maxsize: int = 128) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, Any]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self.hits += 1
                return self._store[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0


def score_cache_key(
    geometry: dict,
    use_case: str,
    weights: dict[str, float],
    thresholds: dict,
    prohibited: list[str],
    limit: int,
    offset: int,
) -> str:
    """Stable hash over everything that determines a /api/score result (§8 API: cache key)."""
    payload = json.dumps(
        {
            "geometry": geometry,
            "use_case": use_case,
            "weights": {k: round(v, 6) for k, v in sorted(weights.items())},
            "thresholds": {k: thresholds[k] for k in sorted(thresholds)},
            "prohibited": sorted(prohibited),
            "limit": limit,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 (non-crypto cache key)


# Process-wide score cache (sized for a single VPS; small responses).
score_cache = ResultCache(maxsize=256)
