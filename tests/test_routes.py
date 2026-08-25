from pathlib import Path
from unittest.mock import patch

import pytest

from phfares import routes_for, stop_in_bbox
from phfares.core import _FARES_PATH, _ROUTES_PATH


class TestStopInBbox:
    def test_inside_bbox(self):
        bbox = [14.555, 121.025, 14.600, 121.065]
        assert stop_in_bbox(14.57, 121.04, bbox) is True

    def test_outside_bbox(self):
        bbox = [14.555, 121.025, 14.600, 121.065]
        assert stop_in_bbox(14.60, 121.10, bbox) is False

    def test_on_boundary(self):
        bbox = [14.555, 121.025, 14.600, 121.065]
        assert stop_in_bbox(14.555, 121.025, bbox) is True
        assert stop_in_bbox(14.600, 121.065, bbox) is True


class TestRoutesFor:
    def test_case_insensitive_match(self):
        result = routes_for("mandaluyong")
        assert isinstance(result, list)

    def test_capitalized_match(self):
        result = routes_for("Mandaluyong")
        assert isinstance(result, list)

    def test_unknown_city_raises_value_error(self):
        with pytest.raises(ValueError, match="Pilot scope"):
            routes_for("Manila")

    def test_returns_empty_when_routes_file_absent(self, monkeypatch):
        monkeypatch.setattr(
            "phfares.core._ROUTES_PATH",
            Path("/nonexistent/path/routes.json"),
        )
        # Clear lru_cache so the mocked path is picked up
        from phfares.core import _load_fares_data
        _load_fares_data.cache_clear()

        result = routes_for("Mandaluyong")
        assert result == []

        _load_fares_data.cache_clear()


@pytest.mark.skipif(
    not _ROUTES_PATH.exists(),
    reason="data/routes_mandaluyong.json not implemented yet",
)
class TestRoutesFileContent:
    def test_entries_have_required_keys(self):
        from phfares.core import _load_routes
        routes = _load_routes()
        for route in routes:
            assert "route_id" in route
            assert "route_long_name" in route
            assert "n_stops_in_bbox" in route
