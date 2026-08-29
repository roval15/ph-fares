"""Deterministic flood-risk backend for ph-fares — stdlib only.

Folds three sources:

* **NOAH hazard data** (susceptibility) — the vendor-built raster grid under
  ``data/flood/`` (see ``tools/build_flood_grid.py`` for provenance).
* **Open-Meteo weather** (trigger) — live hourly precipitation, with graceful
  degradation: any failure yields a susceptibility-only verdict.
* **Route geometry** — plan legs from ``guide.planner`` resampled every ~50 m.

Everything is deterministic: the same inputs always produce the same exposure
counts, verdicts, and polygon samples.
"""

from __future__ import annotations

import functools
import gzip
import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_EARTH_RADIUS_M = 6_371_000

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FLOOD_DIR = _DATA_DIR / "flood"
_DEFAULT_GRID_PATH = _FLOOD_DIR / "noah_mm_flood_grid.bin.gz"
_DEFAULT_META_PATH = _FLOOD_DIR / "flood_metadata.json"
_STOP_SEQUENCES_PATH = _DATA_DIR / "stop_sequences.json"

RULES = {
    "version": "0.2",
    "thresholds": {"heavy_rain_mm_hr": 30.0, "storm_rain_24h_mm": 100.0},
}

# Internal labels (RULES itself intentionally exposes only version+thresholds).
_LEVEL_LABELS = {0: "NONE", 1: "LOW", 2: "MEDIUM", 3: "HIGH"}

_MANILA_TZ = timezone(timedelta(hours=8))

_WEATHER_ENDPOINT = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=precipitation&past_days=1&forecast_days=2"
    "&timezone=Asia%2FManila"
)

_urlopen = urllib.request.urlopen
_WEATHER_CACHE: dict[tuple, dict] = {}


# ---------------------------------------------------------------------------
# Grid loading / susceptibility
# ---------------------------------------------------------------------------

class FloodGrid:
    """Immutable lazily-loaded NOAH flood raster with O(1) cell lookups."""

    __slots__ = ("_data", "_lon_min", "_lat_min", "_lon_max", "_lat_max",
                 "_dlon", "_dlat", "_nrows", "_ncols")

    def __init__(self, data: bytes, metadata: dict):
        bbox = metadata["bbox"]
        nrows, ncols = metadata["shape"]
        dlon, dlat = metadata["cell_deg"]
        if len(data) != nrows * ncols:
            raise ValueError(
                f"raster length {len(data)} != nrows*ncols {nrows * ncols}"
            )
        self._data = data
        self._lon_min, self._lat_min, self._lon_max, self._lat_max = bbox
        self._dlon, self._dlat = dlon, dlat
        self._nrows, self._ncols = nrows, ncols

    def susceptibility(self, lat: float, lon: float) -> int:
        """Return 0-3 cell value; out-of-bbox coordinates map to 0."""
        if not (self._lat_min <= lat <= self._lat_max
                and self._lon_min <= lon <= self._lon_max):
            return 0
        row = int((lat - self._lat_min) / self._dlat)
        col = int((lon - self._lon_min) / self._dlon)
        row = max(0, min(self._nrows - 1, row))
        col = max(0, min(self._ncols - 1, col))
        return self._data[row * self._ncols + col]


def load_grid(path: Path | str = _DEFAULT_GRID_PATH,
              meta_path: Path | str = _DEFAULT_META_PATH) -> FloodGrid:
    """Read the gzip-compressed uint8 raster plus its JSON metadata."""
    with gzip.open(path, "rb") as fh:
        data = fh.read()
    with open(meta_path, encoding="utf-8") as fh:
        metadata = json.load(fh)
    return FloodGrid(data, metadata)


# ---------------------------------------------------------------------------
# Routes / geometry
# ---------------------------------------------------------------------------

