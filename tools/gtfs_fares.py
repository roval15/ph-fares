#!/usr/bin/env python3
"""Generate GTFS Fares v0 artifacts for Mandaluyong routes.

Emits ``dist/fare_attributes.txt`` and ``dist/fare_rules.txt`` following
**GTFS Fares v0** semantics (``fare_attributes.txt`` + ``fare_rules.txt``
as defined in the GTFS specification prior to the Fares v1 proposal).

Version note
------------
GTFS v0 fares are **flat per route** — each fare_attributes row carries a
single price.  The real jeepney fare structure is distance-based:

* Traditional jeepney (modes.jeepney_traditional): PHP 12.00 base for the
  first 4 km, then PHP 1.80 per additional km.
* Modern jeepney (modes.jeepney_modern): PHP 14.00 base for the first
  4 km, then PHP 2.20 per additional km.

The flat prices emitted here equal the *base_fare* from the dataset.
Consumers that need exact distance-based fares should apply the formula
from ``data/fares.json`` rather than relying on the v0 flat price.

Route–mode mapping
------------------
Routes are classified by ``route_id`` prefix:

* ``LTFRB_PUJ*`` — traditional jeepney routes.  Fare attribute derives
  from ``modes.jeepney_traditional`` (base_fare = 12.00).
* ``LTFRB_PUB*`` — LTFRB city-bus routes serving Mandaluyong.  These
  also receive a fare rule priced at the jeepney_traditional base_fare
  because city-bus fares are **unverified** for this pilot (SPEC.md marks
  city bus "unverified / exclude if no source") and the orchestrator
  requires one rule per route.

Every route in ``data/routes_mandaluyong.json`` receives exactly one
``fare_rules`` row; the set covers "jeepney + LTFRB city bus routes
serving Mandaluyong".

CLI
---
::

    python3 tools/gtfs_fares.py

Exits 0 on success.  Prints a summary to stdout.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — resolve relative to the repository root (parent of tools/)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FARES_JSON = _REPO_ROOT / "data" / "fares.json"
_ROUTES_JSON = _REPO_ROOT / "data" / "routes_mandaluyong.json"
_DIST_DIR = _REPO_ROOT / "dist"

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | list:
    """Load a JSON file or exit with a clear error."""
    if not path.exists():
        print(f"Error: input file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ---- load inputs -------------------------------------------------------
    fares = _load_json(_FARES_JSON)
    routes = _load_json(_ROUTES_JSON)

    if not isinstance(routes, list):
        print(f"Error: { _ROUTES_JSON } is not a JSON list", file=sys.stderr)
        sys.exit(2)

    currency = fares.get("currency", "PHP")
    modes = fares.get("modes", {})

    try:
        puj_base = modes["jeepney_traditional"]["base_fare"]
    except (KeyError, TypeError) as exc:
        print(
            f"Error: cannot read jeepney_traditional.base_fare from {_FARES_JSON}: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    # ---- fare attributes (two rows) ----------------------------------------
    fare_attributes = [
        {
            "fare_id": "puj_traditional_base",
            "price": f"{puj_base:.2f}",
            "currency_type": currency,
            "payment_method": 0,
            "transfers": 0,
        },
        {
            "fare_id": "pub_citybus_unverified_uses_puj_base",
            "price": f"{puj_base:.2f}",
            "currency_type": currency,
            "payment_method": 0,
            "transfers": 0,
        },
    ]

    # ---- fare rules (one per route) ----------------------------------------
    fare_rules = []
    for route in sorted(routes, key=lambda r: r["route_id"]):
        route_id = route["route_id"]
        if route_id.startswith("LTFRB_PUB"):
            fare_id = "pub_citybus_unverified_uses_puj_base"
        else:
            # PUJ and anything else → traditional jeepney base fare
            fare_id = "puj_traditional_base"
        fare_rules.append({"fare_id": fare_id, "route_id": route_id})

    fare_rules.sort(key=lambda r: r["route_id"])

    # ---- write outputs -----------------------------------------------------
    _DIST_DIR.mkdir(parents=True, exist_ok=True)

    _write_csv(
        _DIST_DIR / "fare_attributes.txt",
        ["fare_id", "price", "currency_type", "payment_method", "transfers"],
        fare_attributes,
    )

    _write_csv(
        _DIST_DIR / "fare_rules.txt",
        ["fare_id", "route_id"],
        fare_rules,
    )

    readme = (
        "# GTFS Fares v0 — Mandaluyong Routes\n\n"
        "This directory contains GTFS **Fares v0** artifacts generated from "
        "`data/fares.json` and `data/routes_mandaluyong.json`.\n\n"
        "## Files\n\n"
        "| File | Description |\n"
        "|------|-------------|\n"
        "| `fare_attributes.txt` | Two fare attributes: "
        "`puj_traditional_base` (jeepney traditional) and "
        "`pub_citybus_unverified_uses_puj_base` (city bus, unverified) |\n"
        "| `fare_rules.txt` | One rule per route (334 total) mapping each "
        "route to its fare attribute |\n\n"
        "## GTFS version\n\n"
        "Generated **GTFS Fares v0** (`fare_attributes.txt` + "
        "`fare_rules.txt`). This is the flat-fare schema from the original "
        "GTFS specification, predating Fares v1.\n\n"
        "## Fare derivation\n\n"
        "| fare_id | Dataset mode | base_fare |\n"
        "|---------|-------------|-----------|\n"
        f"| `puj_traditional_base` | `modes.jeepney_traditional` | "
        f"{puj_base:.2f} {currency} |\n"
        f"| `pub_citybus_unverified_uses_puj_base` | "
        f"`modes.jeepney_traditional` (unverified city bus fallback) | "
        f"{puj_base:.2f} {currency} |\n\n"
        "### Route–mode mapping\n\n"
        "* `LTFRB_PUJ*` routes → `puj_traditional_base`\n"
        "* `LTFRB_PUB*` routes → `pub_citybus_unverified_uses_puj_base`\n\n"
        "The set covers **jeepney + LTFRB city bus routes** serving "
        "Mandaluyong.\n\n"
        "## Distance-formula limitation\n\n"
        "GTFS v0 fares are **flat per route**.  The real fare structure is "
        "distance-based:\n\n"
        "* Traditional jeepney: PHP 12.00 base (first 4 km), then "
        "PHP 1.80 per additional km.\n"
        "* Modern jeepney: PHP 14.00 base (first 4 km), then "
        "PHP 2.20 per additional km.\n\n"
        "The prices in `fare_attributes.txt` equal the *base_fare* only.  "
        "Consumers needing exact distance-based fares should read the "
        "formula parameters from `data/fares.json` instead.\n\n"
        "City-bus fares are **unverified** for this pilot; they reuse the "
        "jeepney traditional base_fare as a placeholder.\n"
    )
    (_DIST_DIR / "README.md").write_text(readme, encoding="utf-8")

    # ---- summary -----------------------------------------------------------
    puj_count = sum(
        1 for r in fare_rules if r["fare_id"] == "puj_traditional_base"
    )
    pub_count = len(fare_rules) - puj_count
    print(f"GTFS Fares v0 artifacts written to {_DIST_DIR}/")
    print(f"  fare_attributes.txt : {len(fare_attributes)} rows")
    print(f"  fare_rules.txt      : {len(fare_rules)} rows "
          f"({puj_count} PUJ + {pub_count} PUB)")
    print(f"  README.md")


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
