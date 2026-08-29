"""Tests for the flood-risk backend: vendored grid, fold, weather, endpoint.

No network in tests: weather calls use an injected fake ``urlopen`` and the
endpoint exercises the handler through the same fake-socket pattern as
tests/test_web_api.py.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

import phfares.flood as flood_mod
from phfares.flood import (
    RULES,
    assess,
    fetch_weather,
    haversine_m,
    load_grid,
    route_exposure,
    sample_polyline,
)

_MANILA = timezone(timedelta(hours=8))

# Oracle regression fixture: (lon, lat, expected Var) from NOAH's live
# production service on 2026-08-29. Every point must match the built grid.
ORACLE = [
    (121.018, 14.553, 1), (121.018, 14.57, 1), (121.018, 14.587, 3), (121.018, 14.604, 0),
    (121.029, 14.553, 0), (121.029, 14.57, 0), (121.029, 14.587, 0), (121.029, 14.604, 0),
    (121.04, 14.553, 0), (121.04, 14.57, 1), (121.04, 14.587, 3), (121.04, 14.604, 0),
    (121.051, 14.553, 0), (121.051, 14.57, 2), (121.051, 14.587, 0), (121.051, 14.604, 0),
    (121.062, 14.553, 0), (121.062, 14.57, 0), (121.062, 14.587, 0), (121.062, 14.604, 0),
    (121.0490646, 14.5736071, 0), (121.0568831, 14.5847281, 0), (121.033046, 14.5908641, 0),
    (121.0416642, 14.575458, 0), (121.067134, 14.5447555, 0),
]

_NO_RAIN = {"available": True, "rain_max_6h_mm": 0.0, "rain_next_24h_mm": 0.0}


@pytest.fixture(autouse=True)
def _clear_weather_cache():
    flood_mod._WEATHER_CACHE.clear()
    yield
    flood_mod._WEATHER_CACHE.clear()


def _as_of(thour: int = 10) -> datetime:
    return datetime(2026, 8, 29, thour, 0, 0, tzinfo=_MANILA)


def _payload(thour: int = 10) -> dict:
    times = [f"2026-08-29T{h:02d}:00" for h in range(24)]
    precip = [1.0] * 24
    precip[thour] = 40.0
    return {"hourly": {"time": times, "precipitation": precip}}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# Grid / oracle
# ---------------------------------------------------------------------------

class TestGridOracle:
    def test_grid_loads_and_lookup(self):
        g = load_grid()
        assert g.susceptibility(14.577439, 121.033897) == 2  # AC-2 point

    def test_out_of_bbox_returns_zero(self, tmp_path):
        meta = {
            "bbox": [120.90, 14.35, 121.14, 14.79],
            "cell_deg": [0.0002, 0.00018],
            "shape": [1, 1],
        }
        g = flood_mod.FloodGrid(bytes([3]), meta)
        assert g.susceptibility(14.577439, 121.033897) == 3
        assert g.susceptibility(20.0, 121.0) == 0
        assert g.susceptibility(14.5, 130.0) == 0

    def test_oracle_25_of_25(self):
        g = load_grid()
        mismatches = [
            (lon, lat, expected, g.susceptibility(lat, lon))
            for lon, lat, expected in ORACLE
            if g.susceptibility(lat, lon) != expected
        ]
        assert mismatches == [], (
            f"oracle mismatch ({len(mismatches)}/25): {mismatches}"
        )


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

class TestGeometry:
    def test_haversine_known_distance(self):
        a = (14.60, 121.00)
        b = (14.60, 121.01)
        d = haversine_m(a, b)
        assert 1000 < d < 1250  # ~1.1 km per 0.01 deg lon at this latitude

    def test_sample_polyline_deterministic_and_bounded(self):
        pts = [(14.60, 121.00), (14.62, 121.00), (14.62, 121.03)]
        s1 = sample_polyline(pts, step_m=50.0)
        s2 = sample_polyline(pts, step_m=50.0)
        assert s1 == s2
        assert s1[0] == pts[0] and s1[-1] == pts[-1]
        spans = [haversine_m(a, b) for a, b in zip(s1, s1[1:])]
        assert all(d <= 100.0 for d in spans)  # ~2x step tolerance


# ---------------------------------------------------------------------------
# assess() — pure escalation logic
# ---------------------------------------------------------------------------

class TestAssess:
    def test_returns_expected_shape(self):
        result = assess({0: 10, 2: 5}, _NO_RAIN)
        assert set(result) == {"level", "verdict", "reasons", "pct_in_zone"}

    def test_base_from_exposure(self):
        result = assess({0: 10, 2: 5}, _NO_RAIN)
        assert result["level"] == 2
        assert result["verdict"] == "MEDIUM"
        assert result["pct_in_zone"] == pytest.approx(5 / 15)

    def test_base_zero_outside_zones(self):
        result = assess({0: 20}, _NO_RAIN)
        assert result["level"] == 0
        assert result["verdict"] == "NONE"
        assert result["pct_in_zone"] == 0.0
        assert any("outside NOAH mapped flood zones" in r for r in result["reasons"])

    def test_heavy_rain_escalates(self):
        w = {"available": True, "rain_max_6h_mm": 40.0, "rain_next_24h_mm": 5.0}
        result = assess({0: 10, 2: 5}, w)
        assert result["level"] == 3
        assert result["verdict"] == "HIGH"
        assert any("heavy rain now" in r for r in result["reasons"])

    def test_storm_24h_escalates(self):
        w = {"available": True, "rain_max_6h_mm": 5.0, "rain_next_24h_mm": 120.0}
        result = assess({0: 10, 2: 5}, w)
        assert result["level"] == 3
        assert any("storm-scale rain forecast" in r for r in result["reasons"])

    def test_both_triggers_cap_at_3(self):
        w = {"available": True, "rain_max_6h_mm": 50.0, "rain_next_24h_mm": 150.0}
        result = assess({0: 10, 3: 5}, w, rules=RULES)
        assert result["level"] == 3
        assert result["verdict"] == "HIGH"

    def test_cap_at_3_from_base_2_with_two_triggers(self):
        w = {"available": True, "rain_max_6h_mm": 50.0, "rain_next_24h_mm": 150.0}
        result = assess({0: 10, 2: 5}, w, rules=RULES)
        assert result["level"] == 3  # 2 -> 3 -> capped at 3

    def test_no_escalation_when_base_zero(self):
        w = {"available": True, "rain_max_6h_mm": 50.0, "rain_next_24h_mm": 150.0}
        result = assess({0: 20}, w)
        assert result["level"] == 0
        assert not any("heavy rain now" in r for r in result["reasons"])

    def test_moderate_rain_noted_without_escalation(self):
        w = {"available": True, "rain_max_6h_mm": 20.0, "rain_next_24h_mm": 5.0}
        result = assess({0: 10, 2: 5}, w)
        assert result["level"] == 2
        assert any("moderate rain" in r for r in result["reasons"])
        assert not any("heavy rain" in r for r in result["reasons"])

    def test_determinism_same_input(self):
        args = ({0: 3, 2: 7},
                {"available": True, "rain_max_6h_mm": 42.0, "rain_next_24h_mm": 88.0},
                RULES)
        assert assess(*args) == assess(*args)

    def test_degraded_weather_susceptibility_only(self):
        result = assess({0: 3, 2: 7}, {"available": False})
        assert result["level"] == 2
        assert result["verdict"] == "MEDIUM"
        assert any("live rain data unavailable" in r for r in result["reasons"])

    def test_degrated_weather_base_zero(self):
        result = assess({0: 10}, {"available": False})
        assert result["level"] == 0
        assert result["verdict"] == "NONE"


# ---------------------------------------------------------------------------
# fetch_weather() — fake urlopen, no network
# ---------------------------------------------------------------------------

class TestFetchWeather:
    def test_parses_rain_fields(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=20):
            calls["n"] += 1
            return _FakeResp(_payload(thour=10))

        monkeypatch.setattr(flood_mod, "_urlopen", fake_urlopen)
        result = fetch_weather(14.5, 121.0, now=_as_of(10))
        assert result["available"] is True
        assert result["rain_max_6h_mm"] == 40.0
        assert result["rain_next_24h_mm"] == 53.0
        assert result["as_of"] == "2026-08-29T10:00:00+08:00"
        assert calls["n"] == 1

    def test_degraded_on_network_failure(self, monkeypatch):
        def boom(req, timeout=20):
            raise urllib.error.URLError("no network")

        monkeypatch.setattr(flood_mod, "_urlopen", boom)
        assert fetch_weather(14.5, 121.0, now=_as_of(10)) == {"available": False}

    def test_degraded_on_bad_json(self, monkeypatch):
        def bad(req, timeout=20):
            class R(_FakeResp):
                def read(self):
                    raise OSError("truncated")
            return R({})

        monkeypatch.setattr(flood_mod, "_urlopen", bad)
        assert fetch_weather(14.5, 121.0, now=_as_of(10)) == {"available": False}

    def test_cache_reuses_same_window_and_cell(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=20):
            calls["n"] += 1
            return _FakeResp(_payload(thour=10))

        monkeypatch.setattr(flood_mod, "_urlopen", fake_urlopen)
        r1 = fetch_weather(14.5, 121.0, now=_as_of(10))
        r2 = fetch_weather(14.501, 121.002, now=_as_of(10))  # same 0.02-deg cell
        r3 = fetch_weather(14.5, 121.0, now=_as_of(10))
        assert calls["n"] == 1
        assert r2 is r1 and r3 is r1

    def test_cache_rounds_to_15_minute_window(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=20):
            calls["n"] += 1
            return _FakeResp(_payload(thour=10))

        monkeypatch.setattr(flood_mod, "_urlopen", fake_urlopen)
        fetch_weather(14.5, 121.0, now=_as_of(10))
        fetch_weather(14.5, 121.0, now=_as_of(10).replace(minute=7))
        assert calls["n"] == 1
        fetch_weather(14.5, 121.0, now=_as_of(10).replace(minute=20))
        assert calls["n"] == 2

    def test_cache_distinguishes_cells(self, monkeypatch):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=20):
            calls["n"] += 1
            return _FakeResp(_payload(thour=10))

        monkeypatch.setattr(flood_mod, "_urlopen", fake_urlopen)
        fetch_weather(14.5, 121.0, now=_as_of(10))
        fetch_weather(14.7, 121.0, now=_as_of(10))
        assert calls["n"] == 2

    def test_url_and_timeout_are_sane(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=20):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["ua"] = req.get_header("User-agent")
            return _FakeResp(_payload(thour=10))

        monkeypatch.setattr(flood_mod, "_urlopen", fake_urlopen)
        fetch_weather(14.5, 121.0, now=_as_of(10))
        assert "https://api.open-meteo.com/v1/forecast" in captured["url"]
        assert "latitude=14.5" in captured["url"]
        assert "timezone=Asia%2FManila" in captured["url"]
        assert captured["ua"] == "ph-fares/0.2"
        assert captured["timeout"] == 20


# ---------------------------------------------------------------------------
# Endpoint contract — fake handler, mocked planner + weather
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
    body = handler.wfile.read().decode("utf-8")
    return captured["status"], json.loads(body)


def _ok_plan():
    return {"status": "ok", "options": [
        {
            "legs": [
                {"type": "walk", "from": {"lat": 1, "lon": 1}, "to": {"lat": 2, "lon": 2}, "distance_m": 100},
                {"type": "ride", "mode": "jeepney_traditional", "route_id": "R1", "route_long_name": "Shaw-Ortigas",
                 "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                 "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3}, "distance_km": 2.5},
                {"type": "walk", "from": {"lat": 3, "lon": 3}, "to": {"lat": 4, "lon": 4}, "distance_m": 100},
            ],
            "fare_breakdown": {"jeepney_traditional": 15.0},
            "total_fare": 15.0,
            "notes": [],
        },
        {
            "legs": [
                {"type": "walk", "from": {"lat": 1, "lon": 1}, "to": {"lat": 2, "lon": 2}, "distance_m": 100},
                {"type": "ride", "mode": "jeepney_modern", "route_id": "R2", "route_long_name": "Shaw-Ortigas Modern",
                 "board_stop": {"stop_id": "S1", "stop_name": "Stop A", "lat": 2, "lon": 2},
                 "alight_stop": {"stop_id": "S2", "stop_name": "Stop B", "lat": 3, "lon": 3}, "distance_km": 2.5},
            ],
            "fare_breakdown": {"jeepney_modern": 18.0},
            "total_fare": 18.0,
            "notes": [],
        },
    ]}


class TestFloodRiskEndpoint:
    URL = ("/api/flood-risk?from_lat=14.5736071&from_lon=121.0490646"
           "&to_lat=14.5847281&to_lon=121.0568831")

    def test_missing_params(self):
        status, data = _get_response("/api/flood-risk?from_lat=1")
        assert status == 400
        assert data["status"] == "error"

    def test_ok_shape(self, monkeypatch):
        monkeypatch.setattr("web.server.guide_planner.plan", lambda *a, **kw: _ok_plan())
        monkeypatch.setattr(
            flood_mod, "fetch_weather",
            lambda lat, lon, now=None: {
                "available": True, "rain_max_6h_mm": 3.0,
                "rain_next_24h_mm": 8.0, "as_of": "2026-08-29T10:00:00+08:00",
            },
        )
        status, data = _get_response(self.URL)
        assert status == 200
        assert data["status"] == "ok"
        assert data["as_of"] == "2026-08-29T10:00:00+08:00"
        assert data["rules_version"] == "0.2"
        assert data["attribution"] == (
            "Flood data: Project NOAH (UP Resilience Institute), ODbL · Weather: Open-Meteo"
        )
        assert data["weather"]["available"] is True
        assert len(data["options"]) == 2
        for opt in data["options"]:
            assert {"fare", "modes", "exposure", "samples", "verdict",
                    "level", "reasons", "pct_in_zone"} <= set(opt)
            assert opt["modes"]  # non-empty modes list
            assert isinstance(opt["samples"], int) and opt["samples"] >= 1
            assert sum(opt["exposure"].values()) == opt["samples"]
            assert 0 <= opt["level"] <= 3
            assert isinstance(opt["reasons"], list)
        assert data["options"][0]["fare"] == 15.0
        assert data["options"][0]["modes"] == ["jeepney_traditional"]

    def test_caps_five_options(self, monkeypatch):
        plan = {"status": "ok", "options": [
            {"legs": [], "fare_breakdown": {}, "total_fare": 1.0, "notes": []} for _ in range(9)
        ]}
        monkeypatch.setattr("web.server.guide_planner.plan", lambda *a, **kw: plan)
        monkeypatch.setattr(flood_mod, "fetch_weather", lambda *a, **kw: _NO_RAIN)
        status, data = _get_response(self.URL)
        assert status == 200
        assert len(data["options"]) == 5

    def test_no_route_shape(self, monkeypatch):
        monkeypatch.setattr("web.server.guide_planner.plan", lambda *a, **kw: {
            "status": "no_route",
            "message": "No direct route found between the given points.",
            "from": {"lat": 1, "lon": 1},
            "to": {"lat": 2, "lon": 2},
        })
        calls = {"n": 0}
        def _wf(lat, lon, now=None):
            calls["n"] += 1
            return _NO_RAIN
        monkeypatch.setattr(flood_mod, "fetch_weather", _wf)
        status, data = _get_response(self.URL)
        assert status == 200
        assert data["status"] == "no_route"
        assert "message" in data
        assert calls["n"] == 0

    def test_planner_error_does_not_crash(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr("web.server.guide_planner.plan", boom)
        status, data = _get_response(self.URL)
        assert status == 500
        assert data["status"] == "error"

    def test_weather_unavailable_still_ok(self, monkeypatch):
        monkeypatch.setattr("web.server.guide_planner.plan", lambda *a, **kw: _ok_plan())
        monkeypatch.setattr(flood_mod, "fetch_weather", lambda *a, **kw: {"available": False})
        status, data = _get_response(self.URL)
        assert status == 200
        assert data["status"] == "ok"
        assert data["weather"] == {"available": False}
        assert any(
            "live rain data unavailable" in r
            for opt in data["options"] for r in opt["reasons"]
        )


# ---------------------------------------------------------------------------
# Latency gate (AC-6): exposure + assess for SM Light -> Megamall plan
# ---------------------------------------------------------------------------

class TestFloodLatency:
    def test_exposure_assess_under_100ms(self):
        import guide.planner as planner_mod
        plan = planner_mod.plan(14.5736071, 121.0490646, 14.5847281, 121.0568831)
        assert plan["status"] == "ok"
        legs = plan["options"][0]["legs"]
        grid = load_grid()
        # Warm on-disk data loads so we time the pure fold, not IO/parsing.
        flood_mod._stops_by_route()
        t0 = time.perf_counter()
        exposure = route_exposure(grid, legs)
        verdict = assess(exposure["exposure"], _NO_RAIN)
        elapsed = time.perf_counter() - t0
        assert exposure["samples"] >= 1
        assert 0 <= verdict["level"] <= 3
        assert elapsed < 0.100, f"exposure+assess took {elapsed * 1000:.1f} ms"


# ---------------------------------------------------------------------------
# AC-4: phfares/flood.py must import stdlib only
# ---------------------------------------------------------------------------

class TestStdlibOnly:
    def test_flood_module_imports_only_stdlib(self):
        import ast
        import sys
        from pathlib import Path

        tree = ast.parse(Path("phfares/flood.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    imported.add(node.module)
        top_level = {name.split(".")[0] for name in imported}
        assert top_level, "no imports found in phfares/flood.py"
        non_stdlib = top_level - set(sys.stdlib_module_names)
        assert not non_stdlib, f"non-stdlib imports in phfares/flood.py: {sorted(non_stdlib)}"