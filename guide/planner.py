"""Trip planner — thin layer over finder primitives that produces
itineraries with fare breakdowns.

``plan(from_lat, from_lon, to_lat, to_lon)`` returns a structured result
with walk + ride legs (and optionally MRT-3), sorted cheapest-first.
No-route cases return a machine-readable dict, never raise.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import phfares
from guide.feedback import freshness as _freshness
from guide.finder import (
    _build_stop_index,
    _haversine_m,
    _load_mrt3,
    _load_sequences,
    candidate_direct_routes,
    nearest_mrt3_station,
    nearest_stops,
    ride_segment,
)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FARES_PATH = _DATA_DIR / "fares.json"


def _load_fares_modes() -> dict:
    with open(_FARES_PATH, encoding="utf-8") as fh:
        return json.load(fh)["modes"]


def _mrt3_fare(station_count: int) -> Decimal | None:
    """Return MRT-3 fare for *station_count* using station_bands, or None."""
    modes = _load_fares_modes()
    mrt3 = modes.get("mrt3", {})
    for band in mrt3.get("station_bands", []):
        if band["min_stations"] <= station_count <= band["max_stations"]:
            return Decimal(str(band["fare"]))
    return None


def _mrt3_discount_note() -> str:
    modes = _load_fares_modes()
    return modes.get("mrt3", {}).get("notes", "")


def _sort_options(options: list[dict], tier_of) -> None:
    """Sort cheapest-first; among EQUAL total fares, options whose ride legs
    include any disputed-tier route sink below others.
    Stable: never reorders otherwise."""
    def _disputed(opt):
        for leg in opt["legs"]:
            if leg["type"] == "ride":
                rid = leg.get("route_id")
                if rid is not None and tier_of(rid) == "disputed":
                    return True
        return False

    options.sort(
        key=lambda o: (o["total_fare"], 1 if _disputed(o) else 0)
    )


def _ride_shape(
    sequences: list[dict],
    route_id: str,
    board_stop: dict,
    alight_stop: dict,
) -> list[list[float]]:
    """Return ordered [[lat,lon], ...] from BOARD stop to ALIGHT stop inclusive.

    Searches *sequences* for the matching route_id, locates the board and
    alight stops by stop_id, and slices between them.  If the stored sequence
    runs opposite to travel direction (board index > alight index), the slice
    is reversed so the result always starts at the board stop and ends at the
    alight stop.

    Fallback (route not found or stop_id missing): two-point polyline.
    """
    board_id = board_stop["stop_id"]
    alight_id = alight_stop["stop_id"]
    fallback = [[board_stop["lat"], board_stop["lon"]],
                [alight_stop["lat"], alight_stop["lon"]]]

    for seq in sequences:
        if seq["route_id"] != route_id:
            continue
        stops = seq["stops"]
        board_idx: int | None = None
        alight_idx: int | None = None
        for i, s in enumerate(stops):
            if s["stop_id"] == board_id:
                board_idx = i
            if s["stop_id"] == alight_id:
                alight_idx = i
        if board_idx is None or alight_idx is None:
            return fallback
        if board_idx <= alight_idx:
            return [[s["lat"], s["lon"]] for s in stops[board_idx:alight_idx + 1]]
        else:
            return [[s["lat"], s["lon"]] for s in stops[alight_idx:board_idx + 1][::-1]]

    return fallback


def plan(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    walk_radius_m: float = 400,
    mrt_radius_m: float = 600,
) -> dict:
    """Plan a trip from origin to destination.

    Returns ``{"status": "ok", "options": [...]}`` or
    ``{"status": "no_route", "message": "...", "from": {...}, "to": {...}}``.
    """
    options: list[dict] = []

    # Load sequences once for shape computation
    sequences = _load_sequences()

    # --- Jeepney options ---
    candidates = candidate_direct_routes(
        from_lat, from_lon, to_lat, to_lon, walk_radius_m
    )
    seen_modes_routes: set[tuple[str, str]] = set()

    for cand in candidates:
        for mode in ("jeepney_traditional", "jeepney_modern"):
            key = (mode, cand["route_id"])
            if key in seen_modes_routes:
                continue
            seen_modes_routes.add(key)

            ride_km = cand["ride_km"]
            fare_val = phfares.fare(mode, ride_km)

            legs = [
                {
                    "type": "walk",
                    "from": {"lat": from_lat, "lon": from_lon},
                    "to": {"lat": cand["board_stop"]["lat"],
                           "lon": cand["board_stop"]["lon"]},
                    "distance_m": cand["walk_from_m"],
                },
                {
                    "type": "ride",
                    "mode": mode,
                    "route_id": cand["route_id"],
                    "route_long_name": cand["route_long_name"],
                    "board_stop": cand["board_stop"],
                    "alight_stop": cand["alight_stop"],
                    "distance_km": ride_km,
                    "shape": _ride_shape(sequences, cand["route_id"],
                                         cand["board_stop"], cand["alight_stop"]),
                },
                {
                    "type": "walk",
                    "from": {"lat": cand["alight_stop"]["lat"],
                             "lon": cand["alight_stop"]["lon"]},
                    "to": {"lat": to_lat, "lon": to_lon},
                    "distance_m": cand["walk_to_m"],
                },
            ]

            options.append({
                "legs": legs,
                "fare_breakdown": {mode: fare_val},
                "total_fare": fare_val,
                "notes": [],
            })

    # --- MRT-3 option ---
    mrt_from = nearest_mrt3_station(from_lat, from_lon)
    mrt_to = nearest_mrt3_station(to_lat, to_lon)

    if mrt_from is not None and mrt_to is not None:
        mrt3_data = _load_mrt3()
        stations = mrt3_data["stations"]
        idx_a: int | None = None
        idx_b: int | None = None
        for i, st in enumerate(stations):
            if st["stop_id"] == mrt_from["stop_id"]:
                idx_a = i
            if st["stop_id"] == mrt_to["stop_id"]:
                idx_b = i

        if idx_a is not None and idx_b is not None:
            station_count = abs(idx_a - idx_b)
            mrt_fare = _mrt3_fare(station_count)
            if mrt_fare is not None:
                # Walk distances for MRT legs
                walk_from_m = round(
                    _haversine_m(from_lat, from_lon,
                                 mrt_from["lat"], mrt_from["lon"]), 1
                )
                walk_to_m = round(
                    _haversine_m(to_lat, to_lon,
                                 mrt_to["lat"], mrt_to["lon"]), 1
                )

                # Ride km between the two stations
                ride_m = _haversine_m(
                    mrt_from["lat"], mrt_from["lon"],
                    mrt_to["lat"], mrt_to["lon"],
                )

                legs = [
                    {
                        "type": "walk",
                        "from": {"lat": from_lat, "lon": from_lon},
                        "to": {"lat": mrt_from["lat"], "lon": mrt_from["lon"]},
                        "distance_m": walk_from_m,
                    },
                    {
                        "type": "ride",
                        "mode": "mrt3",
                        "route_id": mrt3_data["route_id"],
                        "route_long_name": mrt3_data["route_long_name"],
                        "board_stop": mrt_from,
                        "alight_stop": mrt_to,
                        "distance_km": round(ride_m / 1000, 3),
                        "shape": _ride_shape(sequences, mrt3_data["route_id"],
                                             mrt_from, mrt_to),
                    },
                    {
                        "type": "walk",
                        "from": {"lat": mrt_to["lat"], "lon": mrt_to["lon"]},
                        "to": {"lat": to_lat, "lon": to_lon},
                        "distance_m": walk_to_m,
                    },
                ]

                discount_note = _mrt3_discount_note()
                notes = [discount_note] if discount_note else []

                options.append({
                    "legs": legs,
                    "fare_breakdown": {"mrt3": mrt_fare},
                    "total_fare": mrt_fare,
                    "notes": notes,
                })

    # Compute freshness for every ride-leg route_id once
    route_ids_seen: set[str] = set()
    for opt in options:
        for leg in opt["legs"]:
            if leg["type"] == "ride" and "route_id" in leg:
                route_ids_seen.add(leg["route_id"])
    _freshness_cache: dict[str, dict] = {}
    for rid in route_ids_seen:
        try:
            _freshness_cache[rid] = _freshness(rid)
        except Exception:
            _freshness_cache[rid] = {"tier": "gray"}

    def _tier_of(route_id: str) -> str | None:
        return _freshness_cache.get(route_id, {}).get("tier")

    # Sort cheapest first; among equal fares, disputed options sink
    _sort_options(options, _tier_of)

    if not options:
        return {
            "status": "no_route",
            "message": "No direct route found between the given points.",
            "from": {"lat": from_lat, "lon": from_lon},
            "to": {"lat": to_lat, "lon": to_lon},
        }

    return {"status": "ok", "options": options}
