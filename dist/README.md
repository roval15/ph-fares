# GTFS Fares v0 — Mandaluyong Routes

This directory contains GTFS **Fares v0** artifacts generated from `data/fares.json` and `data/routes_mandaluyong.json`.

## Files

| File | Description |
|------|-------------|
| `fare_attributes.txt` | Two fare attributes: `puj_traditional_base` (jeepney traditional) and `pub_citybus_unverified_uses_puj_base` (city bus, unverified) |
| `fare_rules.txt` | One rule per route (334 total) mapping each route to its fare attribute |

## GTFS version

Generated **GTFS Fares v0** (`fare_attributes.txt` + `fare_rules.txt`). This is the flat-fare schema from the original GTFS specification, predating Fares v1.

## Fare derivation

| fare_id | Dataset mode | base_fare |
|---------|-------------|-----------|
| `puj_traditional_base` | `modes.jeepney_traditional` | 12.00 PHP |
| `pub_citybus_unverified_uses_puj_base` | `modes.jeepney_traditional` (unverified city bus fallback) | 12.00 PHP |

### Route–mode mapping

* `LTFRB_PUJ*` routes → `puj_traditional_base`
* `LTFRB_PUB*` routes → `pub_citybus_unverified_uses_puj_base`

The set covers **jeepney + LTFRB city bus routes** serving Mandaluyong.

## Distance-formula limitation

GTFS v0 fares are **flat per route**.  The real fare structure is distance-based:

* Traditional jeepney: PHP 12.00 base (first 4 km), then PHP 1.80 per additional km.
* Modern jeepney: PHP 14.00 base (first 4 km), then PHP 2.20 per additional km.

The prices in `fare_attributes.txt` equal the *base_fare* only.  Consumers needing exact distance-based fares should read the formula parameters from `data/fares.json` instead.

City-bus fares are **unverified** for this pilot; they reuse the jeepney traditional base_fare as a placeholder.
