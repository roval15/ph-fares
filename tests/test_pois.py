"""Tests for guide.pois — all HTTP mocked, no live network."""

import http.client
import json
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pois_mod = importlib.import_module("guide.pois")

# Shaw MRT is at 14.581, 121.054 in mrt3_stations.json
SHAW_LAT, SHAW_LON = 14.581, 121.054


def _mock_urlopen_factory(response_body: dict | None = None, exc: Exception | None = None):
    """Return a mock urlopen and a call-count function.

    If *exc* is given, every call raises that exception.
    If *response_body* is given, it is JSON-encoded and returned.
    """
    call_count = 0

    def _urlopen(request, timeout=10):
        nonlocal call_count
        call_count += 1
        if exc is not None:
            raise exc
        resp = MagicMock()
        resp.read.return_value = json.dumps(response_body).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    return _urlopen, lambda: call_count


def _overpass_response(elements):
    """Wrap elements in a minimal Overpass JSON envelope."""
    return {"elements": elements}


# ---------------------------------------------------------------------------
# Fixture: reset module state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Isolate cache paths and reset module-level state."""
    monkeypatch.setattr(pois_mod, "CACHE_PATH", tmp_path / "poi_cache.json")
    monkeypatch.setattr(pois_mod, "_MRT_PATH", Path(__file__).resolve().parent.parent / "data" / "mrt3_stations.json")
    pois_mod._cache = None
    pois_mod._mrt_stations = None
    pois_mod._last_request_ts = 0.0
    yield
    pois_mod._cache = None
    pois_mod._mrt_stations = None


# ---------------------------------------------------------------------------
# Tests: distance sorting
# ---------------------------------------------------------------------------

class TestDistanceSorting:
    def test_results_sorted_by_distance(self, monkeypatch):
        """MRT stations come back sorted nearest-first."""
        mock_fn, _ = _mock_urlopen_factory(_overpass_response([]))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=5000, limit=20)
        distances = [r["distance_m"] for r in results]
        assert distances == sorted(distances)


# ---------------------------------------------------------------------------
# Tests: MRT station inclusion (offline path)
# ---------------------------------------------------------------------------

class TestMrtOffline:
    def test_shaw_mrt_included_at_zero_distance(self, monkeypatch):
        """Shaw MRT at 14.581/121.054 should appear at ~0 m."""
        mock_fn, _ = _mock_urlopen_factory(_overpass_response([]))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        names = [r["name"] for r in results]
        assert "Shaw MRT" in names
        shaw = next(r for r in results if r["name"] == "Shaw MRT")
        assert shaw["distance_m"] == 0
        assert shaw["source"] == "mrt"
        assert shaw["category"] == "station"

    def test_no_network_for_mrt(self, monkeypatch):
        """MRT lookup should work even if Overpass is configured to error."""
        mock_fn, _ = _mock_urlopen_factory(exc=ConnectionError("no network"))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1
        names = [r["name"] for r in mrt]
        assert "Shaw MRT" in names


# ---------------------------------------------------------------------------
# Tests: mocked Overpass merge + sort
# ---------------------------------------------------------------------------

