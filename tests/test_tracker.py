"""Tests for guide/tracker.py and the tracking API endpoints.

Covers: feature flag (disabled by enabled), round-trip, invalid points,
idempotent stop, haversine, /api/config, and buffer/retry via node.
"""

import io
import json
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import guide.tracker as tracker_mod


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_web_api.py pattern)
# ---------------------------------------------------------------------------

def _get_response(path):
    from web.server import FareHandler
    captured = {"status": 0}
    class _H(FareHandler):
        def send_response(self, code, *a, **kw):
            captured["status"] = code
        def send_header(self, *a, **kw):
            pass
        def end_headers(self):
            pass
    handler = _H.__new__(_H)
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "GET"
    handler.headers = {}
    handler.path = path
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler.log_message = lambda *a, **kw: None
    handler.do_GET()
    handler.wfile.seek(0)
    body = handler.wfile.read().decode()
    return captured["status"], json.loads(body)


def _post_response(path, body_dict):
    from web.server import FareHandler
    captured = {"status": 0}
    class _H(FareHandler):
        def send_response(self, code, *a, **kw):
            captured["status"] = code
        def send_header(self, *a, **kw):
            pass
        def end_headers(self):
            pass
    payload = json.dumps(body_dict).encode("utf-8")
    rfile = io.BytesIO(payload)
    wfile = io.BytesIO()
    handler = _H.__new__(_H)
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "POST"
    handler.headers = {"Content-Length": str(len(payload))}
    handler.path = path
    handler.wfile = wfile
    handler.rfile = rfile
    handler.client_address = ("127.0.0.1", 12345)
    handler.log_message = lambda *a, **kw: None
    handler.do_POST()
    wfile.seek(0)
    body = wfile.read().decode()
    return captured["status"], json.loads(body)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_tracker():
    """Reset in-memory state before and after each test."""
    tracker_mod.reset()
    yield
    tracker_mod.reset()


@pytest.fixture(autouse=True)
def _no_env_flag(monkeypatch):
    """Ensure TRACKING_ENABLED is unset by default."""
    monkeypatch.delenv("TRACKING_ENABLED", raising=False)


# ---------------------------------------------------------------------------
# 1. Disabled flag (default): all track POSTs → 503; GET /api/trips → empty
# ---------------------------------------------------------------------------

class TestTrackingDisabled:
    def test_start_returns_503(self):
        status, data = _post_response("/api/track/start", {})
        assert status == 503
        assert data["error"] == "tracking not available"

    def test_point_returns_503(self):
        status, data = _post_response("/api/track/point", {
            "trip_id": "fake", "lat": 14.0, "lon": 121.0,
            "ts": 1000, "accuracy": 10
        })
        assert status == 503
        assert data["error"] == "tracking not available"

    def test_stop_returns_503(self):
        status, data = _post_response("/api/track/stop", {"trip_id": "fake"})
        assert status == 503
        assert data["error"] == "tracking not available"

    def test_trips_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips.jsonl")
        status, data = _get_response("/api/trips")
        assert status == 200
        assert data["trips"] == []

    def test_no_data_directory_created(self, tmp_path, monkeypatch):
        """While disabled, no data/trips/ directory should be created."""
        monkeypatch.setattr(tracker_mod, "_TRIPS_DIR", tmp_path / "trips")
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips" / "trips.jsonl")
        # These calls should all return 503 without touching disk
        _post_response("/api/track/start", {})
        _post_response("/api/track/point", {
            "trip_id": "fake", "lat": 14.0, "lon": 121.0,
            "ts": 1000, "accuracy": 10
        })
        _post_response("/api/track/stop", {"trip_id": "fake"})
        assert not (tmp_path / "trips").exists()


# ---------------------------------------------------------------------------
# 2. Enabled round-trip: start → points → stop → trips
# ---------------------------------------------------------------------------

