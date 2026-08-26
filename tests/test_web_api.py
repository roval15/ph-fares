"""Tests for web/server.py API endpoints (geocode, plan, fare, feedback, freshness, community).

No live server or network calls — we use mock sockets to test the handler.
"""

import io
import json
from decimal import Decimal
from unittest.mock import patch

import pytest


def _get_response(path):
    """Route a fake GET through FareHandler and return (status_code, json_dict)."""
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


def _get_response_raw(path):
    """Like _get_response but returns the raw body string instead of parsed JSON."""
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
    return captured["status"], body


def _post_response(path, body_dict):
    """Route a fake POST through FareHandler and return (status_code, json_dict)."""
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


class TestGeocodeAPI:
    def test_missing_q(self):
        status, data = _get_response("/api/geocode")
        assert status == 400
        assert data["status"] == "error"
        assert "q" in data["message"].lower()

    def test_blank_q(self):
        status, data = _get_response("/api/geocode?q=+")
        assert status == 400
        assert data["status"] == "error"

    @patch("web.server.guide_geocode.geocode")
    def test_success(self, mock_geocode):
        mock_geocode.return_value = [
            {"display_name": "Shaw Blvd, Mandaluyong", "lat": 14.581, "lon": 121.054}
        ]
        status, data = _get_response("/api/geocode?q=Shaw")
        assert status == 200
        assert data["status"] == "ok"
        assert data["query"] == "Shaw"
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["lat"] == 14.581

    @patch("web.server.guide_geocode.geocode")
    def test_zero_results(self, mock_geocode):
        mock_geocode.return_value = []
        status, data = _get_response("/api/geocode?q=xyznonexistent")
        assert status == 200
        assert data["status"] == "ok"
        assert data["candidates"] == []

    @patch("web.server.guide_geocode.geocode")
    def test_network_error_returns_503(self, mock_geocode):
        import urllib.error
        mock_geocode.side_effect = urllib.error.URLError("timeout")
        status, data = _get_response("/api/geocode?q=Shaw")
        assert status == 503
        assert data["status"] == "error"
        assert "unavailable" in data["message"].lower()

    @patch("web.server.guide_geocode.geocode")
    def test_socket_timeout_returns_503(self, mock_geocode):
        import socket
        mock_geocode.side_effect = socket.timeout("timed out")
        status, data = _get_response("/api/geocode?q=Shaw")
        assert status == 503
        assert data["status"] == "error"

    @patch("web.server.guide_geocode.geocode")
    def test_unexpected_error_returns_500(self, mock_geocode):
        mock_geocode.side_effect = RuntimeError("boom")
        status, data = _get_response("/api/geocode?q=Shaw")
        assert status == 500
        assert data["status"] == "error"
        assert "unexpected" in data["message"].lower()


class TestPlanAPI:
    def test_missing_params(self):
        status, data = _get_response("/api/plan?from_lat=x")
        assert status == 400
        assert data["status"] == "error"
        assert "from_lon" in data["message"]

    def test_non_numeric_param(self):
        status, data = _get_response("/api/plan?from_lat=abc&from_lon=1&to_lat=1&to_lon=1")
        assert status == 400
        assert "from_lat" in data["message"]

    def test_infinite_param(self):
        status, data = _get_response("/api/plan?from_lat=inf&from_lon=1&to_lat=1&to_lon=1")
        assert status == 400
        assert "from_lat" in data["message"]

    @patch("web.server.guide_planner.plan")
    def test_success(self, mock_plan):
        mock_plan.return_value = {
            "status": "ok",
            "options": [
                {
                    "legs": [
                        {"type": "walk", "from": {"lat": 1, "lon": 1}, "to": {"lat": 2, "lon": 2}, "distance_m": 100},
                        {"type": "ride", "mode": "jeepney_traditional", "route_id": "R1",
                         "route_long_name": "Shaw-Ortigas",
                         "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                         "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3},
                         "distance_km": 2.5},
                    ],
                    "fare_breakdown": {"jeepney_traditional": 15.0},
                    "total_fare": 15.0,
                    "notes": [],
                }
            ],
        }
        status, data = _get_response(
            "/api/plan?from_lat=14.581&from_lon=121.054&to_lat=14.5729&to_lon=121.048"
        )
        assert status == 200
        assert data["status"] == "ok"
        assert len(data["options"]) == 1
        assert data["options"][0]["total_fare"] == 15.0
        mock_plan.assert_called_once_with(14.581, 121.054, 14.5729, 121.048)

    @patch("web.server.guide_planner.plan")
    def test_no_route(self, mock_plan):
        mock_plan.return_value = {
            "status": "no_route",
            "message": "No direct route found between the given points.",
            "from": {"lat": 1, "lon": 1},
            "to": {"lat": 2, "lon": 2},
        }
        status, data = _get_response("/api/plan?from_lat=1&from_lon=1&to_lat=2&to_lon=2")
        assert status == 200
        assert data["status"] == "no_route"

    @patch("web.server.guide_planner.plan")
    def test_unexpected_error(self, mock_plan):
        mock_plan.side_effect = RuntimeError("boom")
        status, data = _get_response("/api/plan?from_lat=1&from_lon=1&to_lat=2&to_lon=2")
        assert status == 500
        assert data["status"] == "error"
        assert "unexpected" in data["message"].lower()

    @patch("web.server.guide_planner.plan")
    def test_plan_with_decimal_fares(self, mock_plan):
        """Regression: plan() returns Decimal fares; _send_json must not crash."""
        mock_plan.return_value = {
            "status": "ok",
            "options": [
                {
                    "legs": [
                        {"type": "ride", "mode": "jeepney_traditional", "route_id": "R1",
                         "route_long_name": "Shaw-Ortigas",
                         "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                         "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3},
                         "distance_km": 7.5},
                    ],
                    "fare_breakdown": {"jeepney_traditional": Decimal("18.30")},
                    "total_fare": Decimal("18.30"),
                    "notes": [],
                }
            ],
        }
        status, raw = _get_response_raw(
            "/api/plan?from_lat=14.581&from_lon=121.054&to_lat=14.5729&to_lon=121.048"
        )
        assert status == 200
        data = json.loads(raw)
        assert data["status"] == "ok"
        opt = data["options"][0]
        assert opt["fare_breakdown"]["jeepney_traditional"] == 18.3
        assert opt["total_fare"] == 18.3


