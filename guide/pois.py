"""POI (Point of Interest) layer for the ph-fares commute guide.

Provides ``nearby_pois()`` which merges offline MRT-3 station data with
live Overpass API results.  Results are cached to ``data/poi_cache.json``
so repeated queries for the same spot hit zero HTTP.

Runtime: stdlib only — no third-party imports.
"""

import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

_urlopen = urllib.request.urlopen

# ---------------------------------------------------------------------------
# Configuration — patchable for tests
# ---------------------------------------------------------------------------

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "poi_cache.json"
_MRT_PATH = Path(__file__).resolve().parent.parent / "data" / "mrt3_stations.json"

_USER_AGENT = "ph-fares-commute-guide/0.1 (https://github.com/roval15/ph-fares)"
_MIN_INTERVAL = 1.0  # seconds between requests (Overpass etiquette)
_TIMEOUT = 10  # seconds per HTTP request

_last_request_ts: float = 0.0  # module-level, resettable for tests

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

logger = logging.getLogger(__name__)

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
# Geometry helpers
# ---------------------------------------------------------------------------

_EARTH_R = 6_371_000  # metres


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Return haversine distance in metres (rounded to int)."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return int(_EARTH_R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _cache_key(lat: float, lon: float, radius_m: int) -> str:
    """Cache key from coords rounded to ~50 m grid."""
    return f"{round(lat, 3):.3f},{round(lon, 3):.3f},{radius_m}"


# ---------------------------------------------------------------------------
# Category derivation
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = [
    "railway",
    "tourism",
    "amenity",
    "shop",
    "building",
]


def _category(tags: dict) -> str:
    """Derive a human-readable category string from OSM tags (primary tag wins)."""
    if tags.get("railway") == "station":
        return "station"
    for key in _CATEGORY_ORDER:
        val = tags.get(key)
        if val:
            return val
    return "building"


# ---------------------------------------------------------------------------
# MRT-3 offline source
# ---------------------------------------------------------------------------

_mrt_stations: list[dict] | None = None


def _load_mrt_stations() -> list[dict]:
    """Load MRT-3 stations from the JSON file on disk."""
    global _mrt_stations
    if _mrt_stations is not None:
        return _mrt_stations
    try:
        with _MRT_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        _mrt_stations = data.get("stations", [])
    except (OSError, json.JSONDecodeError, ValueError):
        _mrt_stations = []
    return _mrt_stations


def _mrt_nearby(lat: float, lon: float, radius_m: int) -> list[dict]:
    """Return MRT-3 stations within *radius_m* of (lat, lon)."""
    results: list[dict] = []
    for s in _load_mrt_stations():
        slat = float(s["lat"])
        slon = float(s["lon"])
        d = _haversine(lat, lon, slat, slon)
        if d <= radius_m:
            results.append(
                {
                    "name": s["stop_name"].strip(),
                    "category": "station",
                    "distance_m": d,
                    "lat": slat,
                    "lon": slon,
                    "source": "mrt",
                }
            )
    return results


# ---------------------------------------------------------------------------
# Overpass source
# ---------------------------------------------------------------------------

_OVERPASS_QUERY = """\
[out:json][timeout:{timeout}];
(
  node["name"]["amenity"](around:{radius},{lat},{lon});
  node["name"]["shop"](around:{radius},{lat},{lon});
  node["name"]["tourism"](around:{radius},{lat},{lon});
  node["railway"="station"]["name"](around:{radius},{lat},{lon});
  node["name"]["building"](around:{radius},{lat},{lon});
  way["name"]["amenity"](around:{radius},{lat},{lon});
  way["name"]["shop"](around:{radius},{lat},{lon});
  way["name"]["tourism"](around:{radius},{lat},{lon});
  way["railway"="station"]["name"](around:{radius},{lat},{lon});
  way["name"]["building"](around:{radius},{lat},{lon});
);
out center {limit};
"""


def _build_overpass_query(lat: float, lon: float, radius_m: int, limit: int) -> str:
    """Build an Overpass QL around-query string."""
    return _OVERPASS_QUERY.format(
        timeout=10,
        radius=radius_m,
        lat=lat,
        lon=lon,
        limit=limit,
    )


def _parse_overpass_elements(elements: list[dict]) -> list[dict]:
    """Parse Overpass JSON elements into POI dicts (skip unnamed)."""
    results: list[dict] = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            continue
        # Get coordinates: node has lat/lon, way has center.lat/center.lon
        if el.get("type") == "node":
            elat = el.get("lat")
            elon = el.get("lon")
        else:
            center = el.get("center", {})
            elat = center.get("lat")
            elon = center.get("lon")
        if elat is None or elon is None:
            continue
        results.append(
            {
                "name": name.strip(),
                "category": _category(tags),
                "lat": float(elat),
                "lon": float(elon),
                "source": "overpass",
            }
        )
    return results


def _fetch_overpass(lat: float, lon: float, radius_m: int, limit: int) -> list[dict]:
    """Query Overpass mirrors with failover; returns POI list or empty."""
    global _last_request_ts
    query = _build_overpass_query(lat, lon, radius_m, limit)

    for mirror_url in OVERPASS_MIRRORS:
        # Rate limit
        global _last_request_ts
        elapsed = time.monotonic() - _last_request_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

        params = urllib.parse.urlencode({"data": query})
        url = f"{mirror_url}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

        try:
            with _urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
            _last_request_ts = time.monotonic()
            data = json.loads(raw)
            elements = data.get("elements", [])
            return _parse_overpass_elements(elements)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TimeoutError) as exc:
            _last_request_ts = time.monotonic()
            logger.warning("Overpass mirror %s failed: %s", mirror_url, exc)
            warnings.warn(
                f"Overpass mirror {mirror_url} failed: {exc}",
                stacklevel=2,
            )
            continue

    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def nearby_pois(lat: float, lon: float, radius_m: int = 250, limit: int = 5) -> list[dict]:
    """Find nearby POIs: MRT-3 stations (offline) + Overpass named places.

    Returns a list sorted by distance::

        [{"name": str, "category": str, "distance_m": int,
          "lat": float, "lon": float, "source": str}, ...]

    Graceful degradation: Overpass failures are swallowed; offline results
    (or an empty list) are always returned.  The caller never sees an exception.
    """
    # 1. Offline MRT stations (always free, no network)
    results = _mrt_nearby(lat, lon, radius_m)

    # 2. Overpass online results (with caching)
    cache = _load_cache()
    key = _cache_key(lat, lon, radius_m)

    if key in cache:
        overpass_pois = cache[key]
    else:
        # Generous limit for Overpass so we can sort + cap locally
        overpass_pois = _fetch_overpass(lat, lon, radius_m, limit=max(limit * 4, 20))
        # Compute distance for each
        for poi in overpass_pois:
            poi["distance_m"] = _haversine(lat, lon, poi["lat"], poi["lon"])
        # Cache only the overpass results
        cache[key] = overpass_pois
        _save_cache()

    results.extend(overpass_pois)

    # 3. Sort by distance, cap at limit
    results.sort(key=lambda p: p["distance_m"])
    return results[:limit]