class TestTrackingEnabledRoundTrip:
    def test_full_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACKING_ENABLED", "1")
        monkeypatch.setattr(tracker_mod, "_TRIPS_DIR", tmp_path / "trips")
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips" / "trips.jsonl")
        fixed_now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(tracker_mod, "_now", lambda: fixed_now)

        # Start
        status, data = _post_response("/api/track/start", {"alias": "alice"})
        assert status == 200
        trip_id = data["trip_id"]
        assert len(trip_id) > 0

        # Post 3 valid points
        points = [
            {"trip_id": trip_id, "lat": 14.5810, "lon": 121.0540, "ts": 1000, "accuracy": 10},
            {"trip_id": trip_id, "lat": 14.5820, "lon": 121.0550, "ts": 1005, "accuracy": 12},
            {"trip_id": trip_id, "lat": 14.5830, "lon": 121.0560, "ts": 1010, "accuracy": 8},
        ]
        for pt in points:
            status, data = _post_response("/api/track/point", pt)
            assert status == 200
            assert data["ok"] is True

        # Stop
        status, data = _post_response("/api/track/stop", {"trip_id": trip_id, "alias": "alice"})
        assert status == 200
        assert data["point_count"] == 3
        assert data["trip_id"] == trip_id
        assert "distance_km" in data
        assert data["distance_km"] > 0

        # Verify trips.jsonl has exactly 1 line
        ledger = tmp_path / "trips" / "trips.jsonl"
        assert ledger.exists()
        lines = [l for l in ledger.read_text().strip().split("\n") if l.strip()]
        assert len(lines) == 1
        summary = json.loads(lines[0])
        assert summary["point_count"] == 3

        # Verify points file
        points_file = tmp_path / "trips" / f"points_{trip_id}.jsonl"
        assert points_file.exists()
        pt_lines = [l for l in points_file.read_text().strip().split("\n") if l.strip()]
        assert len(pt_lines) == 3

        # GET /api/trips
        status, data = _get_response("/api/trips?limit=5")
        assert status == 200
        assert len(data["trips"]) == 1
        assert data["trips"][0]["point_count"] == 3
        assert data["trips"][0]["trip_id"] == trip_id
        # Summary fields only — no raw points
        assert "points" not in data["trips"][0]


# ---------------------------------------------------------------------------
# 3. Invalid point rejection
# ---------------------------------------------------------------------------

