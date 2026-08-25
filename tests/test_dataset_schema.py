import pytest

from phfares.core import _load_fares_data


class TestDatasetSchema:
    def test_fares_json_parses(self):
        data = _load_fares_data()
        assert "modes" in data

    def test_currency_php(self):
        data = _load_fares_data()
        assert data["currency"] == "PHP"

    def test_modes_have_source(self):
        data = _load_fares_data()
        for mode, entry in data["modes"].items():
            assert entry.get("source"), f"Mode '{mode}' missing source"

    def test_modes_have_as_of(self):
        data = _load_fares_data()
        for mode, entry in data["modes"].items():
            assert entry.get("as_of"), f"Mode '{mode}' missing as_of"

    def test_modes_have_status(self):
        data = _load_fares_data()
        for mode, entry in data["modes"].items():
            assert entry.get("status"), f"Mode '{mode}' missing status"

    def test_verified_entries_have_source(self):
        data = _load_fares_data()
        for mode, entry in data["modes"].items():
            if entry.get("status") == "verified":
                assert entry.get("source"), (
                    f"Verified mode '{mode}' missing source"
                )

    def test_bbox_has_four_numbers(self):
        data = _load_fares_data()
        bbox = data["region"]["bbox"]
        assert len(bbox) == 4
        assert all(isinstance(n, (int, float)) for n in bbox)
