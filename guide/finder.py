"""Route finder core — pure, composable primitives for the commute guide.

All data is loaded lazily from JSON files under ``data/``.  Module-level
``_*_PATH`` variables are patchable so that tests can inject synthetic
fixtures without touching real files.

Level-B reuse
-------------
Level B's BFS over the transfer graph reuses these pure primitives:

* ``nearest_stops`` — find stops near a coordinate.
* ``routes_at_stop`` — list routes serving a stop.
* ``ride_segment`` — along-route km between two stops of the same route.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_STOP_SEQUENCES_PATH = _DATA_DIR / "stop_sequences.json"
_MRT3_STATIONS_PATH = _DATA_DIR / "mrt3_stations.json"

_EARTH_RADIUS_M = 6_371_000


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return haversine distance in metres between two lat/lon points."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return _EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Lazy data loaders
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_sequences() -> list[dict]:
    """Load stop_sequences.json (list of route-sequence dicts)."""
    with open(_STOP_SEQUENCES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _load_mrt3() -> dict:
    """Load mrt3_stations.json."""
    with open(_MRT3_STATIONS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _build_stop_index() -> dict[str, dict]:
    """Build a stop_id → stop dict index from ALL sequence stops (deduped).

    Each sequence stop embeds its own lat/lon so the index is self-contained;
    correctness does not depend on cross-file joins with stops_mandaluyong.json.
    """
    idx: dict[str, dict] = {}
    for seq in _load_sequences():
        for s in seq["stops"]:
            sid = s["stop_id"]
            if sid not in idx:
                idx[sid] = {"stop_id": sid, "stop_name": s["stop_name"],
                            "lat": s["lat"], "lon": s["lon"]}
    return idx


# ---------------------------------------------------------------------------
# Public primitives
# ---------------------------------------------------------------------------

def nearest_stops(lat: float, lon: float, radius_m: float = 400) -> list[dict]:
    """Return stops within *radius_m* of (lat, lon), sorted nearest-first.

    Each result is ``{"stop_id", "stop_name", "lat", "lon", "distance_m"}``
    where ``distance_m`` is rounded to 1 decimal.
    """
    idx = _build_stop_index()
    results: list[dict] = []
    for s in idx.values():
        d = _haversine_m(lat, lon, s["lat"], s["lon"])
        if d <= radius_m:
            results.append({
                "stop_id": s["stop_id"],
                "stop_name": s["stop_name"],
                "lat": s["lat"],
                "lon": s["lon"],
                "distance_m": round(d, 1),
            })
    results.sort(key=lambda r: r["distance_m"])
    return results


def routes_at_stop(stop_id: str) -> list[dict]:
    """Return routes that serve *stop_id*.

    Each result is ``{"route_id", "route_long_name"}``.

    Level-B note: this primitive is reused by the BFS over the transfer graph
    to discover which routes a passenger can board or alight at a given stop.
    """
    idx = _build_stop_index()
    if stop_id not in idx:
        return []
    seen: set[str] = set()
    results: list[dict] = []
    for seq in _load_sequences():
        for s in seq["stops"]:
            if s["stop_id"] == stop_id:
                rid = seq["route_id"]
                if rid not in seen:
                    seen.add(rid)
                    results.append({"route_id": rid,
                                    "route_long_name": seq["route_long_name"]})
                break
    return results


def ride_segment(sequence: dict, stop_a_id: str, stop_b_id: str) -> float:
    """Return along-route km between *stop_a_id* and *stop_b_id* in *sequence*.

    If ``shape_dist_traveled`` values are present and non-zero they are used
    (alight minus board).  Otherwise falls back to the sum of haversine
    distances between consecutive stops in the ordered list from A to B
    (inclusive of both endpoints' segments).

    Level-B note: this is the primitive used by the transfer-graph BFS to
    compute ride distances for multi-leg itineraries.
    """
    stops = sequence["stops"]

    # Locate board and alight indices
    board_idx: int | None = None
    alight_idx: int | None = None
    for i, s in enumerate(stops):
        if s["stop_id"] == stop_a_id:
            board_idx = i
        if s["stop_id"] == stop_b_id:
            alight_idx = i
    if board_idx is None or alight_idx is None:
        raise ValueError(
            f"Stops {stop_a_id} or {stop_b_id} not found in sequence "
            f"for route {sequence.get('route_id', '?')}"
        )
    if board_idx >= alight_idx:
        raise ValueError(
            f"Board index ({board_idx}) must precede alight index ({alight_idx})"
        )

    # Try shape_dist_traveled first
    sdt_a = stops[board_idx].get("shape_dist_traveled")
    sdt_b = stops[alight_idx].get("shape_dist_traveled")
    if sdt_a is not None and sdt_b is not None and (sdt_a or sdt_b):
        return round((sdt_b - sdt_a) / 1000, 3)

    # Fallback: sum haversine between consecutive stops board..alight
    total_m = 0.0
    for i in range(board_idx, alight_idx):
        total_m += _haversine_m(
            stops[i]["lat"], stops[i]["lon"],
            stops[i + 1]["lat"], stops[i + 1]["lon"],
        )
    return round(total_m / 1000, 3)


def candidate_direct_routes(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    walk_radius_m: float = 400,
) -> list[dict]:
    """Find direct-route candidates from origin to destination.

    For each route sequence the **first** stop within *walk_radius_m* of the
    origin is the boarding stop and the **last** stop within *walk_radius_m*
    of the destination is the alight stop.  Board must precede alight in the
    sequence.

    Returns a list sorted by ride_km ascending, each element::

        {
            "route_id": str,
            "route_long_name": str,
            "board_stop": {"stop_id", "stop_name", "lat", "lon", "distance_m"},
            "alight_stop": {"stop_id", "stop_name", "lat", "lon", "distance_m"},
            "ride_km": float,
            "walk_from_m": float,   # haversine origin → board stop
            "walk_to_m": float,     # haversine dest → alight stop
        }
    """
    candidates: list[dict] = []

    for seq in _load_sequences():
        stops = seq["stops"]

        # First stop near origin
        board: dict | None = None
        for s in stops:
            d = _haversine_m(from_lat, from_lon, s["lat"], s["lon"])
            if d <= walk_radius_m:
                board = {"stop_id": s["stop_id"], "stop_name": s["stop_name"],
                         "lat": s["lat"], "lon": s["lon"],
                         "distance_m": round(d, 1)}
                break
        if board is None:
            continue

        # Last stop near destination
        alight: dict | None = None
        for s in reversed(stops):
            d = _haversine_m(to_lat, to_lon, s["lat"], s["lon"])
            if d <= walk_radius_m:
                alight = {"stop_id": s["stop_id"], "stop_name": s["stop_name"],
                          "lat": s["lat"], "lon": s["lon"],
                          "distance_m": round(d, 1)}
                break
        if alight is None:
            continue

        # Ensure board precedes alight
        board_idx = next(i for i, s in enumerate(stops) if s["stop_id"] == board["stop_id"])
        alight_idx = next(i for i, s in enumerate(stops) if s["stop_id"] == alight["stop_id"])
        if board_idx >= alight_idx:
            continue

        ride_km = ride_segment(seq, board["stop_id"], alight["stop_id"])
        walk_from_m = round(_haversine_m(from_lat, from_lon, board["lat"], board["lon"]), 1)
        walk_to_m = round(_haversine_m(to_lat, to_lon, alight["lat"], alight["lon"]), 1)

        candidates.append({
            "route_id": seq["route_id"],
            "route_long_name": seq["route_long_name"],
            "board_stop": board,
            "alight_stop": alight,
            "ride_km": ride_km,
            "walk_from_m": walk_from_m,
            "walk_to_m": walk_to_m,
        })

    candidates.sort(key=lambda c: c["ride_km"])
    return candidates


def nearest_mrt3_station(lat: float, lon: float) -> dict | None:
    """Return nearest MRT-3 station dict or ``None`` if beyond 600 m."""
    mrt3 = _load_mrt3()
    best: dict | None = None
    best_dist = float("inf")
    for st in mrt3["stations"]:
        d = _haversine_m(lat, lon, st["lat"], st["lon"])
        if d < best_dist:
            best_dist = d
            best = {"stop_id": st["stop_id"], "stop_name": st["stop_name"],
                     "lat": st["lat"], "lon": st["lon"],
                     "distance_m": round(d, 1)}
    if best is None or best["distance_m"] > 600:
        return None
    return best