class TestInvalidPointRejection:
    def _start_trip(self):
        status, data = _post_response("/api/track/start", {})
        return data["trip_id"]

    def test_bad_lat(self):
        monkeypatch_set = True  # we rely on autouse _no_env_flag
        import os
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            tid = self._start_trip()
            status, data = _post_response("/api/track/point", {
                "trip_id": tid, "lat": 999, "lon": 121.0, "ts": 1000, "accuracy": 10
            })
            assert status == 400
            assert "lat" in data["error"].lower()
        finally:
            del os.environ["TRACKING_ENABLED"]

    def test_non_numeric_lon(self):
        import os
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            tid = self._start_trip()
            status, data = _post_response("/api/track/point", {
                "trip_id": tid, "lat": 14.0, "lon": "abc", "ts": 1000, "accuracy": 10
            })
            assert status == 400
            assert "lon" in data["error"].lower()
        finally:
            del os.environ["TRACKING_ENABLED"]

    def test_accuracy_too_high(self):
        import os
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            tid = self._start_trip()
            status, data = _post_response("/api/track/point", {
                "trip_id": tid, "lat": 14.0, "lon": 121.0, "ts": 1000, "accuracy": 250
            })
            assert status == 400
            assert "accuracy" in data["error"].lower()
        finally:
            del os.environ["TRACKING_ENABLED"]

    def test_nothing_appended_on_bad_point(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACKING_ENABLED", "1")
        monkeypatch.setattr(tracker_mod, "_TRIPS_DIR", tmp_path / "trips")
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips" / "trips.jsonl")
        tid = self._start_trip()
        _post_response("/api/track/point", {
            "trip_id": tid, "lat": 999, "lon": 121.0, "ts": 1000, "accuracy": 10
        })
        trip = tracker_mod._trips[tid]
        assert len(trip["points"]) == 0


# ---------------------------------------------------------------------------
# 4. Unknown trip_id → 404; point after stop → 409
# ---------------------------------------------------------------------------

class TestTripLifecycle:
    def test_unknown_trip_point(self):
        import os
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            status, data = _post_response("/api/track/point", {
                "trip_id": "nonexistent-uuid", "lat": 14.0, "lon": 121.0,
                "ts": 1000, "accuracy": 10
            })
            assert status == 404
        finally:
            del os.environ["TRACKING_ENABLED"]

    def test_unknown_trip_stop(self):
        import os
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            status, data = _post_response("/api/track/stop", {"trip_id": "nonexistent"})
            assert status == 404
        finally:
            del os.environ["TRACKING_ENABLED"]

    def test_point_after_stop(self, tmp_path, monkeypatch):
        import os
        monkeypatch.setattr(tracker_mod, "_TRIPS_DIR", tmp_path / "trips")
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips" / "trips.jsonl")
        os.environ["TRACKING_ENABLED"] = "1"
        try:
            status, data = _post_response("/api/track/start", {})
            tid = data["trip_id"]
            _post_response("/api/track/point", {
                "trip_id": tid, "lat": 14.0, "lon": 121.0, "ts": 1000, "accuracy": 10
            })
            _post_response("/api/track/stop", {"trip_id": tid})
            status, data = _post_response("/api/track/point", {
                "trip_id": tid, "lat": 14.0, "lon": 121.0, "ts": 1001, "accuracy": 10
            })
            assert status == 409
        finally:
            del os.environ["TRACKING_ENABLED"]


# ---------------------------------------------------------------------------
# 5. Idempotent stop
# ---------------------------------------------------------------------------

class TestIdempotentStop:
    def test_second_stop_returns_same_summary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACKING_ENABLED", "1")
        monkeypatch.setattr(tracker_mod, "_TRIPS_DIR", tmp_path / "trips")
        monkeypatch.setattr(tracker_mod, "_TRIPS_LEDGER", tmp_path / "trips" / "trips.jsonl")

        status, data = _post_response("/api/track/start", {})
        tid = data["trip_id"]
        _post_response("/api/track/point", {
            "trip_id": tid, "lat": 14.0, "lon": 121.0, "ts": 1000, "accuracy": 10
        })

        # First stop
        status1, data1 = _post_response("/api/track/stop", {"trip_id": tid})
        assert status1 == 200
        assert data1["point_count"] == 1

        # Second stop — same summary, 200
        status2, data2 = _post_response("/api/track/stop", {"trip_id": tid})
        assert status2 == 200
        assert data2["trip_id"] == data1["trip_id"]
        assert data2["point_count"] == data1["point_count"]

        # Verify only 1 summary line in ledger
        ledger = tmp_path / "trips" / "trips.jsonl"
        lines = [l for l in ledger.read_text().strip().split("\n") if l.strip()]
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# 6. Haversine distance sanity
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_known_distance(self):
        """Manila (14.5995, 120.9842) to Quezon City (14.6760, 121.0437) ≈ 10.7 km."""
        d = tracker_mod.haversine_km(14.5995, 120.9842, 14.6760, 121.0437)
        assert abs(d - 10.7) < 0.5  # within ~5%

    def test_same_point_zero(self):
        d = tracker_mod.haversine_km(14.581, 121.054, 14.581, 121.054)
        assert d == 0.0


# ---------------------------------------------------------------------------
# 7. /api/config
# ---------------------------------------------------------------------------

class TestApiConfig:
    def test_disabled_by_default(self):
        status, data = _get_response("/api/config")
        assert status == 200
        assert data["tracking_enabled"] is False

    def test_enabled_when_set(self, monkeypatch):
        monkeypatch.setenv("TRACKING_ENABLED", "1")
        status, data = _get_response("/api/config")
        assert status == 200
        assert data["tracking_enabled"] is True

    def test_enabled_case_insensitive(self, monkeypatch):
        for val in ("1", "true", "on", "TRUE", "ON", "True"):
            monkeypatch.setenv("TRACKING_ENABLED", val)
            status, data = _get_response("/api/config")
            assert data["tracking_enabled"] is True, f"Failed for value: {val}"

    def test_disabled_for_other_values(self, monkeypatch):
        for val in ("0", "false", "no", "", "yes"):
            monkeypatch.setenv("TRACKING_ENABLED", val)
            status, data = _get_response("/api/config")
            assert data["tracking_enabled"] is False, f"Failed for value: {val}"


# ---------------------------------------------------------------------------
# 8. Buffer/retry logic — unit test via Node.js
# ---------------------------------------------------------------------------

class TestTrackingCoreNode:
    def test_tracking_core_via_node(self):
        """Run tracking-core.js tests in Node via subprocess."""
        harness = textwrap.dedent("""\
            // Load TrackingCore
            var fs = require('fs');
            var vm = require('vm');
            var code = fs.readFileSync(process.argv[2], 'utf8');
            var ctx = { window: {}, module: module, console: console };
            vm.runInNewContext(code, ctx);
            var TrackingCore = module.exports;

            var passed = 0;
            var failed = 0;

            function assert(cond, msg) {
              if (!cond) {
                console.error('FAIL: ' + msg);
                failed++;
              } else {
                passed++;
              }
            }

            // Test 1: shouldSend respects interval
            var tc = TrackingCore({ intervalMs: 5000 });
            assert(tc.shouldSend(0) === true, 'first send should go');
            assert(tc.shouldSend(1000) === false, '1s too soon');
            assert(tc.shouldSend(5001) === true, '5s later should go');

            // Test 2: buffer + takeBuffer
            var tc2 = TrackingCore({ intervalMs: 5000, maxBuffer: 3 });
            tc2.onSendFailure({ lat: 1 });
            tc2.onSendFailure({ lat: 2 });
            tc2.onSendFailure({ lat: 3 });
            assert(tc2.bufferLength === 3, 'buffer has 3');
            tc2.onSendFailure({ lat: 4 });  // should drop oldest
            assert(tc2.bufferLength === 3, 'buffer capped at 3');
            var buf = tc2.takeBuffer();
            assert(buf.length === 3, 'takeBuffer returns 3');
            assert(tc2.bufferLength === 0, 'buffer empty after take');
            assert(buf[0].lat === 2, 'oldest was dropped');

            // Test 3: reset clears everything
            tc2.reset();
            assert(tc2.bufferLength === 0, 'buffer empty after reset');

            // Test 4: intervalMs is adjustable
            var tc3 = TrackingCore({ intervalMs: 15000 });
            assert(tc3.intervalMs === 15000, 'initial interval 15s');
            tc3.intervalMs = 5000;
            assert(tc3.intervalMs === 5000, 'changed interval 5s');
            assert(tc3.shouldSend(0) === true, 'first send at new interval');
            assert(tc3.shouldSend(3000) === false, '3s too soon at 5s');
            assert(tc3.shouldSend(5001) === true, '5s at new interval');

            // Test 5: peekBuffer doesn't drain
            var tc4 = TrackingCore({ intervalMs: 5000, maxBuffer: 10 });
            tc4.onSendFailure({ lat: 10 });
            tc4.onSendFailure({ lat: 20 });
            assert(tc4.peekBuffer().length === 2, 'peek shows 2');
            assert(tc4.bufferLength === 2, 'peek does not drain');
            tc4.takeBuffer();
            assert(tc4.bufferLength === 0, 'drained after take');

            if (failed > 0) {
              console.error('\\n' + failed + ' test(s) FAILED');
              process.exit(1);
            } else {
              console.log('All ' + passed + ' tracking-core tests passed');
              process.exit(0);
            }
        """)
        harness_path = Path("/tmp/tracking_core_test.js")
        harness_path.write_text(harness)
        core_path = Path(__file__).resolve().parent.parent / "web" / "tracking-core.js"
        result = subprocess.run(
            ["node", str(harness_path), str(core_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Node tests failed:\n{result.stdout}\n{result.stderr}"
