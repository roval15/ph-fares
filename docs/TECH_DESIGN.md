# ph-fares — Technical Solution Design

> Mandaluyong pilot. Pure-Python, stdlib-only runtime.

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Data Model](#2-data-model)
3. [Library API](#3-library-api)
4. [Route Extraction Pipeline](#4-route-extraction-pipeline)
5. [GTFS Artifact Generation](#5-gtfs-artifact-generation)
6. [Pilot UI](#6-pilot-ui)
7. [Testing Strategy](#7-testing-strategy)
8. [Constraints & Decisions](#8-constraints--decisions)

---

## 1. Overview & Architecture

```
data/fares.json ──▶ phfares/ ──▶ consumers
                                  ├─ tools/gtfs_fares.py  (CLI → dist/)
                                  └─ web/server.py        (HTTP → pilot UI)
```

Three layers, no database, no services:

| Layer | Path | Purpose |
|---|---|---|
| **Dataset** | `data/fares.json` | Versioned, machine-readable fare data for Mandaluyong modes |
| **Library** | `phfares/` | Pure-Python (stdlib-only) API: `fare()`, `routes_for()` |
| **Consumers** | `tools/gtfs_fares.py`, `web/server.py` | GTFS artifact generator and pilot web UI |

Runtime constraint: Python 3.13, no system pip. All runtime imports are
stdlib-only. `pytest` (via `uv`) is the sole dev dependency.

---

## 2. Data Model

`data/fares.json` is the single source of truth for fares in the pilot.

### Top-level schema

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | Schema version (starts at 1) |
| `currency` | string | Always `"PHP"` |
| `as_of` | string (date) | Dataset vintage date (e.g. `"2026-08-25"`) |
| `region` | object | `{ pilot_city, bbox: [south, west, north, east] }` |
| `modes` | object | Map of mode key → fare definition |

### Mode entry fields

Every entry under `modes` carries:

| Field | Required | Notes |
|---|---|---|
| `fare_model` | yes | `"distance"` (formula) or `"station_table"` (lookup) |
| `source` | yes | Citable URL or `"LTFRB MC <no>"` |
| `as_of` | yes | Date string |
| `status` | yes | `"verified"` or `"unverified"` |

For `fare_model: "distance"`:

| Field | Type | Description |
|---|---|---|
| `base_fare` | float | Flat fare for the first `base_km` |
| `base_km` | float | Threshold distance |
| `per_km` | float | Rate charged per km beyond `base_km` |

For `fare_model: "station_table"` (e.g. MRT-3), the entry carries a
station-pair fare lookup instead of the distance formula fields.

### Verified vs. unverified contract

- **verified**: Fare structure confirmed by 3+ independent recent encodings
  of the LTFRB formula (traditional and modern jeepney). Mechanical QA:
  verified entries must carry a source URL.
- **unverified**: LTFRB formula exists but not independently confirmed for
  the pilot scope (e.g. city bus if included). Must carry
  `status: "unverified"` in the dataset.

This distinction is a first-class data field, not a code concern — the library
does not branch on it.

---

## 3. Library API

### `fare(mode, km) -> float`

```python
from phfares import fare
```

**Semantics:**

1. `km <= 0` → `ValueError` ("km must be positive")
2. `mode` not found → `KeyError` (helpful message listing valid modes)
3. `km <= base_km` → returns `base_fare`
4. `km > base_km` → returns `base_fare + (km - base_km) * per_km`

**Rounding:** fares round to 2 decimal places, half-up (Python `Decimal`
quantize with `ROUND_HALF_UP`).

### Worked examples

| Mode | km | Calculation | Result |
|---|---|---|---|
| `jeepney_traditional` | 4.0 | `km <= base_km` → base fare | **12.00** |
| `jeepney_traditional` | 7.5 | `12 + (7.5 - 4) × 1.80` | **18.30** |
| `jeepney_modern` | 4.0 | `km <= base_km` → base fare | **14.00** |
| `jeepney_modern` | 5.0 | `14 + (5 - 4) × 2.20` | **16.20** |

### `routes_for(city) -> list[dict]`

Returns jeepney routes serving the named city, sourced from
`data/routes_mandaluyong.json`. Non-empty for `"Mandaluyong"`.

---

## 4. Route Extraction Pipeline

One-off pipeline to produce `data/routes_mandaluyong.json` from the community
GTFS feed.

### Steps

1. Clone/fetch `sakayph/gtfs` (https://github.com/sakayph/gtfs) into a
   **scratch directory** (never committed to this repo).
2. Parse `stops.txt`, `routes.txt`, `trips.txt`.
3. Filter: keep routes with ≥ 1 stop inside the pilot bbox
   `[14.555, 121.025, 14.600, 121.065]`.
4. Emit `[{route_id, route_long_name, n_stops_in_bbox}, ...]`.
5. Sanity check: output is non-empty and includes at least one route whose
   name mentions Shaw, EDSA, Boni, or Ortigas.

### Idempotency

The scratch clone is disposable — the pipeline re-clones each run. The output
file (`routes_mandaluyong.json`) is committed; the clone is not.

### Why the clone is never committed

The GTFS feed is a large, external dataset that changes independently of this
repo. Committing it would bloat history and create merge conflicts with no
upside — the pipeline regenerates `routes_mandaluyong.json` from it on demand.

---

## 5. GTFS Artifact Generation

### `tools/gtfs_fares.py`

CLI script that reads `data/fares.json` and writes GTFS fare files to `dist/`.

### GTFS Fares version

The pilot emits **GTFS Fares v0** (`fare_attributes.txt` / `fare_rules.txt`
semantics). Rationale: Fares v0 is the widely-deployed format; the community
feed (`sakayph/gtfs`) and most GTFS consumers expect it. Fares v2
(`fare_leg_rules.txt`, `fare_transfer_rules.txt`) is more expressive but
adds complexity with no consumer benefit for Mandaluyong jeepney routes in
this pilot.

### Output

```
dist/
  fare_attributes.txt    # fare_id, price, currency_type, payment_method, transfers, transfer_duration
  fare_rules.txt         # fare_id, route_id, origin_id, destination_id, contains_id
```

Exit code 0 on success; CSV output parses with required GTFS columns.

---

## 6. Pilot UI

### Server

`web/server.py` — Python stdlib `http.server`. No FastAPI, no dependencies.

```bash
python3 web/server.py
```

### API endpoint

```
GET /api/fare?mode=<mode>&km=<n>  →  JSON { "mode": ..., "km": ..., "fare": ... }
```

Backed by `phfares.fare()` imported from repo root.

### Frontend

Single static HTML file (`web/index.html`) with inline CSS + JS. No build
step, no npm, no node artifacts.

- **Mobile-first** — must work at 375px width.
- **Preset trips** — ≥ 3 hardcoded Mandaluyong trips (e.g. Shaw → Ortigas
  ~2.5 km, Boni → Ortigas ~1.8 km, City Hall → Shaw ~1.2 km).
- **Mode comparison** — fares shown for all available modes; cheapest
  highlighted.
- **Optional slider** — "what if" distance adjustment with live fare updates.

### No npm artifacts

All frontend code is inline in a single HTML file. No `node_modules/`, no
`package.json`, no build output.

---

## 7. Testing Strategy

### Runner

```bash
uv run --with pytest python -m pytest tests/ -q
```

`pytest` is the only dev dependency; no system pip needed.

### Test matrix (minimum)

| Area | Cases |
|---|---|
| **Traditional jeepney** | km 0.5 → 12.00 (base); km 4.0 → 12.00 (boundary); km 7.5 → 18.30 |
| **Modern jeepney** | km 4.0 → 14.00 (boundary); km 5.0 → 16.20 |
| **Error handling** | km ≤ 0 → `ValueError`; unknown mode → `KeyError` |
| **Dataset validation** | Every mode has `source`, `as_of`, `status`; verified entries have source URL |
| **Routes** | Non-empty; bbox membership correct for synthetic stop inside/outside bbox |
| **GTFS output** | `gtfs_fares.py` exit 0; output parses as CSV with required GTFS columns |

---

## 8. Constraints & Decisions

### Hard constraints

| Constraint | Rationale |
|---|---|
| **Python 3.13** | Host runtime; no system pip → `uv` for tooling |
| **stdlib-only runtime** | No external packages at runtime; keeps deployment trivial |
| **No system pip** | Host restriction; all tooling via `uv run` |
| **No branch switching** | Orchestrator controls branching; commit on current branch only |

### Design decisions

| Decision | Rationale |
|---|---|
| Pure-Python, no native deps | Pilot simplicity; no build toolchain needed |
| `fares.json` as dataset format | Human-readable, diffable, version-controllable; no DB needed |
| GTFS Fares v0 over v2 | Widely deployed; community feed expects it; v2 adds no consumer benefit for pilot scope |
| Stdlib `http.server` over FastAPI | Zero dependencies; pilot UI is trivial |
| `uv` as tool manager | Host constraint; avoids system pip |
| Scratch clone, never committed | External dataset changes independently; regenerate on demand |

### Honest limitations

- Fare data is **estimates only** — LTFRB circulars supersede this dataset.
- Motorcycle taxi apps (Angkas, Move It, Grab) are excluded — no published
  fare data exists.
- City bus fares are unverified if included — carry `status: unverified`.
- MRT-3 is station-to-station; no distance-based formula.
- The dataset is a point-in-time snapshot; `as_of` dates matter.
