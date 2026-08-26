"""Tests for guide.finder — all with synthetic fixtures, no real data."""

from __future__ import annotations

import importlib
import math

import pytest

import guide.finder as finder_mod

# ---------------------------------------------------------------------------
# Synthetic fixture data
# ---------------------------------------------------------------------------

# L-shaped / curved route: origin area → north → east → destination area.
# Straight-line S1→S4 is much shorter than the along-sequence sum.
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

# Simple straight route A→B→C
_STRAIGHT_SEQUENCE = {
    "route_id": "FIX_STRAIGHT",
    "route_long_name": "Straight Test Route",
    "stops": [
        {"stop_id": "SS1", "stop_name": "Straight Stop 1", "lat": 14.571, "lon": 121.041},
        {"stop_id": "SS2", "stop_name": "Straight Stop 2", "lat": 14.576, "lon": 121.041},
        {"stop_id": "SS3", "stop_name": "Straight Stop 3", "lat": 14.581, "lon": 121.041},
    ],
}

# MRT-3 fixture (3 stations)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_finder(monkeypatch):
    """Replace finder's data loaders with synthetic fixtures."""
    monkeypatch.setattr(finder_mod, "_load_sequences",
                        lambda: _FIXTURE_SEQUENCES)
    monkeypatch.setattr(finder_mod, "_load_mrt3",
                        lambda: _MRT3_FIXTURE)
    # Force stop index rebuild
    finder_mod._build_stop_index.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_zero_distance(self):
        assert finder_mod._haversine_m(14.58, 121.05, 14.58, 121.05) == 0.0

    def test_known_distance(self):
        # Roughly 1 degree lat ≈ 111 km
        d = finder_mod._haversine_m(14.0, 121.0, 15.0, 121.0)
        assert 110_000 < d < 112_000


class TestNearestStops:
    def test_returns_nearby_stops(self, monkeypatch):
        _patch_finder(monkeypatch)
        # Origin is near CS1 (14.570, 121.040) and SS1 (14.571, 121.041)
        results = finder_mod.nearest_stops(14.570, 121.040, radius_m=500)
        ids = [r["stop_id"] for r in results]
        assert "CS1" in ids
        assert "SS1" in ids
        # All within radius
        for r in results:
            assert r["distance_m"] <= 500

    def test_sorted_by_distance(self, monkeypatch):
        _patch_finder(monkeypatch)
        results = finder_mod.nearest_stops(14.570, 121.040, radius_m=500)
        dists = [r["distance_m"] for r in results]
        assert dists == sorted(dists)

    def test_empty_when_nothing_nearby(self, monkeypatch):
        _patch_finder(monkeypatch)
        results = finder_mod.nearest_stops(14.999, 121.999, radius_m=10)
        assert results == []


class TestRoutesAtStop:
    def test_returns_routes(self, monkeypatch):
        _patch_finder(monkeypatch)
        routes = finder_mod.routes_at_stop("CS1")
        rids = [r["route_id"] for r in routes]
        assert "FIX_CURVED" in rids

    def test_empty_for_unknown_stop(self, monkeypatch):
        _patch_finder(monkeypatch)
        assert finder_mod.routes_at_stop("NONEXISTENT") == []


class TestRideSegment:
    def test_curved_fallback(self, monkeypatch):
        _patch_finder(monkeypatch)
        km = finder_mod.ride_segment(_CURVED_SEQUENCE, "CS1", "CS4")
        assert km > 0
        # Sum of haversine segments: S1→S2 + S2→S3 + S3→S4
        expected_m = (
            finder_mod._haversine_m(14.570, 121.040, 14.575, 121.040)
            + finder_mod._haversine_m(14.575, 121.040, 14.575, 121.050)
            + finder_mod._haversine_m(14.575, 121.050, 14.580, 121.050)
        )
        expected_km = round(expected_m / 1000, 3)
        assert km == expected_km

    def test_curved_km_exceeds_straight_line(self, monkeypatch):
        _patch_finder(monkeypatch)
        along_km = finder_mod.ride_segment(_CURVED_SEQUENCE, "CS1", "CS4")
        s1 = _CURVED_SEQUENCE["stops"][0]
        s4 = _CURVED_SEQUENCE["stops"][3]
        straight_km = round(
            finder_mod._haversine_m(s1["lat"], s1["lon"], s4["lat"], s4["lon"]) / 1000,
            3,
        )
        # Along-route MUST be longer than straight-line
        assert along_km > straight_km

    def test_straight_route_segment(self, monkeypatch):
        _patch_finder(monkeypatch)
        km = finder_mod.ride_segment(_STRAIGHT_SEQUENCE, "SS1", "SS3")
        expected_m = (
            finder_mod._haversine_m(14.571, 121.041, 14.576, 121.041)
            + finder_mod._haversine_m(14.576, 121.041, 14.581, 121.041)
        )
        assert km == round(expected_m / 1000, 3)

    def test_raises_on_missing_stop(self, monkeypatch):
        _patch_finder(monkeypatch)
        with pytest.raises(ValueError, match="not found"):
            finder_mod.ride_segment(_CURVED_SEQUENCE, "CS1", "BOGUS")


class TestCandidateDirectRoutes:
    def test_finds_curved_candidate(self, monkeypatch):
        _patch_finder(monkeypatch)
        # Origin near CS1, dest near CS4
        cands = finder_mod.candidate_direct_routes(
            14.5705, 121.040, 14.5795, 121.050
        )
        assert len(cands) >= 1
        curved = [c for c in cands if c["route_id"] == "FIX_CURVED"]
        assert len(curved) == 1
        c = curved[0]
        assert c["board_stop"]["stop_id"] == "CS1"
        assert c["alight_stop"]["stop_id"] == "CS4"
        assert c["ride_km"] > 0

    def test_sorted_by_ride_km(self, monkeypatch):
        _patch_finder(monkeypatch)
        cands = finder_mod.candidate_direct_routes(
            14.5705, 121.040, 14.5795, 121.050
        )
        kms = [c["ride_km"] for c in cands]
        assert kms == sorted(kms)

    def test_board_before_alight(self, monkeypatch):
        """board_stop index must be < alight_stop index."""
        _patch_finder(monkeypatch)
        cands = finder_mod.candidate_direct_routes(
            14.5705, 121.040, 14.5795, 121.050
        )
        for c in cands:
            seq = next(
                s for s in _FIXTURE_SEQUENCES
                if s["route_id"] == c["route_id"]
            )
            stops = seq["stops"]
            bi = next(i for i, s in enumerate(stops) if s["stop_id"] == c["board_stop"]["stop_id"])
            ai = next(i for i, s in enumerate(stops) if s["stop_id"] == c["alight_stop"]["stop_id"])
            assert bi < ai

    def test_empty_when_nothing_nearby(self, monkeypatch):
        _patch_finder(monkeypatch)
        cands = finder_mod.candidate_direct_routes(
            14.999, 121.999, 14.998, 121.998
        )
        assert cands == []


class TestNearestMrt3Station:
    def test_within_range(self, monkeypatch):
        _patch_finder(monkeypatch)
        # Near Station A (14.558, 121.048)
        st = finder_mod.nearest_mrt3_station(14.5585, 121.048)
        assert st is not None
        assert st["stop_id"] == "M1"

    def test_beyond_600m_returns_none(self, monkeypatch):
        _patch_finder(monkeypatch)
        # Far from all stations
        st = finder_mod.nearest_mrt3_station(14.999, 121.999)
        assert st is None
