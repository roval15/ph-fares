"""Tests for guide.planner — end-to-end plan() on synthetic fixtures."""

from __future__ import annotations

from decimal import Decimal

import pytest

import guide.finder as finder_mod
import guide.planner as planner_mod
import phfares


# ---------------------------------------------------------------------------
# Re-use same fixture data as test_finder.py
# ---------------------------------------------------------------------------

_CURVED_SEQUENCE = {
    "route_id": "FIX_CURVED",
    "route_long_name": "Curved Test Route",
    "stops": [
        {"stop_id": "CS1", "stop_name": "Curved Stop 1", "lat": 14.570, "lon": 121.040},
        {"stop_id": "CS2", "stop_name": "Curved Stop 2", "lat": 14.575, "lon": 121.040},
        {"stop_id": "CS3", "stop_name": "Curved Stop 3", "lat": 14.575, "lon": 121.050},
        {"stop_id": "CS4", "stop_name": "Curved Stop 4", "lat": 14.580, "lon": 121.050},
    ],
}

_STRAIGHT_SEQUENCE = {
    "route_id": "FIX_STRAIGHT",
    "route_long_name": "Straight Test Route",
    "stops": [
        {"stop_id": "SS1", "stop_name": "Straight Stop 1", "lat": 14.571, "lon": 121.041},
        {"stop_id": "SS2", "stop_name": "Straight Stop 2", "lat": 14.576, "lon": 121.041},
        {"stop_id": "SS3", "stop_name": "Straight Stop 3", "lat": 14.581, "lon": 121.041},
    ],
}

_MRT3_FIXTURE = {
    "route_id": "FIX_MRT3",
    "route_short_name": "MRT-3",
    "route_long_name": "Taft Ave - North Ave",
    "stations": [
        {"stop_id": "M1", "stop_name": "Station A", "lat": 14.558, "lon": 121.048},
        {"stop_id": "M2", "stop_name": "Station B", "lat": 14.568, "lon": 121.050},
        {"stop_id": "M3", "stop_name": "Station C", "lat": 14.578, "lon": 121.052},
    ],
}

_FIXTURE_SEQUENCES = [_CURVED_SEQUENCE, _STRAIGHT_SEQUENCE]

_FARES_FIXTURE = {
    "modes": {
        "jeepney_traditional": {
            "fare_model": "distance",
            "base_fare": 12.0,
            "base_km": 4.0,
            "per_km": 1.80,
        },
        "jeepney_modern": {
            "fare_model": "distance",
            "base_fare": 14.0,
            "base_km": 4.0,
            "per_km": 2.20,
        },
        "mrt3": {
            "fare_model": "station_table",
            "station_bands": [
                {"min_stations": 1, "max_stations": 2, "fare": 13},
                {"min_stations": 3, "max_stations": 4, "fare": 16},
            ],
            "notes": "Fares 50% discounted since 2026-03-23 (fuel-cost relief measure per same source); the table reflects published pre-discount fares.",
        },
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_all(monkeypatch):
    """Patch both finder and planner data loaders."""
    finder_mod._build_stop_index.cache_clear()
    monkeypatch.setattr(finder_mod, "_load_sequences", lambda: _FIXTURE_SEQUENCES)
    monkeypatch.setattr(finder_mod, "_load_mrt3", lambda: _MRT3_FIXTURE)
    # planner imports _load_mrt3 from finder — patch the local binding too
    monkeypatch.setattr(planner_mod, "_load_mrt3", lambda: _MRT3_FIXTURE)
    monkeypatch.setattr(planner_mod, "_load_fares_modes", lambda: _FARES_FIXTURE["modes"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlanJeepney:
    def test_legs_structure(self, monkeypatch):
        _patch_all(monkeypatch)
        result = planner_mod.plan(14.5705, 121.040, 14.5795, 121.050)
        assert result["status"] == "ok"
        for opt in result["options"]:
            legs = opt["legs"]
            assert len(legs) == 3
            assert legs[0]["type"] == "walk"
            assert legs[1]["type"] == "ride"
            assert legs[2]["type"] == "walk"

    def test_fares_match_phfares(self, monkeypatch):
        _patch_all(monkeypatch)
        result = planner_mod.plan(14.5705, 121.040, 14.5795, 121.050)
        assert result["status"] == "ok"
        for opt in result["options"]:
            ride_leg = [l for l in opt["legs"] if l["type"] == "ride"][0]
            mode = ride_leg["mode"]
            ride_km = ride_leg["distance_km"]
            expected_fare = phfares.fare(mode, ride_km)
            assert opt["total_fare"] == expected_fare
            assert opt["fare_breakdown"][mode] == expected_fare

    def test_sorted_cheapest_first(self, monkeypatch):
        _patch_all(monkeypatch)
        result = planner_mod.plan(14.5705, 121.040, 14.5795, 121.050)
        fares = [o["total_fare"] for o in result["options"]]
        assert fares == sorted(fares)


class TestPlanMrt3Gating:
    def test_both_near_mrt3_present(self, monkeypatch):
        _patch_all(monkeypatch)
        # Origin near M1, dest near M3 → both within 600 m
        result = planner_mod.plan(14.5585, 121.048, 14.5785, 121.052)
        assert result["status"] == "ok"
        mrt_opts = [o for o in result["options"]
                    if any(l.get("mode") == "mrt3" for l in o["legs"])]
        assert len(mrt_opts) == 1
        mrt = mrt_opts[0]
        # M1 is index 0, M3 is index 2 → count = 2 → fare = 13
        assert mrt["fare_breakdown"]["mrt3"] == Decimal("13")
        # Discount note surfaced
        assert any("discount" in n.lower() or "discounted" in n.lower()
                    for n in mrt.get("notes", []))

    def test_one_far_mrt3_absent(self, monkeypatch):
        _patch_all(monkeypatch)
        # Origin near M1, dest far away — no jeepney routes, no MRT
        result = planner_mod.plan(14.5585, 121.048, 14.999, 121.999)
        mrt_opts = [o for o in result.get("options", [])
                    if any(l.get("mode") == "mrt3" for l in o["legs"])]
        assert len(mrt_opts) == 0


class TestPlanNoRoute:
    def test_returns_structured_no_route(self, monkeypatch):
        _patch_all(monkeypatch)
        result = planner_mod.plan(14.999, 121.999, 14.998, 121.998)
        assert result["status"] == "no_route"
        assert "message" in result
        assert "from" in result
        assert "to" in result
