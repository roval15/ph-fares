"""Nominatim geocoding layer for the ph-fares commute guide.

Uses OpenStreetMap Nominatim via urllib (stdlib only).  Results are cached
to ``data/geocode_cache.json`` so repeated queries hit zero HTTP.

Level B reuses the same cache file — callers see no difference.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_urlopen = urllib.request.urlopen

# ---------------------------------------------------------------------------
# Configuration — patchable for tests
# ---------------------------------------------------------------------------

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "geocode_cache.json"

_USER_AGENT = "ph-fares-commute-guide/0.1 (https://github.com/roval15/ph-fares)"
_MIN_INTERVAL = 1.0  # seconds between requests (Nominatim rate limit)

_last_request_ts: float = 0.0  # module-level, resettable for tests

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_cache: dict | None = None


def _load_cache() -> dict:
    """Load the on-disk cache; returns {} on any error."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with CACHE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
    except (OSError, json.JSONDecodeError, ValueError):
        data = {}
    _cache = data
    return _cache


def _save_cache() -> None:
    """Persist the in-memory cache to disk (atomic-ish via temp + replace)."""
    if _cache is None:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(_cache, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    os.replace(str(tmp), str(CACHE_PATH))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def geocode(query: str) -> list[dict]:
    """Geocode *query* via Nominatim (PH only, up to 5 results).

    Returns a list of dicts ``[{"display_name": str, "lat": float, "lon": float}, ...]``.
    Hits are served from ``data/geocode_cache.json`` — no HTTP on cache hit.
    """
    cache = _load_cache()

    # Cache hit
    if query in cache:
        return cache[query]

    # Rate-limit: wait if needed
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    # Build request
    params = urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "ph"}
    )
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with _urlopen(req, timeout=10) as resp:
            raw = resp.read()
    finally:
        _last_request_ts = time.monotonic()

    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(
            f"Nominatim returned non-list JSON for query {query!r}: "
            f"{type(data).__name__}"
        )

    results: list[dict] = []
    for item in data[:5]:
        results.append(
            {
                "display_name": item.get("display_name", ""),
                "lat": float(item.get("lat", 0)),
                "lon": float(item.get("lon", 0)),
            }
        )

    cache[query] = results
    _save_cache()

    return results
