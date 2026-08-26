"""Tests for web/server.py API endpoints (geocode, plan, fare).

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
