# ph-fares — Pilot Spec: Mandaluyong City

> Open-source fare dataset + tiny library for Philippine public transport.
> Pilot scope: **Mandaluyong City**. Everything here is deliberately small.

## Why

- Ride-hailing apps (Grab/Angkas/Move It) keep fare data closed and in-app.
- The community GTFS feed for Metro Manila (`sakayph/gtfs`) has routes and
  stops but **no `fares.txt`** — the fare layer is missing from the only
  established open dataset.
- Fare structures for franchised modes (jeepney, bus) are LTFRB-regulated
  public facts. Encoding them is legal and useful.

## Pilot goals (this phase ONLY)

1. A versioned, machine-readable fare dataset for the modes relevant to a
   Mandaluyong commuter.
2. A tiny Python library (`phfares`) answering: "how much does it cost for
   mode X over distance Y?" with Mandaluyong route awareness.
3. Tests. Everything above is worthless without tests.
4. A `fares.txt` (GTFS) generator for Mandaluyong jeepney routes — the
   community-contribution artifact.

Out of scope for the pilot: web UI, maps, crowd-sourcing intake, Railway
deployment, Angkas/Move It/Grab fare numbers (no published data exists —
see Data availability).

## Data availability (due-diligence verdict, 2026-08-25)

| Mode | Status | Structure |
|---|---|---|
| Traditional jeepney | ✅ verified (LTFRB formula, 3+ independent recent encodings) | ₱12 base for first 4 km, ₱1.80 per km after |
| Modern jeepney (PUJ-mod) | ✅ verified (same provenance) | ₱14 base for first 4 km, ₱2.20 per km after |
| City bus | ⚠️ LTFRB formula exists but unverified for pilot — include with `status: unverified` if a source is found, else exclude | distance-based |
| MRT-3 (touches Mandaluyong at Shaw Blvd & Boni Ave stations) | ⚠️ station-to-station fare table — coder must verify against a citable source (Wikipedia/LRTA news) and record `source` + `as_of`; if unverifiable, include with `status: unverified` | flat distance-based |
| Angkas / Move It / Grab | ❌ closed, no published fares | excluded; future crowd-sourced tier |

**Rule: every fare entry in the dataset MUST carry `source` (URL or "LTFRB MC
<no>") and `as_of` (date). Unverified entries must carry
`status: unverified`. QA checks this mechanically.**

## Deliverables

### 1. Dataset — `data/fares.json`

```jsonc
{
  "schema_version": 1,
  "currency": "PHP",
  "as_of": "2026-08-25",
  "region": { "pilot_city": "Mandaluyong", "bbox": [14.555, 121.025, 14.600, 121.065] },
  "modes": {
    "jeepney_traditional": {
      "fare_model": "distance",
      "base_fare": 12.0, "base_km": 4.0, "per_km": 1.80,
      "source": "...", "as_of": "...", "status": "verified"
    },
    "jeepney_modern": { "...": "..." },
    "mrt3": { "fare_model": "station_table", "...": "..." }
  }
}
```

Design latitude is allowed, but the fields above (`source`, `as_of`,
`status`, fare-model split) are mandatory.

### 2. Library — `phfares/` (Python 3.13, stdlib-only runtime)

Public API (minimum):

```python
from phfares import fare, routes_for

fare("jeepney_traditional", km=7.5)      # -> Decimal/float, e.g. 18.30
fare("jeepney_modern", km=4.0)           # base fare, no per-km
routes_for("Mandaluyong")                # -> list of jeepney routes
                                         #    serving the pilot bbox
```

- Fares round to 2 decimals, half-up (pesos).
- `km <= 0` raises `ValueError`; `km <= base_km` returns base fare.
- Unknown mode raises `KeyError` with a helpful message.

### 3. Mandaluyong route list — `data/routes_mandaluyong.json`

Extract jeepney routes serving Mandaluyong from the community feed:
1. Clone/fetch `sakayph/gtfs` (https://github.com/sakayph/gtfs).
2. Parse `stops.txt` + `routes.txt` + `trips.txt`; keep routes having ≥1
   stop inside the pilot bbox.
3. Emit `[{route_id, route_long_name, n_stops_in_bbox}, ...]`.
4. Sanity: the output must be non-empty and include at least one route whose
   name mentions Shaw, EDSA, Boni, or Ortigas (the major Mandaluyong
   corridors). If none does, investigate the bbox before changing it.

### 4. GTFS artifact — `tools/gtfs_fares.py`

Generate a valid GTFS `fares.txt` for the Mandaluyong jeepney routes using
the dataset (fare attribute mode = distance formula parameters, per GTFS
Fares v0 `fare_attributes.txt`/`fare_rules.txt` semantics — document which
version you emit). Output lands in `dist/`.

### 5. Tests — `tests/` (pytest)

Minimum coverage:
- jeepney traditional: km 0.5 → base; km 4 → base; km 7.5 → 12 + 3.5×1.80 = 18.30
- jeepney modern: km 4 → 14; km 5 → 14 + 1×2.20 = 16.20
- ValueError on km<=0; KeyError on unknown mode
- dataset schema validation: every mode has source/as_of/status; no
  verified entry may lack a source URL
- routes list: non-empty, bbox membership correct for a synthetic stop
  inside/outside the bbox
- gtfs_fares.py: output parses as CSV with required GTFS columns

### 6. README.md

What this is, the pilot scope, the data availability table above, how to
use the library, how to contribute fare updates (PR with source citation),
license (MIT), and an honest limitations section (estimates only; LTFRB
circulars supersede; motorcycle apps excluded).

## Repo notes

- Python 3.13, no system pip on this host → `uv` for tooling
  (`uv run --with pytest python -m pytest tests/ -q`).
- Stdlib-only for runtime code; pytest is the only dev dependency.
- Commit on the task branch; do NOT push (orchestrator pushes).
- The GTFS feed clone goes in a scratch dir, never committed into this repo.

## Acceptance criteria (mechanical)

1. `uv run --with pytest python -m pytest tests/ -q` → all green.
2. `data/fares.json` parses; every mode entry has `source`, `as_of`, `status`.
3. `python3 -c "from phfares import fare; print(fare('jeepney_traditional', 7.5))"` from repo root → `18.30`.
4. `data/routes_mandaluyong.json` exists, is a non-empty JSON list, and includes a Shaw/EDSA/Boni/Ortigas route.
5. `python3 tools/gtfs_fares.py` exits 0 and writes a CSV to `dist/` with GTFS fare columns.
6. README.md exists and contains the data-availability table.
