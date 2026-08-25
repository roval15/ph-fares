# ph-fares

Open-source Philippine public-transit fare dataset and tiny Python library,
piloting in **Mandaluyong City**.

## Pilot scope

This project encodes LTFRB-regulated fare structures as machine-readable
data and exposes them through a small Python library. The pilot covers
Mandaluyong City jeepney routes and MRT-3 (Shaw Blvd & Boni Ave stations).

### Data availability

| Mode | Status | Structure |
|---|---|---|
| Traditional jeepney | verified (LTFRB formula, multiple independent recent encodings) | PHP 12 base for first 4 km, PHP 1.80 per km after |
| Modern jeepney (PUJ mod) | verified (same provenance) | PHP 14 base for first 4 km, PHP 2.20 per km after |
| City bus | LTFRB formula exists but unverified for pilot — included in GTFS output only as an unverified placeholder priced at the PUJ base | distance-based |
| MRT-3 (Shaw Blvd & Boni Ave stations) | verified station-table bands (PHP 13/16/20/24/28) with source + as_of recorded | station-to-station table |
| Angkas / Move It / Grab | excluded — closed apps, no published fares | future crowd-sourced tier |

Every mode entry in `data/fares.json` carries `source` (URL or
`"LTFRB MC <number>"`), `as_of` (date), and `status` (`verified` or
`unverified`).

## Install and use

Clone the repo, then from the repo root:

```python
from phfares import fare, routes_for

fare("jeepney_traditional", km=7.5)  # -> 18.30
```

That is PHP 12 base for the first 4 km + 3.5 km × PHP 1.80.

```python
routes_for("Mandaluyong")  # -> list of jeepney routes serving the pilot bbox
```

Requires **Python 3.13+**. The library is stdlib-only — no third-party
dependencies.

Fares round half-up to 2 decimals. `km <= 0` raises `ValueError`. An unknown
mode raises `KeyError` listing available modes.

## Dataset structure

`data/fares.json` is the canonical dataset. Its top-level fields:

- `schema_version` — integer, currently `1`.
- `currency` — `"PHP"`.
- `as_of` — date the dataset was last updated.
- `region` — pilot bounding box (`bbox`).
- `modes` — keyed by mode slug (e.g. `jeepney_traditional`, `jeepney_modern`,
  `mrt3`). Every mode entry **must** carry `source`, `as_of`, and `status`.

Distance-based modes (`jeepney_traditional`, `jeepney_modern`) use
`base_fare`, `base_km`, and `per_km`. MRT-3 uses a `station_bands` table.

## GTFS output

Generate GTFS fare files:

```sh
python3 tools/gtfs_fares.py
```

This writes `dist/fare_attributes.txt` and `dist/fare_rules.txt`. The output
is **GTFS Fares v0** (flat `fare_attributes.txt` + `fare_rules.txt` schema,
predating Fares v1). One rule per route covers all 334 Mandaluyong routes.

Emitted prices equal each mode's base fare — this is a flat approximation.
Exact distance-based pricing requires applying the formula from
`data/fares.json` yourself.

## Pilot web UI

```sh
python3 web/server.py
```

Opens **http://127.0.0.1:8330** (set `HOST` env var to override bind
address). The UI is a single static HTML page with preset trips, custom
distance input, and live mode comparison. Backed by a JSON API:
`GET /api/fare?mode=<mode>&km=<n>`.

**This is a pilot artifact only.** No maps, estimates only, not a production
service.

## Testing

```sh
uv run --with pytest python -m pytest tests/ -q
```

`pytest` is the only dev dependency. `uv` is used for tooling since the host
may lack a system pip.

## Contributing fare updates

Open a PR that cites a source. Every fare entry **must** carry:

- `source` — a URL or `"LTFRB MC <number>"`.
- `as_of` — the date the fare was verified.
- `status` — `"verified"` if confirmed against an official source, or
  `"unverified"` if not yet cross-checked.

## Limitations

- All figures are **estimates**, not official LTFRB or MRT-3 Corporation
  publications. LTFRB circulars supersede anything in this dataset.
- Motorcycle taxi apps (Angkas, Move It, Grab) are excluded — fare data is
  closed and in-app only. A crowd-sourced tier is planned for the future.
- The GTFS output is a flat-fare v0 approximation. Distance-based pricing
  must be applied separately.
- MRT-3 station bands reflect published pre-discount fares (a 50% fuel-cost
  discount has been in effect since 2026-03-23).

## License

MIT
