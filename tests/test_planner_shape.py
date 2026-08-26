"""Tests for ride-leg shape geometry in guide.planner."""

from __future__ import annotations

import pytest

import guide.finder as finder_mod
import guide.planner as planner_mod


# ---------------------------------------------------------------------------
# Fixtures — two origin/dest pairs that exercise different code paths
# ---------------------------------------------------------------------------

# Forward-stored: origin near first stop, dest near last stop.
# candidate_direct_routes picks first-stop as board, last-stop as alight.
_ORIGIN_FWD = {"lat": 14.5700, "lon": 121.0400}   # near RS1
_DEST_FWD   = {"lat": 14.5800, "lon": 121.0500}   # near RS4

_FORWARD_SEQUENCE = {
    "route_id": "FIX_SHAPE",
    "route_long_name": "Shape Test Route",
    "stops": [
        {"stop_id": "RS1", "stop_name": "Rev Stop 1", "lat": 14.570, "lon": 121.040},
        {"stop_id": "RS2", "stop_name": "Rev Stop 2", "lat": 14.573, "lon": 121.043},
        {"stop_id": "RS3", "stop_name": "Rev Stop 3", "lat": 14.576, "lon": 121.047},
        {"stop_id": "RS4", "stop_name": "Rev Stop 4", "lat": 14.580, "lon": 121.050},
    ],
}

# Reversed-stored: same physical stops but storage order is RS4,RS3,RS2,RS1.
# Origin near RS4 (now idx 0) → candidate picks RS4 as board.
# Dest near RS1 (now idx 3)   → candidate picks RS1 as alight.
# In storage, board(idx 0) < alight(idx 3), so candidate is valid.
# _ride_shape sees board_idx(0) < alight_idx(3) → NO reversal needed.
_ORIGIN_REV = {"lat": 14.5800, "lon": 121.0500}   # near RS4 (idx 0 in rev)
_DEST_REV   = {"lat": 14.5700, "lon": 121.0400}   # near RS1 (idx 3 in rev)

_REVERSED_SEQUENCE = {
    "route_id": "FIX_SHAPE",
    "route_long_name": "Shape Test Route",
    "stops": [
        {"stop_id": "RS4", "stop_name": "Rev Stop 4", "lat": 14.580, "lon": 121.050},
        {"stop_id": "RS3", "stop_name": "Rev Stop 3", "lat": 14.576, "lon": 121.047},
        {"stop_id": "RS2", "stop_name": "Rev Stop 2", "lat": 14.573, "lon": 121.043},
        {"stop_id": "RS1", "stop_name": "Rev Stop 1", "lat": 14.570, "lon": 121.040},
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
            ],
            "notes": "Discounted since 2026.",
        },
    }
}


def _patch_all(monkeypatch, sequences):
    """Patch finder/planner data loaders for shape tests."""
    finder_mod._build_stop_index.cache_clear()
    monkeypatch.setattr(finder_mod, "_load_sequences", lambda: sequences)
    monkeypatch.setattr(planner_mod, "_load_sequences", lambda: sequences)
    monkeypatch.setattr(finder_mod, "_load_mrt3", lambda: _MRT3_FIXTURE)
    monkeypatch.setattr(planner_mod, "_load_mrt3", lambda: _MRT3_FIXTURE)
    monkeypatch.setattr(planner_mod, "_load_fares_modes", lambda: _FARES_FIXTURE["modes"])
    monkeypatch.setattr(planner_mod, "_freshness",
                       lambda rid: {"tier": "gray", "confirmations": 0, "disputes": 0})


