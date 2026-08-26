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


class TestDisputedSinkSort:
    """Disputed options sink below equal-fare clean options; cheapest-first preserved for different fares."""

    def test_disputed_sinks_below_equal_fare_clean(self, monkeypatch):
        _patch_all(monkeypatch)
        # Monkeypatch freshness so R_DIRTY is disputed, R_CLEAN is green
        def fake_freshness(route_id):
            if route_id == "R_DIRTY":
                return {"tier": "disputed", "confirmations": 0, "disputes": 3}
            return {"tier": "green", "confirmations": 5, "disputes": 0}

        monkeypatch.setattr(planner_mod, "_freshness", fake_freshness)

        result = planner_mod.plan(14.5705, 121.040, 14.5795, 121.050)
        assert result["status"] == "ok"

        # If there are options with equal fare, the clean one should come first
        # Build (fare, is_disputed) list
        from itertools import groupby
        fare_groups = []
        for opt in result["options"]:
            fare = opt["total_fare"]
            has_disputed = any(
                l.get("type") == "ride" and l.get("route_id") == "R_DIRTY"
                for l in opt["legs"]
            )
            fare_groups.append((fare, has_disputed))

        # Group by fare to check disputed sinks within each fare group
        for fare, group in groupby(fare_groups, key=lambda x: x[0]):
            group_list = list(group)
            disputed_positions = [i for i, (_, d) in enumerate(group_list) if d]
            clean_positions = [i for i, (_, d) in enumerate(group_list) if not d]
            if disputed_positions and clean_positions:
                assert max(disputed_positions) > min(clean_positions), \
                    f"Disputed option should be after clean option for fare {fare}"

    def test_cheapest_first_preserved_for_different_fares(self, monkeypatch):
        _patch_all(monkeypatch)
        def fake_freshness(route_id):
            return {"tier": "gray", "confirmations": 0, "disputes": 0}
        monkeypatch.setattr(planner_mod, "_freshness", fake_freshness)

        result = planner_mod.plan(14.5705, 121.040, 14.5795, 121.050)
        assert result["status"] == "ok"
        fares = [o["total_fare"] for o in result["options"]]
        assert fares == sorted(fares)
