"""Tests for guide.geocode — all HTTP mocked, no live network."""

import json
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

geocode_mod = importlib.import_module("guide.geocode")


MOCK_RESPONSE = [
    {
        "display_name": "Shaw Boulevard, Mandaluyong, Philippines",
        "lat": "14.581",
        "lon": "121.054",
    },
    {
        "display_name": "Shaw Center Mall, Mandaluyong, Philippines",
        "lat": "14.582",
        "lon": "121.055",
    },
]

MOCK_RESPONSE_7 = MOCK_RESPONSE * 3 + [
    {
        "display_name": "Extra, Philippines",
        "lat": "14.5",
        "lon": "121.0",
    },
]


def _mock_urlopen_factory(responses: list[dict]):
    """Return a mock urlopen that yields JSON-encoded *responses*."""
    call_count = 0

    def _urlopen(request, timeout=10):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.read.return_value = json.dumps(responses).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    return _urlopen, lambda: call_count


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    """Isolate CACHE_PATH and reset module state between tests."""
    monkeypatch.setattr(geocode_mod, "CACHE_PATH", tmp_path / "geocode_cache.json")
    geocode_mod._cache = None
    geocode_mod._last_request_ts = 0.0
    yield
    geocode_mod._cache = None


class TestCacheHitZeroHttp:
    def test_second_call_skips_http(self, monkeypatch):
        mock_fn, count_fn = _mock_urlopen_factory(MOCK_RESPONSE)
        monkeypatch.setattr(geocode_mod, "_urlopen", mock_fn)

        r1 = geocode_mod.geocode("Shaw Boulevard Mandaluyong")
        assert count_fn() == 1
        assert len(r1) == 2

        r2 = geocode_mod.geocode("Shaw Boulevard Mandaluyong")
        assert count_fn() == 1  # still 1 — no additional HTTP
        assert r1 == r2


class TestUserAgent:
    def test_user_agent_contains_ph_fares(self, monkeypatch):
        captured_requests = []

        def _capture(request, timeout=10):
            captured_requests.append(request)
            resp = MagicMock()
            resp.read.return_value = json.dumps(MOCK_RESPONSE).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(geocode_mod, "_urlopen", _capture)
        geocode_mod.geocode("Test Query")
        assert len(captured_requests) == 1
        ua = captured_requests[0].get_header("User-agent")
        assert "ph-fares" in ua


class TestUrlShape:
    def test_url_contains_required_params(self, monkeypatch):
        captured_requests = []

        def _capture(request, timeout=10):
            captured_requests.append(request)
            resp = MagicMock()
            resp.read.return_value = json.dumps(MOCK_RESPONSE).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(geocode_mod, "_urlopen", _capture)
        geocode_mod.geocode("Shaw Boulevard Mandaluyong")
        url = captured_requests[0].full_url
        assert "q=Shaw+Boulevard+Mandaluyong" in url
        assert "format=jsonv2" in url
        assert "limit=5" in url
        assert "countrycodes=ph" in url


class TestCorruptCacheRecovery:
    def test_corrupt_file_overwrites(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "geocode_cache.json"
        cache_file.write_bytes(b"not valid json {{{")
        monkeypatch.setattr(geocode_mod, "CACHE_PATH", cache_file)

        mock_fn, count_fn = _mock_urlopen_factory(MOCK_RESPONSE)
        monkeypatch.setattr(geocode_mod, "_urlopen", mock_fn)

        result = geocode_mod.geocode("Corrupt Cache Test")
        assert len(result) == 2
        assert count_fn() == 1

        # Cache file should now be valid JSON
        with cache_file.open() as fh:
            data = json.load(fh)
        assert "Corrupt Cache Test" in data


class TestLimit5:
    def test_only_five_returned(self, monkeypatch):
        mock_fn, _ = _mock_urlopen_factory(MOCK_RESPONSE_7)
        monkeypatch.setattr(geocode_mod, "_urlopen", mock_fn)

        result = geocode_mod.geocode("Seven Results Query")
        assert len(result) == 5