def _get_ride_leg(result):
    """Return the first ride leg from the cheapest option."""
    return [l for l in result["options"][0]["legs"] if l["type"] == "ride"][0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRideShapeForward:
    """Forward-stored sequence: board_idx < alight_idx in storage."""

    def test_shape_starts_at_board_ends_at_alight(self, monkeypatch):
        _patch_all(monkeypatch, [_FORWARD_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_FWD["lat"], _ORIGIN_FWD["lon"],
            _DEST_FWD["lat"], _DEST_FWD["lon"],
        )
        assert result["status"] == "ok"
        ride = _get_ride_leg(result)
        shape = ride["shape"]
        assert len(shape) >= 2
        # First point = board stop RS1
        assert shape[0][0] == pytest.approx(14.570, abs=1e-4)
        assert shape[0][1] == pytest.approx(121.040, abs=1e-4)
        # Last point = alight stop RS4
        assert shape[-1][0] == pytest.approx(14.580, abs=1e-4)
        assert shape[-1][1] == pytest.approx(121.050, abs=1e-4)
        # Includes all 4 stops in order
        assert len(shape) == 4

    def test_intermediate_stops_in_order(self, monkeypatch):
        _patch_all(monkeypatch, [_FORWARD_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_FWD["lat"], _ORIGIN_FWD["lon"],
            _DEST_FWD["lat"], _DEST_FWD["lon"],
        )
        ride = _get_ride_leg(result)
        shape = ride["shape"]
        # shape[1] = RS2, shape[2] = RS3
        assert shape[1][0] == pytest.approx(14.573, abs=1e-4)
        assert shape[1][1] == pytest.approx(121.043, abs=1e-4)
        assert shape[2][0] == pytest.approx(14.576, abs=1e-4)
        assert shape[2][1] == pytest.approx(121.047, abs=1e-4)


class TestRideShapeReversed:
    """Reversed-stored sequence: storage order is opposite to travel direction.
    candidate_direct_routes picks the stops correctly (origin→idx0, dest→idx3),
    so _ride_shape must return coords in travel order."""

    def test_shape_starts_at_board_ends_at_alight(self, monkeypatch):
        _patch_all(monkeypatch, [_REVERSED_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_REV["lat"], _ORIGIN_REV["lon"],
            _DEST_REV["lat"], _DEST_REV["lon"],
        )
        assert result["status"] == "ok"
        ride = _get_ride_leg(result)
        shape = ride["shape"]
        assert len(shape) >= 2
        # Starts at board stop (RS4 at 14.580, 121.050)
        assert shape[0][0] == pytest.approx(14.580, abs=1e-4)
        assert shape[0][1] == pytest.approx(121.050, abs=1e-4)
        # Ends at alight stop (RS1 at 14.570, 121.040)
        assert shape[-1][0] == pytest.approx(14.570, abs=1e-4)
        assert shape[-1][1] == pytest.approx(121.040, abs=1e-4)

    def test_intermediate_order_matches_travel(self, monkeypatch):
        """In reversed storage, RS3(idx 1) then RS2(idx 2) are between
        RS4(idx 0) and RS1(idx 3). Shape slice is RS4..RS1 in storage
        order, which IS the travel direction (no reversal needed because
        board_idx < alight_idx). Intermediate: RS3, RS2."""
        _patch_all(monkeypatch, [_REVERSED_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_REV["lat"], _ORIGIN_REV["lon"],
            _DEST_REV["lat"], _DEST_REV["lon"],
        )
        ride = _get_ride_leg(result)
        shape = ride["shape"]
        assert len(shape) == 4
        # Intermediate stops in travel order: RS3 then RS2
        assert shape[1][0] == pytest.approx(14.576, abs=1e-4)
        assert shape[2][0] == pytest.approx(14.573, abs=1e-4)


class TestRideShapeReversal:
    """Directly test the reversal branch in _ride_shape by using a fixture
    where board_idx > alight_idx in storage."""

    def test_reversed_slice_starts_at_board(self):
        """When board_idx > alight_idx, _ride_shape reverses the slice
        so the result starts at board and ends at alight."""
        seq = {
            "route_id": "RTEST",
            "route_long_name": "Reversal Test",
            "stops": [
                {"stop_id": "A", "stop_name": "A", "lat": 10.0, "lon": 20.0},
                {"stop_id": "B", "stop_name": "B", "lat": 10.1, "lon": 20.1},
                {"stop_id": "C", "stop_name": "C", "lat": 10.2, "lon": 20.2},
                {"stop_id": "D", "stop_name": "D", "lat": 10.3, "lon": 20.3},
            ],
        }
        # board=D(idx 3), alight=A(idx 0) → board_idx > alight_idx
        shape = planner_mod._ride_shape(
            [seq], "RTEST",
            board_stop={"stop_id": "D", "lat": 10.3, "lon": 20.3},
            alight_stop={"stop_id": "A", "lat": 10.0, "lon": 20.0},
        )
        assert shape[0] == [10.3, 20.3]   # board D
        assert shape[-1] == [10.0, 20.0]  # alight A
        assert len(shape) == 4

    def test_forward_slice_no_reversal(self):
        """When board_idx < alight_idx, _ride_shape returns the slice as-is."""
        seq = {
            "route_id": "FTEST",
            "route_long_name": "Forward Test",
            "stops": [
                {"stop_id": "A", "stop_name": "A", "lat": 10.0, "lon": 20.0},
                {"stop_id": "B", "stop_name": "B", "lat": 10.1, "lon": 20.1},
                {"stop_id": "C", "stop_name": "C", "lat": 10.2, "lon": 20.2},
            ],
        }
        shape = planner_mod._ride_shape(
            [seq], "FTEST",
            board_stop={"stop_id": "A", "lat": 10.0, "lon": 20.0},
            alight_stop={"stop_id": "C", "lat": 10.2, "lon": 20.2},
        )
        assert shape[0] == [10.0, 20.0]   # board A
        assert shape[-1] == [10.2, 20.2]  # alight C
        assert len(shape) == 3


class TestRideShapeFallback:
    """Unknown route_id or missing stop_id -> two-point fallback."""

    def test_unknown_route_id(self, monkeypatch):
        _patch_all(monkeypatch, [])  # empty sequences → no jeepney candidates
        result = planner_mod.plan(
            _ORIGIN_FWD["lat"], _ORIGIN_FWD["lon"],
            _DEST_FWD["lat"], _DEST_FWD["lon"],
        )
        # Only MRT3 option may remain; check all ride legs
        for opt in result.get("options", []):
            for leg in opt["legs"]:
                if leg["type"] == "ride":
                    shape = leg["shape"]
                    assert len(shape) == 2
                    board = leg["board_stop"]
                    alight = leg["alight_stop"]
                    assert shape[0] == [board["lat"], board["lon"]]
                    assert shape[1] == [alight["lat"], alight["lon"]]

    def test_direct_fallback_unknown_route(self):
        """Direct call to _ride_shape with unknown route returns fallback."""
        shape = planner_mod._ride_shape(
            [], "UNKNOWN_ROUTE",
            board_stop={"stop_id": "X", "lat": 5.0, "lon": 6.0},
            alight_stop={"stop_id": "Y", "lat": 7.0, "lon": 8.0},
        )
        assert shape == [[5.0, 6.0], [7.0, 8.0]]

    def test_direct_fallback_missing_stop(self):
        """Direct call to _ride_shape with missing stop_id returns fallback."""
        seq = {
            "route_id": "R",
            "route_long_name": "R",
            "stops": [
                {"stop_id": "A", "stop_name": "A", "lat": 1.0, "lon": 2.0},
            ],
        }
        shape = planner_mod._ride_shape(
            [seq], "R",
            board_stop={"stop_id": "A", "lat": 1.0, "lon": 2.0},
            alight_stop={"stop_id": "MISSING", "lat": 3.0, "lon": 4.0},
        )
        assert shape == [[1.0, 2.0], [3.0, 4.0]]


class TestWalkLegEndpoints:
    """Walk leg from/to dicts are correct through plan()."""

    def test_walk_endpoints(self, monkeypatch):
        _patch_all(monkeypatch, [_FORWARD_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_FWD["lat"], _ORIGIN_FWD["lon"],
            _DEST_FWD["lat"], _DEST_FWD["lon"],
        )
        assert result["status"] == "ok"
        legs = result["options"][0]["legs"]

        walk1 = legs[0]
        assert walk1["type"] == "walk"
        assert walk1["from"]["lat"] == pytest.approx(_ORIGIN_FWD["lat"], abs=1e-6)
        assert walk1["from"]["lon"] == pytest.approx(_ORIGIN_FWD["lon"], abs=1e-6)
        board = legs[1]["board_stop"]
        assert walk1["to"]["lat"] == pytest.approx(board["lat"], abs=1e-6)
        assert walk1["to"]["lon"] == pytest.approx(board["lon"], abs=1e-6)

        walk2 = legs[2]
        assert walk2["type"] == "walk"
        alight = legs[1]["alight_stop"]
        assert walk2["from"]["lat"] == pytest.approx(alight["lat"], abs=1e-6)
        assert walk2["from"]["lon"] == pytest.approx(alight["lon"], abs=1e-6)
        assert walk2["to"]["lat"] == pytest.approx(_DEST_FWD["lat"], abs=1e-6)
        assert walk2["to"]["lon"] == pytest.approx(_DEST_FWD["lon"], abs=1e-6)


class TestAllRideLegsHaveShape:
    """Every ride leg in plan() output carries a shape with >= 2 pairs."""

    def test_shape_on_all_ride_legs_forward(self, monkeypatch):
        _patch_all(monkeypatch, [_FORWARD_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_FWD["lat"], _ORIGIN_FWD["lon"],
            _DEST_FWD["lat"], _DEST_FWD["lon"],
        )
        assert result["status"] == "ok"
        for opt in result["options"]:
            for leg in opt["legs"]:
                if leg["type"] == "ride":
                    shape = leg["shape"]
                    assert isinstance(shape, list)
                    assert len(shape) >= 2
                    for pt in shape:
                        assert isinstance(pt, list)
                        assert len(pt) == 2

    def test_shape_on_all_ride_legs_reversed(self, monkeypatch):
        _patch_all(monkeypatch, [_REVERSED_SEQUENCE])
        result = planner_mod.plan(
            _ORIGIN_REV["lat"], _ORIGIN_REV["lon"],
            _DEST_REV["lat"], _DEST_REV["lon"],
        )
        assert result["status"] == "ok"
        for opt in result["options"]:
            for leg in opt["legs"]:
                if leg["type"] == "ride":
                    shape = leg["shape"]
                    assert isinstance(shape, list)
                    assert len(shape) >= 2
