from __future__ import annotations

import json
import functools
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


class Fare(Decimal):
    __slots__ = ()

    def __repr__(self):
        return super().__repr__()

    def __eq__(self, other):
        if isinstance(other, float):
            return super().__eq__(Decimal(str(other)))
        return super().__eq__(other)

    def __hash__(self):
        return super().__hash__()


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_FARES_PATH = _DATA_DIR / "fares.json"
_ROUTES_PATH = _DATA_DIR / "routes_mandaluyong.json"


@functools.lru_cache(maxsize=1)
def _load_fares_data() -> dict:
    with open(_FARES_PATH, encoding="utf-8") as f:
        return json.load(f)


def stop_in_bbox(lat: float, lon: float, bbox: list[float]) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def fare(mode: str, km: float = 0) -> Fare:
    if km <= 0:
        raise ValueError(f"km must be positive, got {km}")

    data = _load_fares_data()
    modes = data.get("modes", {})

    distance_modes = {
        k for k, v in modes.items() if v.get("fare_model") == "distance"
    }

    if mode not in distance_modes:
        if mode in modes:
            available = sorted(distance_modes)
            raise KeyError(
                f"Mode '{mode}' uses fare_model "
                f"'{modes[mode]['fare_model']}', not distance-based; "
                f"only distance-based modes can be computed via fare(). "
                f"Distance-based modes: {available}"
            )
        available = sorted(modes.keys())
        raise KeyError(
            f"Unknown mode '{mode}'. Available modes: {available}"
        )

    entry = modes[mode]
    base_fare = Decimal(str(entry["base_fare"]))
    base_km = Decimal(str(entry["base_km"]))
    per_km = Decimal(str(entry["per_km"]))
    km_dec = Decimal(str(km))

    if km_dec <= base_km:
        result = base_fare
    else:
        result = base_fare + per_km * (km_dec - base_km)

    return Fare(result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def routes_for(city: str) -> list[dict]:
    data = _load_fares_data()
    pilot_city = data["region"]["pilot_city"]

    if city.lower() != pilot_city.lower():
        raise ValueError(
            f"Pilot scope is '{pilot_city}' (case-insensitive); "
            f"got '{city}'. Only '{pilot_city}' routes are available."
        )

    return _load_routes()


def _load_routes() -> list[dict]:
    if not _ROUTES_PATH.exists():
        return []

    with open(_ROUTES_PATH, encoding="utf-8") as f:
        return json.load(f)
