"""Live land-affordability lookup (GEO-41): the ONE request-path outbound call.

Every other agent tool stays fully local (FR-A5). This module is the deliberate, scoped exception:
it fetches an AREA-LEVEL land/property-cost signal for Kern County, CA from two FREE public APIs —

  * FHFA All-Transactions House Price Index for Kern County via FRED (series ATNHPIUS06029A):
    a relative *price trend* (index + year-over-year %).
  * US Census ACS 5-year median owner-occupied home value (table B25077) for the county: an
    *absolute dollar* anchor.

Both are globally reachable (verified from this host) and free; FRED needs a key, Census works
keyless at low volume (a key lifts the rate limit). The signal is COUNTY-level, so it is the same
for any sub-area drawn inside Kern — a real limitation of free public data, surfaced honestly in
the tool's ``note`` (per-parcel ground truth would need a paid assessor/AVM source).

Hardening mirrors :mod:`app.realtime` exactly: stdlib ``urllib`` (no new dependency), a short
timeout, API keys scrubbed from every log line, and NEVER raising — failures return a structured
``{"ok": False, "error": <client-safe message>}`` so the caller (an agent tool) turns it into a
clean ``ToolError`` the model can narrate. The blocking calls are meant to run in a threadpool.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("api.landvalue")

# Kern County, CA — the only county this app covers.
KERN_STATE_FIPS = "06"
KERN_COUNTY_FIPS = "029"

# FRED series: FHFA All-Transactions House Price Index, Kern County, CA (annual, index).
FRED_KERN_HPI_SERIES = "ATNHPIUS06029A"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# Census ACS 5-year detailed tables; B25077_001E = median value of owner-occupied housing ($).
CENSUS_ACS5_MEDIAN_VALUE_VAR = "B25077_001E"
DEFAULT_ACS_YEAR = 2023  # the latest ACS 5-year vintage confirmed available; we fall back a year.
# Census uses large negative sentinels (e.g. -666666666) for "no estimate".
_CENSUS_NULL_SENTINEL = -100000000

_REQUEST_TIMEOUT_S = 12
_USER_AGENT = "geo-energy/landvalue (+https://localhost)"


def _fred_key() -> str:
    return os.environ.get("FRED_API_KEY", "").strip()


def _census_key() -> str:
    return os.environ.get("CENSUS_API_KEY", "").strip()


def _acs_year() -> int:
    raw = os.environ.get("CENSUS_ACS_YEAR", "").strip()
    if not raw:
        return DEFAULT_ACS_YEAR
    try:
        return int(raw)
    except ValueError:
        log.warning("ignoring malformed CENSUS_ACS_YEAR=%r; using %d", raw, DEFAULT_ACS_YEAR)
        return DEFAULT_ACS_YEAR


def is_configured() -> bool:
    """True iff a live affordability lookup can be attempted (FRED key set, or Census keyless).

    Census works without a key at low volume, so the live tool is always *attemptable*; this
    returns True when at least the trend source (FRED) is configured OR a Census key is present.
    """
    return bool(_fred_key() or _census_key())


def _redact(text: str, *keys: str) -> str:
    """Scrub every supplied API key from a string before it is logged (defence in depth)."""
    for key in keys:
        if key:
            text = text.replace(key, "[REDACTED]")
    return text


def _get_json(url: str, *, redact: tuple[str, ...] = ()):
    """GET ``url`` and parse JSON. Raises on transport/HTTP/parse error (caller maps to a clean dict).

    Keys are scrubbed from any error re-raised/logged via ``redact``.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fred_kern_hpi() -> dict:
    """Latest Kern-County HPI index + year-over-year %. ``{}`` on any failure (logged, key-safe)."""
    key = _fred_key()
    if not key:
        return {}  # FRED requires a key; without one we simply omit the trend signal.
    params = {
        "series_id": FRED_KERN_HPI_SERIES,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "2",
    }
    url = f"{FRED_OBSERVATIONS_URL}?{urllib.parse.urlencode(params)}"
    try:
        data = _get_json(url, redact=(key,))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - best-effort body read
            body = ""
        log.warning("FRED HPI fetch failed: HTTP %s %s", exc.code, _redact(body, key)[:200])
        return {}
    except Exception as exc:  # network / timeout / DNS / parse
        log.warning("FRED HPI fetch error: %s", _redact(str(exc), key))
        return {}

    obs = data.get("observations") or []
    points: list[tuple[str, float]] = []
    for o in obs:
        val = o.get("value")
        if val in (None, ".", ""):  # FRED uses "." for a missing observation
            continue
        try:
            points.append((str(o.get("date", "")), float(val)))
        except (TypeError, ValueError):
            continue
    if not points:
        return {}
    latest_date, latest = points[0]
    out: dict = {"hpi_index": round(latest, 2), "hpi_as_of": latest_date[:4] or None}
    if len(points) >= 2 and points[1][1]:
        prev = points[1][1]
        out["price_trend_yoy_pct"] = round((latest - prev) / prev * 100.0, 1)
    return out


