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


class TestMechanicalAcceptance:
    def test_modern_5km_equals_float_16_20(self):
        assert fare("jeepney_modern", 5) == 16.20

    def test_traditional_75km_equals_float_18_30(self):
        assert fare("jeepney_traditional", 7.5) == 18.30

    def test_traditional_4km_equals_float_12(self):
        assert fare("jeepney_traditional", 4) == 12.0

    def test_still_decimal_and_str(self):
        r = fare("jeepney_modern", 5)
        assert isinstance(r, Decimal) and str(r) == "16.20"

    def test_int_equality(self):
        assert fare("jeepney_modern", 4) == 14
