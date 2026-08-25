"""Tests for the extracted stops / sequences / MRT-3 dataset files."""

import json
from pathlib import Path

import pytest

DATA = Path("data")

EXP_MIN_LAT, EXP_MAX_LAT = 14.535, 14.620
EXP_MIN_LON, EXP_MAX_LON = 121.005, 121.085


# ---------------------------------------------------------------------------
# stops_mandaluyong.json
# ---------------------------------------------------------------------------

class TestStopsSchema:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.path = DATA / "stops_mandaluyong.json"
        with self.path.open(encoding="utf-8") as fh:
            self.data = json.load(fh)

    def test_non_empty(self):
        assert len(self.data) > 0

    def test_required_keys(self):
        required = {"stop_id", "stop_name", "lat", "lon", "route_ids"}
        for entry in self.data:
            assert required == set(entry.keys()), entry["stop_id"]

    def test_types(self):
        for entry in self.data:
            assert isinstance(entry["stop_id"], str)
            assert isinstance(entry["stop_name"], str)
            assert isinstance(entry["lat"], float)
            assert isinstance(entry["lon"], float)
            assert isinstance(entry["route_ids"], list)

    def test_bbox_bounds(self):
        for entry in self.data:
            assert EXP_MIN_LAT <= entry["lat"] <= EXP_MAX_LAT, entry["stop_id"]
            assert EXP_MIN_LON <= entry["lon"] <= EXP_MAX_LON, entry["stop_id"]

    def test_route_ids_sorted_unique_str(self):
        for entry in self.data:
            rids = entry["route_ids"]
            assert rids == sorted(set(rids)), entry["stop_id"]
            for r in rids:
                assert isinstance(r, str)


# ---------------------------------------------------------------------------
# stop_sequences.json
# ---------------------------------------------------------------------------

class TestStopSequences:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.seq_path = DATA / "stop_sequences.json"
        self.rm_path = DATA / "routes_mandaluyong.json"
        with self.seq_path.open(encoding="utf-8") as fh:
            self.sequences = json.load(fh)
        with self.rm_path.open(encoding="utf-8") as fh:
            self.routes = json.load(fh)

    def test_coverage(self):
        rm_ids = {r["route_id"] for r in self.routes}
        seq_ids = {s["route_id"] for s in self.sequences}
        coverage = len(seq_ids & rm_ids) / len(rm_ids) * 100 if rm_ids else 0
        assert coverage >= 90, f"Coverage {coverage:.1f}% < 90%"

    def test_present_sequences_have_at_least_two_stops(self):
        for entry in self.sequences:
            if entry["stops"]:
                assert len(entry["stops"]) >= 2, (
                    f"Route {entry['route_id']} has only {len(entry['stops'])} stop(s)"
                )

    def test_stop_entry_keys(self):
        for entry in self.sequences:
            for stop in entry["stops"]:
                assert {"stop_id", "stop_name", "lat", "lon"}.issubset(
                    stop.keys()
                ), f"Route {entry['route_id']} stop missing keys: {stop}"
                assert isinstance(stop["lat"], float)
                assert isinstance(stop["lon"], float)


# ---------------------------------------------------------------------------
# mrt3_stations.json
# ---------------------------------------------------------------------------

class TestMrt3Stations:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.path = DATA / "mrt3_stations.json"
        with self.path.open(encoding="utf-8") as fh:
            self.data = json.load(fh)

    def test_shaw_present(self):
        names = [s["stop_name"].lower().strip() for s in self.data["stations"]]
        assert any("shaw" in n for n in names)

    def test_boni_present(self):
        names = [s["stop_name"].lower().strip() for s in self.data["stations"]]
        assert any("boni" in n for n in names)

    def test_all_have_float_lat_lon(self):
        for s in self.data["stations"]:
            assert isinstance(s["lat"], float)
            assert isinstance(s["lon"], float)

    def test_at_least_10_stations(self):
        assert len(self.data["stations"]) >= 10