def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    lat1, lon1 = a
    lat2, lon2 = b
    lat1, lon1, lat2, lon2 = map(
        math.radians, [lat1, lon1, lat2, lon2]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


@functools.lru_cache(maxsize=1)
def _load_stop_sequences() -> list[dict]:
    with open(_STOP_SEQUENCES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _stops_by_route() -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for seq in _load_stop_sequences():
        idx[seq["route_id"]] = seq["stops"]
    return idx


def _leg_polyline(leg: dict) -> list[tuple[float, float]]:
    """Resolve a plan leg into an ordered (lat, lon) polyline.

    Ride legs slice the route's stop sequence between the board and alight
    stop_ids (falling back to a straight line if the ids are missing or the
    order is reversed); walk legs are straight lines between their endpoints.
    """
    if leg.get("type") == "walk":
        return [(leg["from"]["lat"], leg["from"]["lon"]),
                (leg["to"]["lat"], leg["to"]["lon"])]

    seq = _stops_by_route().get(leg.get("route_id"), [])
    board = leg["board_stop"]
    alight = leg["alight_stop"]
    i = next((k for k, s in enumerate(seq)
              if s["stop_id"] == board["stop_id"]), None)
    j = next((k for k, s in enumerate(seq)
              if s["stop_id"] == alight["stop_id"]), None)
    if i is None or j is None or j < i:
        return [(board["lat"], board["lon"]), (alight["lat"], alight["lon"])]

    pts = [(s["lat"], s["lon"]) for s in seq[i:j + 1]]
    return [(board["lat"], board["lon"])] + pts + [
        (alight["lat"], alight["lon"])
    ]


def sample_polyline(points: list[tuple[float, float]],
                    step_m: float = 50.0) -> list[tuple[float, float]]:
    """Resample an ordered lat/lon path every ~*step_m* metres.

    Deterministic: interpolation factors are computed from haversine metres
    only. Always includes the first and last vertex.
    """
    out = [points[0]]
    acc = 0.0
    cur = points[0]
    for nxt in points[1:]:
        seg = haversine_m(cur, nxt)
        while acc + seg >= step_m and seg > 0:
            f = (step_m - acc) / seg
            cur = (cur[0] + f * (nxt[0] - cur[0]),
                   cur[1] + f * (nxt[1] - cur[1]))
            out.append(cur)
            seg = haversine_m(cur, nxt)
            acc = 0.0
        acc += seg
        cur = nxt
    out.append(points[-1])
    return out


def route_exposure(grid: FloodGrid,
                   legs: list[dict]) -> dict:
    """Fold a plan option's legs into susceptibility counts.

    Returns ``{"samples": n, "exposure": {0: c0, 1: c1, 2: c2, 3: c3}}``.
    """
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    n = 0
    for leg in legs:
        for lat, lon in sample_polyline(_leg_polyline(leg)):
            counts[grid.susceptibility(lat, lon)] += 1
            n += 1
    return {"samples": n, "exposure": counts}


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, gracefully degradable)
# ---------------------------------------------------------------------------

def _weather_cache_key(now: datetime, lat: float, lon: float) -> tuple:
    """Round to a 15-minute window + ~0.02 deg cell so bursts reuse results."""
    window = now.replace(minute=(now.minute // 15) * 15,
                         second=0, microsecond=0)
    return (window.isoformat(timespec="minutes"),
            round(lat / 0.02) * 0.02,
            round(lon / 0.02) * 0.02)


def fetch_weather(lat: float, lon: float,
                  now: datetime | None = None) -> dict:
    """Fetch Open-Meteo precipitation for the next 6 h / 24 h.

    Returns ``{"available": True, "rain_max_6h_mm", "rain_next_24h_mm",
    "as_of"}`` on success, or ``{"available": False}`` on any failure
    (graceful degradation — callers fall back to a susceptibility-only
    verdict). Results are cached per rounded window and location cell.
    """
    if now is None:
        now = datetime.now(_MANILA_TZ)
    key = _weather_cache_key(now, lat, lon)
    cached = _WEATHER_CACHE.get(key)
    if cached is not None:
        return cached

    url = _WEATHER_ENDPOINT.format(lat=lat, lon=lon)
    req = urllib.request.Request(url, headers={"User-Agent": "ph-fares/0.2"})
    try:
        with _urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
        times = payload["hourly"]["time"]
        precip = payload["hourly"]["precipitation"]
        i_now = next(
            (i for i, t in enumerate(times)
             if t[:13] == now.strftime("%Y-%m-%dT%H")),
            len(times) // 2,
        )
        win = [p for p in precip[max(0, i_now - 3):i_now + 4] if p]
        n24 = [p for p in precip[i_now:i_now + 24] if p]
        result = {
            "available": True,
            "rain_max_6h_mm": float(max(win)) if win else 0.0,
            "rain_next_24h_mm": float(sum(n24)),
            "as_of": now.isoformat(timespec="seconds"),
        }
    except (urllib.error.URLError, TimeoutError, OSError,
            ValueError, KeyError, TypeError):
        result = {"available": False}

    _WEATHER_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Assessment (pure)
# ---------------------------------------------------------------------------

def assess(exposure_counts: dict,
           weather: dict,
           rules: dict = RULES) -> dict:
    """Pure assessment: susceptibility base + rain-trigger escalation.

    ``weather == {"available": False}`` is treated as rain 0 (susceptibility-
    only), with a plain-language reason stating live rain is unavailable.
    """
    counts = {int(k): int(v) for k, v in exposure_counts.items()}
    total = sum(counts.values())
    in_zone = sum(c for lvl, c in counts.items() if lvl > 0)
    positive = {lvl: c for lvl, c in counts.items() if c > 0}
    base = max(positive) if positive else 0
    pct_in_zone = (in_zone / total) if total else 0.0

    reasons: list[str] = []
    if base == 0:
        reasons.append("route outside NOAH mapped flood zones")
    else:
        frac = in_zone / max(1, total)
        reasons.append(
            f"route susceptibility {_LEVEL_LABELS[base]} "
            f"({frac:.0%} of path in mapped flood zones)"
        )

    level = base
    if not weather.get("available", True):
        reasons.append("live rain data unavailable; susceptibility-only verdict")
    else:
        rain_max_6h = float(weather.get("rain_max_6h_mm") or 0.0)
        rain_24h = float(weather.get("rain_next_24h_mm") or 0.0)
        heavy_thr = rules["thresholds"]["heavy_rain_mm_hr"]
        storm_thr = rules["thresholds"]["storm_rain_24h_mm"]
        if rain_max_6h >= heavy_thr and base > 0:
            level = min(3, level + 1)
            reasons.append(f"heavy rain now ({rain_max_6h:.0f} mm/hr)")
        elif rain_max_6h >= 15:
            reasons.append(
                f"moderate rain ({rain_max_6h:.0f} mm/hr), no escalation"
            )
        if rain_24h >= storm_thr and base > 0:
            level = min(3, level + 1)
            reasons.append(
                f"storm-scale rain forecast ({rain_24h:.0f} mm/24h)"
            )

    return {
        "level": level,
        "verdict": _LEVEL_LABELS[level],
        "reasons": reasons,
        "pct_in_zone": pct_in_zone,
    }