class TestOverpassMergeSort:
    def test_mrt_and_overpass_merged(self, monkeypatch):
        overpass_elements = [
            {
                "type": "node",
                "id": 1,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON + 0.001,
                "tags": {"name": "Test Cafe", "amenity": "cafe"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        sources = {r["source"] for r in results}
        assert "mrt" in sources
        assert "overpass" in sources
        # Check sorting
        distances = [r["distance_m"] for r in results]
        assert distances == sorted(distances)

    def test_overpass_category_from_primary_tag(self, monkeypatch):
        overpass_elements = [
            {
                "type": "node",
                "id": 2,
                "lat": SHAW_LAT + 0.0005,
                "lon": SHAW_LON,
                "tags": {"name": "Test Mall", "shop": "mall"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        mall = next(r for r in results if r["name"] == "Test Mall")
        assert mall["category"] == "mall"
        assert mall["source"] == "overpass"

    def test_limit_respected(self, monkeypatch):
        mock_fn, _ = _mock_urlopen_factory(_overpass_response([]))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=5000, limit=2)
        assert len(results) <= 2


# ---------------------------------------------------------------------------
# Tests: mirror failover
# ---------------------------------------------------------------------------

class TestMirrorFailover:
    def test_first_mirror_fails_second_succeeds(self, monkeypatch):
        """When the first mirror raises, the second mirror is tried and succeeds."""
        overpass_elements = [
            {
                "type": "node",
                "id": 10,
                "lat": SHAW_LAT + 0.002,
                "lon": SHAW_LON,
                "tags": {"name": "Failover Shop", "shop": "convenience"},
            },
        ]
        call_count = 0

        def _two_mirror_urlopen(request, timeout=10):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("mirror 1 down")
            resp = MagicMock()
            resp.read.return_value = json.dumps(_overpass_response(overpass_elements)).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pois_mod, "_urlopen", _two_mirror_urlopen)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test", "https://mirror2.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        names = [r["name"] for r in results]
        assert "Failover Shop" in names
        assert call_count == 2

    def test_both_mirrors_fail_degradation(self, monkeypatch):
        """When both mirrors fail, return offline MRT results only, no exception."""
        mock_fn, _ = _mock_urlopen_factory(exc=OSError("all mirrors down"))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test", "https://mirror2.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        # Should not raise — just return MRT results
        assert isinstance(results, list)
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1


# ---------------------------------------------------------------------------
# Tests: name-tag filtering
# ---------------------------------------------------------------------------

class TestNameTagFiltering:
    def test_unnamed_elements_dropped(self, monkeypatch):
        overpass_elements = [
            {
                "type": "node",
                "id": 20,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON,
                "tags": {"amenity": "bench"},  # no name tag
            },
            {
                "type": "node",
                "id": 21,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON + 0.001,
                "tags": {"name": "Named Cafe", "amenity": "cafe"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        overpass_names = [r["name"] for r in results if r["source"] == "overpass"]
        assert "Named Cafe" in overpass_names
        # The unnamed bench should not appear with source=overpass
        for r in results:
            if r["source"] == "overpass":
                assert r["name"]  # must be non-empty

    def test_whitespace_only_name_dropped(self, monkeypatch):
        overpass_elements = [
            {
                "type": "node",
                "id": 30,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON,
                "tags": {"name": "   ", "amenity": "bench"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        overpass = [r for r in results if r["source"] == "overpass"]
        assert len(overpass) == 0

    def test_way_elements_use_center_coords(self, monkeypatch):
        overpass_elements = [
            {
                "type": "way",
                "id": 40,
                "center": {"lat": SHAW_LAT + 0.001, "lon": SHAW_LON + 0.001},
                "tags": {"name": "Test Building", "building": "commercial"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        bldg = next((r for r in results if r["name"] == "Test Building"), None)
        assert bldg is not None
        assert bldg["category"] == "commercial"
        assert bldg["source"] == "overpass"


# ---------------------------------------------------------------------------
# Tests: cache
# ---------------------------------------------------------------------------

class TestCacheZeroHttp:
    def test_second_identical_call_makes_zero_urlopen(self, monkeypatch):
        overpass_elements = [
            {
                "type": "node",
                "id": 50,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON + 0.001,
                "tags": {"name": "Cached Cafe", "amenity": "cafe"},
            },
        ]
        mock_fn, count_fn = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        r1 = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        assert count_fn() == 1

        r2 = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        assert count_fn() == 1  # still 1 — no additional HTTP
        assert r1 == r2


# ---------------------------------------------------------------------------
# Tests: cache poisoning prevention (E9/S9.1)
# ---------------------------------------------------------------------------

class TestCachePoisoning:
    def test_failed_fetch_not_cached(self, monkeypatch):
        """Total Overpass failure must NOT write to cache."""
        mock_fn, _ = _mock_urlopen_factory(exc=OSError("all mirrors down"))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test", "https://mirror2.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        # MRT results still returned
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1
        # Cache key must NOT be present
        key = pois_mod._cache_key(SHAW_LAT, SHAW_LON, 250)
        assert key not in pois_mod._load_cache()

    def test_success_but_empty_is_cached(self, monkeypatch):
        """A legitimate empty Overpass response should be cached."""
        mock_fn, _ = _mock_urlopen_factory(_overpass_response([]))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        key = pois_mod._cache_key(SHAW_LAT, SHAW_LON, 250)
        cache = pois_mod._load_cache()
        assert key in cache
        assert cache[key] == []


# ---------------------------------------------------------------------------
# Tests: graceful degradation — various Overpass failures
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_json_decode_error_returns_offline(self, monkeypatch):
        call_count = 0

        def _bad_json_urlopen(request, timeout=10):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.read.return_value = b"not json"
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pois_mod, "_urlopen", _bad_json_urlopen)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test", "https://mirror2.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        assert isinstance(results, list)
        # MRT results should still be present
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1

    def test_empty_overpass_elements(self, monkeypatch):
        mock_fn, _ = _mock_urlopen_factory(_overpass_response([]))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        # MRT results should still be present
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1


# ---------------------------------------------------------------------------
# Tests: User-Agent header
# ---------------------------------------------------------------------------

class TestUserAgent:
    def test_user_agent_contains_ph_fares(self, monkeypatch):
        captured = []

        def _capture(request, timeout=10):
            captured.append(request)
            resp = MagicMock()
            resp.read.return_value = json.dumps(_overpass_response([])).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pois_mod, "_urlopen", _capture)
        pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=250, limit=5)
        assert len(captured) >= 1
        ua = captured[0].get_header("User-agent")
        assert "ph-fares" in ua


# ---------------------------------------------------------------------------
# Regression tests: never-raise hardening (E9/S9.1)
# ---------------------------------------------------------------------------

class TestNeverRaiseRegression:
    def test_bad_status_line_first_mirror_second_succeeds(self, monkeypatch):
        """First mirror raises BadStatusLine, second succeeds → merged results."""
        overpass_elements = [
            {
                "type": "node",
                "id": 99,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON,
                "tags": {"name": "Regress Cafe", "amenity": "cafe"},
            },
        ]
        call_count = 0

        def _bad_status_urlopen(request, timeout=10):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise http.client.BadStatusLine("\\n")
            resp = MagicMock()
            resp.read.return_value = json.dumps(_overpass_response(overpass_elements)).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pois_mod, "_urlopen", _bad_status_urlopen)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test", "https://mirror2.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        names = [r["name"] for r in results]
        assert "Regress Cafe" in names
        assert call_count == 2

    def test_overpass_returns_list_root_degrades(self, monkeypatch):
        """Mirror returns a JSON list as root → degrade to offline results, no exception."""

        def _list_root_urlopen(request, timeout=10):
            resp = MagicMock()
            resp.read.return_value = json.dumps([1, 2, 3]).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pois_mod, "_urlopen", _list_root_urlopen)
        monkeypatch.setattr(pois_mod, "OVERPASS_MIRRORS", ["https://mirror1.test"])

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        assert isinstance(results, list)
        mrt = [r for r in results if r["source"] == "mrt"]
        assert len(mrt) >= 1

    def test_overpass_element_with_null_tags_skipped(self, monkeypatch):
        """Element with tags=null is silently skipped, no exception."""
        overpass_elements = [
            {
                "type": "node",
                "id": 77,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON,
                "tags": None,
            },
            {
                "type": "node",
                "id": 78,
                "lat": SHAW_LAT + 0.001,
                "lon": SHAW_LON + 0.001,
                "tags": {"name": "Good Cafe", "amenity": "cafe"},
            },
        ]
        mock_fn, _ = _mock_urlopen_factory(_overpass_response(overpass_elements))
        monkeypatch.setattr(pois_mod, "_urlopen", mock_fn)

        results = pois_mod.nearby_pois(SHAW_LAT, SHAW_LON, radius_m=500, limit=10)
        overpass_names = [r["name"] for r in results if r["source"] == "overpass"]
        assert "Good Cafe" in overpass_names

    def test_malformed_mrt_station_skipped(self, monkeypatch):
        """A station dict missing 'lat' does not crash _mrt_nearby."""
        original_stations = pois_mod._load_mrt_stations()
        malformed = {"stop_name": "Bad Station", "lon": 121.054}  # missing lat
        good = {"stop_name": "Good Station", "lat": 14.581, "lon": 121.054}
        pois_mod._mrt_stations = [malformed, good] + original_stations

        results = pois_mod._mrt_nearby(SHAW_LAT, SHAW_LON, radius_m=250)
        names = [r["name"] for r in results]
        assert "Good Station" in names
        assert "Bad Station" not in names
