"""Determinism and correctness tests for tools/extract_stops.py using a synthetic GTFS fixture."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path("tools/extract_stops.py")

# Expanded bbox constants (must match extract_stops.py)
EXP_MIN_LAT, EXP_MAX_LAT = 14.535, 14.620
EXP_MIN_LON, EXP_MAX_LON = 121.005, 121.085


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture()
def synthetic_feed(tmp_path: Path) -> Path:
    """Build a tiny GTFS feed covering edge-cases for the extractor."""
    feed = tmp_path / "feed"
    feed.mkdir()

    # -- agency.txt --
    _write_csv(
        feed / "agency.txt",
        ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        [
            {"agency_id": "LTFRB", "agency_name": "LTFRB",
             "agency_url": "http://ltfrb.gov.ph", "agency_timezone": "Asia/Manila"},
            {"agency_id": "MRTC", "agency_name": "MRTC",
             "agency_url": "http://dotcmrt3.gov.ph", "agency_timezone": "Asia/Manila"},
        ],
    )

    # -- routes.txt --
    # 2 jeepney routes + 1 rail (MRT-like)
    _write_csv(
        feed / "routes.txt",
        ["agency_id", "route_short_name", "route_long_name", "route_desc",
         "route_type", "route_url", "route_color", "route_text_color", "route_id"],
        [
            # Jeepney route A: 3 in-bbox stops + 1 out-of-bbox
            {"agency_id": "LTFRB", "route_short_name": "", "route_long_name": "Route A In-Bbox",
             "route_desc": "", "route_type": "3", "route_url": "",
             "route_color": "", "route_text_color": "", "route_id": "R_JEEP_A"},
            # Jeepney route B: 2 in-bbox stops
            {"agency_id": "LTFRB", "route_short_name": "", "route_long_name": "Route B Short",
             "route_desc": "", "route_type": "3", "route_url": "",
             "route_color": "", "route_text_color": "", "route_id": "R_JEEP_B"},
            # Rail MRT-like route
            {"agency_id": "MRTC", "route_short_name": "MRT-Test",
             "route_long_name": "Test Rail Line", "route_desc": "",
             "route_type": "2", "route_url": "",
             "route_color": "", "route_text_color": "", "route_id": "R_MRT"},
        ],
    )

    # -- stops.txt --
    # Stop 1: in bbox (14.570, 121.040) — in both routes A & B
    # Stop 2: in bbox (14.580, 121.050) — in route A only
    # Stop 3: in bbox (14.590, 121.060) — in route A & B
    # Stop 4: OUT of bbox (14.500, 121.000) — should be excluded
    # Stop 5: rail "Boni RAIL" (14.573, 121.048) — rail
    # Stop 6: rail "Shaw RAIL" (14.581, 121.054) — rail
    # Stop 7: rail "Taft RAIL" (14.540, 121.010) — rail
    _write_csv(
        feed / "stops.txt",
        ["stop_id", "stop_code", "stop_name", "stop_desc",
         "stop_lat", "stop_lon", "zone_id", "stop_url",
         "location_type", "parent_station", "wheelchair_boarding"],
        [
            {"stop_id": "S1", "stop_code": "", "stop_name": "Inbox Stop One",
             "stop_desc": "", "stop_lat": "14.570", "stop_lon": "121.040",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S2", "stop_code": "", "stop_name": "Inbox Stop Two",
             "stop_desc": "", "stop_lat": "14.580", "stop_lon": "121.050",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S3", "stop_code": "", "stop_name": "Inbox Stop Three",
             "stop_desc": "", "stop_lat": "14.590", "stop_lon": "121.060",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S4", "stop_code": "", "stop_name": "Outbox Stop",
             "stop_desc": "", "stop_lat": "14.500", "stop_lon": "121.000",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S5", "stop_code": "", "stop_name": "Boni RAIL",
             "stop_desc": "", "stop_lat": "14.573", "stop_lon": "121.048",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S6", "stop_code": "", "stop_name": "Shaw RAIL",
             "stop_desc": "", "stop_lat": "14.581", "stop_lon": "121.054",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
            {"stop_id": "S7", "stop_code": "", "stop_name": "Taft RAIL",
             "stop_desc": "", "stop_lat": "14.540", "stop_lon": "121.010",
             "zone_id": "", "stop_url": "", "location_type": "0",
             "parent_station": "", "wheelchair_boarding": "0"},
        ],
    )

    # -- trips.txt --
    # Route A: two trips — TA1 (3 stops), TA2 (2 stops) → TA1 wins (most stops)
    # Route B: one trip TB1 (2 stops)
    # Rail: two trips — TM1 (3 stops), TM2 (3 stops) → tie → lexicographically smallest TM1
    _write_csv(
        feed / "trips.txt",
        ["route_id", "service_id", "trip_short_name", "trip_headsign",
         "direction_id", "block_id", "shape_id", "trip_id"],
        [
            {"route_id": "R_JEEP_A", "service_id": "1", "trip_short_name": "",
             "trip_headsign": "", "direction_id": "", "block_id": "",
             "shape_id": "sh1", "trip_id": "TA1"},
            {"route_id": "R_JEEP_A", "service_id": "1", "trip_short_name": "",
             "trip_headsign": "", "direction_id": "", "block_id": "",
             "shape_id": "sh1", "trip_id": "TA2"},
            {"route_id": "R_JEEP_B", "service_id": "1", "trip_short_name": "",
             "trip_headsign": "", "direction_id": "", "block_id": "",
             "shape_id": "sh2", "trip_id": "TB1"},
            {"route_id": "R_MRT", "service_id": "1", "trip_short_name": "",
             "trip_headsign": "", "direction_id": "", "block_id": "",
             "shape_id": "sh3", "trip_id": "TM1"},
            {"route_id": "R_MRT", "service_id": "1", "trip_short_name": "",
             "trip_headsign": "", "direction_id": "", "block_id": "",
             "shape_id": "sh3", "trip_id": "TM2"},
        ],
    )

    # -- stop_times.txt --
    # TA1: S1→S2→S3 (3 stops, tie-break winner for R_JEEP_A)
    #   Stop S2 has empty shape_dist_traveled; S1 and S3 have non-empty
    # TA2: S1→S3 (2 stops, fewer — should NOT win)
    # TB1: S1→S3 (2 stops — only trip for R_JEEP_B)
    # TM1: S7→S5→S6 (3 stops, tie-break: "TM1" < "TM2" lex)
    # TM2: S7→S6→S5 (3 stops)
    _write_csv(
        feed / "stop_times.txt",
        ["trip_id", "stop_sequence", "stop_id", "arrival_time",
         "departure_time", "stop_headsign", "pickup_type", "drop_off_type",
         "shape_dist_traveled"],
        [
            # TA1 (3 stops)
            {"trip_id": "TA1", "stop_sequence": "1", "stop_id": "S1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": "100.5"},
            {"trip_id": "TA1", "stop_sequence": "2", "stop_id": "S2",
             "arrival_time": "08:05:00", "departure_time": "08:05:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TA1", "stop_sequence": "3", "stop_id": "S3",
             "arrival_time": "08:10:00", "departure_time": "08:10:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": "250.0"},
            # TA2 (2 stops — fewer)
            {"trip_id": "TA2", "stop_sequence": "1", "stop_id": "S1",
             "arrival_time": "09:00:00", "departure_time": "09:00:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TA2", "stop_sequence": "2", "stop_id": "S3",
             "arrival_time": "09:10:00", "departure_time": "09:10:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            # TB1 (2 stops)
            {"trip_id": "TB1", "stop_sequence": "1", "stop_id": "S1",
             "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TB1", "stop_sequence": "2", "stop_id": "S3",
             "arrival_time": "08:10:00", "departure_time": "08:10:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            # TM1 (3 stops, tie-break winner for R_MRT)
            {"trip_id": "TM1", "stop_sequence": "1", "stop_id": "S7",
             "arrival_time": "07:00:00", "departure_time": "07:00:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TM1", "stop_sequence": "2", "stop_id": "S5",
             "arrival_time": "07:05:00", "departure_time": "07:05:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TM1", "stop_sequence": "3", "stop_id": "S6",
             "arrival_time": "07:10:00", "departure_time": "07:10:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            # TM2 (3 stops, same count but "TM2" > "TM1" lex)
            {"trip_id": "TM2", "stop_sequence": "1", "stop_id": "S7",
             "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TM2", "stop_sequence": "2", "stop_id": "S6",
             "arrival_time": "08:05:00", "departure_time": "08:05:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
            {"trip_id": "TM2", "stop_sequence": "3", "stop_id": "S5",
             "arrival_time": "08:10:00", "departure_time": "08:10:00",
             "stop_headsign": "", "pickup_type": "0", "drop_off_type": "0",
             "shape_dist_traveled": ""},
        ],
    )

    return feed


def _run_extractor(feed_dir: Path, data_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--feed-dir", str(feed_dir),
         "--data-dir", str(data_dir)],
        capture_output=True, text=True,
    )


class TestDeterminism:
    def test_byte_identical_output(self, synthetic_feed: Path, tmp_path: Path):
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        out1.mkdir()
        out2.mkdir()

        r1 = _run_extractor(synthetic_feed, out1)
        assert r1.returncode == 0, r1.stderr
        r2 = _run_extractor(synthetic_feed, out2)
        assert r2.returncode == 0, r2.stderr

        for name in ["stops_mandaluyong.json", "stop_sequences.json",
                      "mrt3_stations.json"]:
            f1 = (out1 / name).read_bytes()
            f2 = (out2 / name).read_bytes()
            assert f1 == f2, f"{name} differs between runs"


class TestCorrectness:
    @pytest.fixture(autouse=True)
    def _run(self, synthetic_feed: Path, tmp_path: Path):
        self.out = tmp_path / "out"
        self.out.mkdir()
        r = _run_extractor(synthetic_feed, self.out)
        assert r.returncode == 0, r.stderr
        with (self.out / "stops_mandaluyong.json").open() as fh:
            self.stops = json.load(fh)
        with (self.out / "stop_sequences.json").open() as fh:
            self.sequences = json.load(fh)
        with (self.out / "mrt3_stations.json").open() as fh:
            self.mrt3 = json.load(fh)

    def test_bbox_inclusion(self):
        stop_ids = {s["stop_id"] for s in self.stops}
        assert "S1" in stop_ids   # (14.570, 121.040) — inside
        assert "S2" in stop_ids   # (14.580, 121.050) — inside
        assert "S3" in stop_ids   # (14.590, 121.060) — inside
        assert "S4" not in stop_ids  # (14.500, 121.000) — outside

    def test_out_of_bbox_excluded(self):
        assert all(
            EXP_MIN_LAT <= s["lat"] <= EXP_MAX_LAT and
            EXP_MIN_LON <= s["lon"] <= EXP_MAX_LON
            for s in self.stops
        )

    def test_route_ids_membership(self):
        s1 = next(s for s in self.stops if s["stop_id"] == "S1")
        assert "R_JEEP_A" in s1["route_ids"]
        assert "R_JEEP_B" in s1["route_ids"]
        s2 = next(s for s in self.stops if s["stop_id"] == "S2")
        assert s2["route_ids"] == ["R_JEEP_A"]

    def test_tie_break_winner_is_TA1(self):
        seq_a = next(s for s in self.sequences if s["route_id"] == "R_JEEP_A")
        assert len(seq_a["stops"]) == 3
        stop_ids = [st["stop_id"] for st in seq_a["stops"]]
        assert stop_ids == ["S1", "S2", "S3"]

    def test_sdt_key_present_only_when_nonempty(self):
        seq_a = next(s for s in self.sequences if s["route_id"] == "R_JEEP_A")
        s1_stop = next(st for st in seq_a["stops"] if st["stop_id"] == "S1")
        assert "shape_dist_traveled" in s1_stop
        assert s1_stop["shape_dist_traveled"] == 100.5
        s2_stop = next(st for st in seq_a["stops"] if st["stop_id"] == "S2")
        assert "shape_dist_traveled" not in s2_stop
        s3_stop = next(st for st in seq_a["stops"] if st["stop_id"] == "S3")
        assert "shape_dist_traveled" in s3_stop
        assert s3_stop["shape_dist_traveled"] == 250.0

    def test_mrt3_tie_break_TM1_wins(self):
        assert self.mrt3["route_id"] == "R_MRT"
        assert len(self.mrt3["stations"]) == 3
        station_ids = [s["stop_id"] for s in self.mrt3["stations"]]
        # TM1 order: S7→S5→S6
        assert station_ids == ["S7", "S5", "S6"]

    def test_mrt3_sanity_names(self):
        names = {s["stop_name"].lower().strip() for s in self.mrt3["stations"]}
        assert any("shaw" in n for n in names)
        assert any("boni" in n for n in names)

    def test_rail_not_in_jeepney_stops(self):
        stop_ids = {s["stop_id"] for s in self.stops}
        # S5, S6, S7 are rail-only stops — not served by jeepney routes
        assert "S5" not in stop_ids
        assert "S6" not in stop_ids
        assert "S7" not in stop_ids