def _census_median_home_value(state_fips: str, county_fips: str) -> dict:
    """County median owner-occupied home value ($) from ACS 5-year. ``{}`` on any failure.

    Tries the configured ACS vintage then the prior year (vintages roll over annually).
    """
    key = _census_key()
    for year in (_acs_year(), _acs_year() - 1):
        params = {
            "get": f"{CENSUS_ACS5_MEDIAN_VALUE_VAR},NAME",
            "for": f"county:{county_fips}",
            "in": f"state:{state_fips}",
        }
        if key:
            params["key"] = key
        url = f"https://api.census.gov/data/{year}/acs/acs5?{urllib.parse.urlencode(params)}"
        try:
            data = _get_json(url, redact=(key,))
        except urllib.error.HTTPError as exc:
            # A 404 here usually means that vintage isn't published yet — try the prior year.
            log.info("Census ACS %d fetch HTTP %s; trying prior vintage", year, exc.code)
            continue
        except Exception as exc:  # network / timeout / DNS / parse
            log.warning("Census ACS fetch error: %s", _redact(str(exc), key))
            return {}
        # Shape: [[header...],[row...]]; row[0] is the median value as a string.
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            continue
        try:
            value = int(float(data[1][0]))
        except (TypeError, ValueError, IndexError):
            continue
        if value <= _CENSUS_NULL_SENTINEL:
            return {}  # explicit "no estimate" sentinel
        return {"median_home_value_usd": value, "acs_vintage": f"{year} ACS 5-year"}
    return {}


def area_affordability(
    *, state_fips: str = KERN_STATE_FIPS, county_fips: str = KERN_COUNTY_FIPS
) -> dict:
    """Fetch an area (county) land/property-cost signal. Blocking — run in a threadpool.

    Never raises and never logs/returns an API key. Shape:
      - success         -> ``{"ok": True, "median_home_value_usd"?, "acs_vintage"?,
                              "hpi_index"?, "price_trend_yoy_pct"?, "hpi_as_of"?, "sources": [...]}``
      - nothing fetched -> ``{"ok": False, "error": <client-safe message>}``

    "ok" requires at least one of the two signals; both failing (no key / offline / geo-block)
    yields a clean error the caller surfaces to the user.
    """
    out: dict = {}
    sources: list[str] = []

    census = _census_median_home_value(state_fips, county_fips)
    if census:
        out.update(census)
        sources.append(f"Census ACS5 {CENSUS_ACS5_MEDIAN_VALUE_VAR}")

    fred = _fred_kern_hpi()
    if fred:
        out.update(fred)
        sources.append(f"FRED {FRED_KERN_HPI_SERIES}")

    if not out:
        return {
            "ok": False,
            "error": "could not retrieve live land-value data (the public data services were "
            "unreachable). Try again shortly.",
        }
    out["ok"] = True
    out["sources"] = sources
    return out
