"""Trip tracking ledger — GPS ride-along for the Commute Guide.

Tracks live GPS positions during a jeepney ride and records the journey
to a JSONL ledger under ``data/trips/``.  Designed for crowdsourcing
real jeepney paths.

Privacy note
------------
No IP logging, no user accounts.  ``alias`` is optional free text from
the client's localStorage — never required.

Level-B reuse
-------------
Server endpoints in ``web/server.py`` delegate to the plain functions
here so that tests can drive the tracker directly without HTTP.
"""

from __future__ import annotations

import json
import math
import os
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TRIPS_DIR = _DATA_DIR / "trips"
_TRIPS_LEDGER = _TRIPS_DIR / "trips.jsonl"


# ---------------------------------------------------------------------------
# Clock helper (patchable in tests)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return current UTC time.  Patchable for deterministic tests."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

_ENABLED_VALUES = frozenset({"1", "true", "on"})


def is_enabled() -> bool:
    """Check if tracking is enabled via the TRACKING_ENABLED env var.

    Reads the environment at call time (not import time) so tests can
    flip it with ``monkeypatch.setenv`` / ``monkeypatch.delenv``.
    """
    raw = os.environ.get("TRACKING_ENABLED", "")
    return raw.strip().lower() in _ENABLED_VALUES


# ---------------------------------------------------------------------------
# In-memory trip state
# ---------------------------------------------------------------------------

# Active trips keyed by trip_id.  Shape:
#   { trip_id: { "start_ts": float, "points": [...], "stopped": bool,
#                "summary": dict|None, "alias": str|None } }
_trips: dict[str, dict] = {}


def reset() -> None:
    """Clear all in-memory trip state (for tests)."""
    _trips.clear()


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in km between two points."""
    rlat1 = math.radians(lat1)
    rlon1 = math.radians(lon1)
    rlat2 = math.radians(lat2)
    rlon2 = math.radians(lon2)

    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1

    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_point(body: dict) -> str | None:
    """Return an error message string if the point is invalid, else None."""
    for key in ("lat", "lon", "ts", "accuracy"):
        val = body.get(key)
        if val is None:
            return f"Missing required field: {key}"
        try:
            fval = float(val)
        except (ValueError, TypeError):
            return f"Non-numeric {key}: {val!r}"
        if not math.isfinite(fval):
            return f"Non-finite {key}: {val!r}"

    lat = float(body["lat"])
    lon = float(body["lon"])
    accuracy = float(body["accuracy"])

    if lat < -90 or lat > 90:
        return f"lat out of range: {lat}"
    if lon < -180 or lon > 180:
        return f"lon out of range: {lon}"
    if accuracy > 200:
        return f"accuracy too low: {accuracy}m (max 200)"

    return None


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------

def start_trip(alias: str | None = None) -> dict:
    """Start a new tracking trip.

    Returns ``{"trip_id": "<uuid4>"}``.
    """
    trip_id = str(uuid.uuid4())
    _trips[trip_id] = {
        "start_ts": _now().timestamp(),
        "points": [],
        "stopped": False,
        "summary": None,
        "alias": alias,
    }
    return {"trip_id": trip_id}


def add_point(trip_id: str, lat: float, lon: float, ts: float, accuracy: float) -> dict:
    """Append a GPS point to an active trip.

    Returns ``{"ok": True}`` on success.

    Raises
    ------
    KeyError
        If *trip_id* is unknown (callers map to 404).
    ValueError
        If validation fails (callers map to 400).
    RuntimeError
        If the trip is already stopped (callers map to 409).
    """
    if trip_id not in _trips:
        raise KeyError(f"Unknown trip_id: {trip_id}")

    trip = _trips[trip_id]
    if trip["stopped"]:
        raise RuntimeError(f"Trip {trip_id} already stopped")

    point = {
        "lat": lat,
        "lon": lon,
        "ts": ts,
        "accuracy": accuracy,
        "received_at": _now().isoformat(),
    }
    trip["points"].append(point)
    return {"ok": True}


def stop_trip(trip_id: str, alias: str | None = None) -> dict:
    """Stop a tracking trip and persist the summary.

    If the trip is already stopped, returns the existing summary
    (idempotent — no duplicate line written).

    Returns the summary dict.

    Raises
    ------
    KeyError
        If *trip_id* is unknown (callers map to 404).
    """
    if trip_id not in _trips:
        raise KeyError(f"Unknown trip_id: {trip_id}")

    trip = _trips[trip_id]

    # Idempotent: already stopped
    if trip["stopped"] and trip["summary"] is not None:
        return trip["summary"]

    points = trip["points"]
    point_count = len(points)

    # Compute start/end timestamps
    if point_count > 0:
        start_ts = points[0]["ts"]
        end_ts = points[-1]["ts"]
    else:
        start_ts = trip["start_ts"]
        end_ts = trip["start_ts"]

    # Compute haversine distance
    distance_km = 0.0
    for i in range(1, point_count):
        distance_km += haversine_km(
            points[i - 1]["lat"], points[i - 1]["lon"],
            points[i]["lat"], points[i]["lon"],
        )

    # Merge alias: prefer the one passed to stop, fall back to start
    effective_alias = alias or trip.get("alias")

    summary: dict = {
        "trip_id": trip_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "point_count": point_count,
        "distance_km": round(distance_km, 4),
        "points_file": f"points_{trip_id}.jsonl",
    }
    if effective_alias:
        summary["alias"] = effective_alias

    trip["stopped"] = True
    trip["summary"] = summary

    # Write points file
    _write_points(trip_id, points)

    # Append summary to trips ledger (idempotent guard)
    _append_summary(summary)

    return summary


def list_trips(limit: int = 5) -> list[dict]:
    """Return the latest *limit* finalized trip summaries, newest first.

    Reads from the JSONL ledger on disk — never raw points.
    """
    limit = max(1, min(50, limit))

    if not _TRIPS_LEDGER.exists():
        return []

    summaries: list[dict] = []
    with open(_TRIPS_LEDGER, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                warnings.warn(
                    f"Malformed trip line {lineno}: not valid JSON",
                    stacklevel=2,
                )
                continue
            if not isinstance(obj, dict):
                continue
            summaries.append(obj)

    # Newest first
    summaries.reverse()
    return summaries[:limit]


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _write_points(trip_id: str, points: list[dict]) -> None:
    """Write one JSONL line per point to data/trips/points_<trip_id>.jsonl."""
    if not points:
        return
    path = _TRIPS_DIR / f"points_{trip_id}.jsonl"
    _TRIPS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for pt in points:
            fh.write(json.dumps(pt, ensure_ascii=False) + "\n")


def _append_summary(summary: dict) -> None:
    """Append one summary line to data/trips/trips.jsonl."""
    _TRIPS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_TRIPS_LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
