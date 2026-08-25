from decimal import Decimal

import pytest

from phfares import fare
from phfares.core import _load_fares_data


class TestJeepneyTraditional:
    def test_km_below_base(self):
        result = fare("jeepney_traditional", km=0.5)
        assert result == Decimal("12.00")

    def test_km_equals_base(self):
        result = fare("jeepney_traditional", km=4)
        assert result == Decimal("12.00")
        assert result == 12.0

    def test_km_above_base(self):
        result = fare("jeepney_traditional", km=7.5)
        assert result == Decimal("18.30")

    def test_return_type_is_decimal(self):
        result = fare("jeepney_traditional", km=4)
        assert isinstance(result, Decimal)


class TestJeepneyModern:
    def test_km_equals_base(self):
        result = fare("jeepney_modern", km=4)
        assert result == Decimal("14.00")

    def test_km_above_base(self):
        result = fare("jeepney_modern", km=5)
        assert result == Decimal("16.20")


class TestRounding:
    def test_half_up_rounding(self):
        result = fare("jeepney_traditional", km=4.125)
        assert result == Decimal("12.23")


class TestErrors:
    def test_km_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="km must be positive"):
            fare("jeepney_traditional", km=0)

    def test_negative_km_raises_value_error(self):
        with pytest.raises(ValueError, match="km must be positive"):
            fare("jeepney_traditional", km=-1.5)

    def test_unknown_mode_raises_key_error(self):
        with pytest.raises(KeyError, match="helicopter"):
            fare("helicopter", km=5)

    def test_unknown_mode_lists_available_modes(self):
        with pytest.raises(KeyError, match="Available modes"):
            fare("helicopter", km=5)

    def test_station_table_mode_raises_key_error(self):
        with pytest.raises(KeyError, match="station"):
            fare("mrt3", km=5)

    def test_station_table_mode_message(self):
        with pytest.raises(KeyError, match="not distance-based"):
            fare("mrt3", km=5)
