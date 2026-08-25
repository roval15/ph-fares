from pathlib import Path

import pytest

TOOLS_SCRIPT = Path("tools/gtfs_fares.py")
DIST_DIR = Path("dist")


@pytest.mark.skipif(
    not TOOLS_SCRIPT.exists(),
    reason="tools/gtfs_fares.py not implemented yet",
)
class TestGtfsFares:
    def test_script_exits_zero(self):
        import subprocess

        result = subprocess.run(
            ["python3", str(TOOLS_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_csv_has_required_columns(self):
        import csv
        import subprocess

        subprocess.run(
            ["python3", str(TOOLS_SCRIPT)],
            capture_output=True,
            text=True,
        )

        fare_attributes_csv = DIST_DIR / "fare_attributes.txt"
        assert fare_attributes_csv.exists()

        with open(fare_attributes_csv, newline="") as f:
            reader = csv.DictReader(f)
            assert "fare_id" in reader.fieldnames
            assert "price" in reader.fieldnames
            assert "currency_type" in reader.fieldnames
            assert "payment_method" in reader.fieldnames
            assert "transfers" in reader.fieldnames

        fare_rules_csv = DIST_DIR / "fare_rules.txt"
        assert fare_rules_csv.exists()

        with open(fare_rules_csv, newline="") as f:
            reader = csv.DictReader(f)
            assert "fare_id" in reader.fieldnames
            assert "route_id" in reader.fieldnames
            assert len(list(reader)) > 0