class TestFareAPIPreserved:
    def test_fare_ok(self):
        status, data = _get_response("/api/fare?mode=jeepney_traditional&km=7.5")
        assert status == 200
        assert data["status"] == "ok"
        assert data["fare"] == 18.3

    def test_fare_missing_mode(self):
        status, data = _get_response("/api/fare?km=5")
        assert status == 400
        assert "error" in data

    def test_fare_invalid_mode(self):
        status, data = _get_response("/api/fare?mode=invalid&km=5")
        assert status == 400


class TestIndexServed:
    def test_root_returns_html_with_tabs(self):
        from web.server import _HTML_PATH
        html = _HTML_PATH.read_text(encoding="utf-8")
        assert "Commute Guide" in html
        assert "Fare Calculator" in html
        assert 'name="viewport"' in html
        assert "tab-bar" in html

    def test_fare_calc_7_5km(self):
        """Regression: 7.5 km traditional = 18.30."""
        status, data = _get_response("/api/fare?mode=jeepney_traditional&km=7.5")
        assert data["fare"] == 18.3


class TestFeedbackAPI:
    def test_round_trip(self, tmp_path, monkeypatch):
        """POST /api/feedback writes exactly one line to the ledger."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")
        from datetime import datetime, timezone
        monkeypatch.setattr(fb_mod, "_now", lambda: datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc))

        status, data = _post_response("/api/feedback", {
            "route_id": "R1",
            "kind": "confirm",
            "alias": "alice",
        })
        assert status == 200
        assert data["ok"] is True
        assert "freshness" in data
        assert data["freshness"]["tier"] in ("green", "yellow", "gray", "disputed")

        # Check the ledger has exactly one line
        ledger = tmp_path / "community_updates.jsonl"
        assert ledger.exists()
        lines = [l for l in ledger.read_text().strip().split("\n") if l.strip()]
        assert len(lines) == 1

    def test_invalid_kind(self, tmp_path, monkeypatch):
        """Invalid kind returns 400 with friendly message."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")

        status, data = _post_response("/api/feedback", {
            "route_id": "R1",
            "kind": "bogus",
        })
        assert status == 400
        assert data["status"] == "error"
        assert "confirm" in data["message"].lower()

    def test_missing_route_id(self, tmp_path, monkeypatch):
        """Missing route_id returns 400."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")

        status, data = _post_response("/api/feedback", {
            "kind": "confirm",
        })
        assert status == 400
        assert data["status"] == "error"
        assert "route_id" in data["message"].lower()

    def test_empty_route_id(self, tmp_path, monkeypatch):
        """Empty route_id returns 400."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")

        status, data = _post_response("/api/feedback", {
            "route_id": "  ",
            "kind": "confirm",
        })
        assert status == 400

    def test_invalid_json_body(self, tmp_path, monkeypatch):
        """Invalid JSON body returns 400."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")

        from web.server import FareHandler
        captured = {"status": 0}

        class _H(FareHandler):
            def send_response(self, code, *a, **kw):
                captured["status"] = code
            def send_header(self, *a, **kw):
                pass
            def end_headers(self):
                pass

        payload = b"not json"
        handler = _H.__new__(_H)
        handler.requestline = "POST /api/feedback HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "POST"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.path = "/api/feedback"
        handler.wfile = io.BytesIO()
        handler.rfile = io.BytesIO(payload)
        handler.client_address = ("127.0.0.1", 12345)
        handler.log_message = lambda *a, **kw: None

        handler.do_POST()
        handler.wfile.seek(0)
        body = handler.wfile.read().decode()
        data = json.loads(body)
        assert captured["status"] == 400
        assert "json" in data["message"].lower()


class TestFreshnessAPI:
    def test_unknown_route_returns_gray(self):
        """Unknown route_id -> 200 with tier gray."""
        status, data = _get_response("/api/freshness?route_id=NONEXISTENT")
        assert status == 200
        assert data["tier"] == "gray"
        assert data["confirmations"] == 0

    def test_missing_route_id(self):
        """Missing route_id -> 400."""
        status, data = _get_response("/api/freshness")
        assert status == 400
        assert data["status"] == "error"
        assert "route_id" in data["message"].lower()

    def test_empty_route_id(self):
        """Empty route_id -> 400."""
        status, data = _get_response("/api/freshness?route_id=")
        assert status == 400


class TestPlanRouteFreshness:
    @patch("web.server.guide_planner.plan")
    @patch("web.server.feedback_freshness")
    def test_plan_includes_route_freshness(self, mock_freshness, mock_plan):
        """Plan response includes route_freshness covering every ride leg route_id."""
        mock_plan.return_value = {
            "status": "ok",
            "options": [
                {
                    "legs": [
                        {"type": "walk", "from": {"lat": 1, "lon": 1}, "to": {"lat": 2, "lon": 2}, "distance_m": 100},
                        {"type": "ride", "mode": "jeepney_traditional", "route_id": "R1",
                         "route_long_name": "Shaw-Ortigas",
                         "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                         "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3},
                         "distance_km": 2.5},
                    ],
                    "fare_breakdown": {"jeepney_traditional": 15.0},
                    "total_fare": 15.0,
                    "notes": [],
                },
                {
                    "legs": [
                        {"type": "walk", "from": {"lat": 1, "lon": 1}, "to": {"lat": 2, "lon": 2}, "distance_m": 100},
                        {"type": "ride", "mode": "jeepney_modern", "route_id": "R2",
                         "route_long_name": "Shaw-Ortigas Modern",
                         "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                         "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3},
                         "distance_km": 2.5},
                    ],
                    "fare_breakdown": {"jeepney_modern": 18.0},
                    "total_fare": 18.0,
                    "notes": [],
                },
            ],
        }
        mock_freshness.side_effect = lambda rid: {
            "tier": "green" if rid == "R1" else "gray",
            "confirmations": 5 if rid == "R1" else 0,
            "disputes": 0,
        }

        status, data = _get_response(
            "/api/plan?from_lat=14.581&from_lon=121.054&to_lat=14.5729&to_lon=121.048"
        )
        assert status == 200
        assert "route_freshness" in data
        rf = data["route_freshness"]
        assert "R1" in rf
        assert "R2" in rf
        assert rf["R1"]["tier"] == "green"
        assert rf["R2"]["tier"] == "gray"


class TestCommunityAPI:
    def test_returns_total_reports_and_stewards(self, tmp_path, monkeypatch):
        """GET /api/community returns total_reports and stewards structure."""
        import guide.feedback as fb_mod
        from datetime import datetime, timezone
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")
        monkeypatch.setattr(fb_mod, "_now", lambda: datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc))

        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp2", alias="alice")
        fb_mod.append_feedback("R2", "confirm", fingerprint="fp3", alias="bob")

        status, data = _get_response("/api/community")
        assert status == 200
        assert data["total_reports"] == 3
        assert len(data["corridors"]) == 2
        # R1 has 2 reports, R2 has 1
        assert data["corridors"][0]["route_id"] == "R1"
        assert data["corridors"][0]["report_count"] == 2
        assert data["corridors"][0]["top_stewards"][0]["alias"] == "alice"
        assert data["corridors"][0]["top_stewards"][0]["confirmations"] == 2
        assert data["corridors"][1]["route_id"] == "R2"
        assert data["corridors"][1]["report_count"] == 1

    def test_empty_ledger(self, tmp_path, monkeypatch):
        """Empty ledger returns 0 reports and empty corridors."""
        import guide.feedback as fb_mod
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "nonexistent.jsonl")

        status, data = _get_response("/api/community")
        assert status == 200
        assert data["total_reports"] == 0
        assert data["corridors"] == []
