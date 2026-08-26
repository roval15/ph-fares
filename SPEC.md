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

### 6. Pilot UI/UX experience (epic E4)

**Goal:** a simple, no-maps web experience that lets us feel and critique the
user journey before investing in real map integration. Pilot artifact only.

User journey to prototype (single page, mobile-first):
1. Commuter lands on the page → sees the Mandaluyong pilot branding + one-line
   purpose ("Know your fare before you commute").
2. Picks a **preset trip** (e.g. Shaw Blvd → Ortigas ~2.5 km, Boni → Ortigas
   ~1.8 km, Mandaluyong City Hall → Shaw ~1.2 km) OR types a custom distance.
3. Page shows the fare **per available mode** (traditional jeepney, modern
   jeepney, MRT-3 when in dataset) as a comparison — cheapest highlighted.
4. Optionally a "what if" slider adjusting distance to see fares move live.

Technical constraints:
- Python stdlib HTTP server (`http.server`) or FastAPI — coder's choice,
  document it. Frontend: ONE static HTML file + inline/small CSS+JS. No build
  step, no npm.
- Small JSON API endpoint: `GET /api/fare?mode=<mode>&km=<n>` → JSON fare,
  backed by the `phfares` library (imported from repo root).
- Must work at 375px width (mobile). Clean, calm design; honest pilot copy.
- `web/` directory in repo; README documents how to run it.

Acceptance criteria (mechanical):
- `python3 web/server.py` (or documented command) starts and serves `/`
  returning HTML that mentions Mandaluyong.
- `curl /api/fare?mode=jeepney_traditional&km=7.5` returns JSON with fare 18.30.
- Page includes ≥3 preset trips and renders the mode comparison for one.
- No npm/node artifacts committed.

### 7. Epic E6: Commute Guide — Location 1 to Location 2 (Level A: direct routes)

**Goal:** user enters origin + destination → gets a commute guide with fare
breakdown per mode. Designed so Level B (multi-leg transfers) extends it
later WITHOUT schema rework.

**Design-for-B requirements (binding):**
- Itinerary is a LIST OF LEGS (`walk` / `ride` / later `transfer-wait`) —
  multi-leg journeys are just longer lists, no schema change.
- Finder primitives are pure, composable functions with docstrings noting
  how Level B's BFS over the transfer graph reuses them:
  `nearest_stops`, `routes_at_stop`, `ride_segment(route, stop_a, stop_b)`.
- Geocode cache persists (Level B needs it too).

#### S6.1 — Stops dataset + geocode layer
- `tools/extract_stops.py` from sakayph/gtfs (scratch clone, NEVER committed):
  - `data/stops_mandaluyong.json`: stops inside pilot bbox EXPANDED by a
    0.02° margin: `{stop_id, stop_name, lat, lon, route_ids[]}`.
  - `data/stop_sequences.json`: for each route in routes_mandaluyong.json,
    the stop sequence of its most-stops trip, incl. `shape_dist_traveled`
    where present in stop_times.txt.
  - MRT-3 stations (route_type=2 rail) with coords — must include Shaw Blvd
    and Boni stations.
- `guide/geocode.py`: `geocode(query)` → candidates
  `[{display_name, lat, lon}]` via Nominatim:
  `https://nominatim.openstreetmap.org/search?q=<q>&format=jsonv2&limit=5&countrycodes=ph`
  with a descriptive User-Agent header (Nominatim ToS requires it), max
  1 request/sec, results cached persistently in `data/geocode_cache.json`.
- Unit tests mock HTTP (no live network in tests); determinism check on the
  extractor.

AC: (1) stops file non-empty, every stop has lat/lon/route_ids; (2)
stop_sequences covers ≥90% of routes_mandaluyong.json with ≥2 stops each;
(3) MRT-3 stations include Shaw + Boni (case-insensitive); (4) repeated
geocode of the same query makes zero HTTP calls the second time (mock test).

#### S6.2 — Route finder core
- `guide/finder.py`: `nearest_stops(lat, lon, radius_m=400)` (haversine);
  `candidate_direct_routes(from, to, walk_radius_m=400)` → candidates with
  board_stop, alight_stop, ride_km, walk_from_m, walk_to_m.
- `ride_km` computed ALONG THE ROUTE: `shape_dist_traveled` at alight stop
  minus board stop; fallback = sum of haversine between consecutive stops in
  sequence. NEVER straight-line origin→destination.
- `guide/planner.py`: `plan(from, to)` → options sorted cheapest-first, each:
  `{legs: [...], fare_breakdown: {mode: fare}}` using `phfares.fare(mode,
  ride_km)`; MRT-3 option appears only when both points are within 600 m of
  MRT-3 stations, priced via dataset station_bands (station count between
  them) with the active-discount note surfaced.
- No-route case returns a structured "no direct route found" result (friendly,
  machine-readable) — not an exception.
- Unit tests with synthetic fixtures: curved-route test proving ride_km is
  along-route not straight-line; plan() end-to-end on fixtures; MRT-3 gating.

AC: (1) `uv run --with pytest python -m pytest tests/ -q` green; (2) fixture
plan() returns walk+ride+walk legs with fares matching phfares math; (3)
curved-route test passes; (4) MRT-3 option gated correctly.

#### S6.3 — Plan API + UI v2
- `web/server.py` additions: `GET /api/geocode?q=` → JSON candidates;
  `GET /api/plan?from_lat=&from_lon=&to_lat=&to_lon=` → JSON itinerary.
- `web/index.html` v2, two tabs:
  1. **Commute Guide** (default): origin + destination text inputs →
     candidate picker (tap to confirm) → Plan → leg cards (walk m, ride
     route/boarding stops/distance) + fare breakdown per mode with CHEAPEST
     badge + MRT-3 card when applicable.
  2. **Fare Calculator** — existing page preserved verbatim (regression:
     7.5 km traditional = 18.30).
- Mobile-first 375px, no npm, no maps. Friendly errors for no-results and
  no-route cases.

AC: (1) `/api/geocode?q=Shaw` returns ≥1 candidate with lat/lon; (2)
`/api/plan` between real Mandaluyong points returns legs + fare breakdown
matching phfares math; (3) both tabs work; calculator regression intact;
(4) no npm artifacts; viewport OK; no-route and no-geocode cases return
friendly JSON/UI messages.

#### E6 Gate
Commuter simulation with real trips (Mandaluyong City Hall → Ortigas; Shaw →
Boni; one out-of-coverage destination → friendly handling), then merge to
main + push to the existing GitHub repo + Telegram notify.

### 8. Epic E7: Community Data Flywheel (Level 1)

**Goal:** every trip search becomes a data-quality event. The pilot's 2017-vintage
routes get fresher WHERE PEOPLE WALK — confirmations, disputes, and corrections
collected in the moment of use, visible to the next commuter.

**Core loop:** search → see freshness → confirm/dispute → next commuter sees
better data → more trust → more searches → more signal.

**Scope decisions (binding):**
- Intake channel for E7 = in-app web UI ONLY. Telegram bot intake is deferred
  (backlog card, future E8).
- Recognition = lightweight v1: contribution counts + "Route Steward" label
  for the top confirmer per corridor. No full leaderboard, no accounts.
- Corrections are NEVER auto-applied to the dataset. They are stored,
  surfaced, and ranked with — human/swarm review promotes them later.

#### S7.1 — Feedback data layer + freshness engine
- `data/community_updates.jsonl` — append-only ledger, one JSON object per
  line: `{ts (ISO), route_id, kind: confirm|dispute|note, alias? (free text,
  optional), note? (kind=note), fingerprint}`. Malformed lines are skipped
  with a warning, never crash the loader.
- `guide/feedback.py`:
  - `append_feedback(route_id, kind, alias=None, note=None, fingerprint=...)`
  - `load_feedback()` → list of records
  - `freshness(route_id)` → `{tier, confirmations, disputes, last_confirmed,
    stewards}` with tiers:
    - `green`: ≥3 confirmations within 30 days, disputes ≤ confirmations
    - `yellow`: ≥1 confirmation within 90 days
    - `disputed`: disputes > confirmations within 30 days
    - `gray`: never confirmed (the 2017-feed default)
  - Dedupe: same route + fingerprint + kind within the same calendar day
    counts once.
- Privacy: alias is optional free text; docstring states aliases are public
  and no other PII is collected.
- Tests: freshness tier math, dedupe, malformed-line tolerance, steward
  attribution (most confirmations on a route = steward). No live network.

AC: (1) full suite green incl. pre-existing; (2) tier thresholds exactly as
specified (unit tests on synthetic clock values); (3) dedupe works; (4) no
network in tests.

#### S7.2 — Feedback API + UI v3 (freshness chips + post-search prompt)
- `web/server.py` additions (all prior endpoints intact):
  - `POST /api/feedback` body `{route_id, kind, alias?, note?}` → 200
    `{ok: true, freshness: {...}}`; invalid kind / missing route_id → friendly
    JSON 400.
  - `GET /api/freshness?route_id=...` → freshness JSON; unknown route → gray
    defaults (NOT an error).
  - `/api/plan` responses gain a `route_freshness` map (route_id → tier +
    counts) for every ride leg.
- Planner integration: options sort cheapest-first as today, BUT disputed
  routes sort below non-disputed options at equal total fare.
- `web/index.html` v3:
  - Freshness chip on every Commute Guide option, color-coded:
    green "Verified by N commuters · Xd ago", yellow "Recently confirmed",
    gray "Unverified (2017 data)", disputed "⚠ Reported issues".
  - Post-search prompt below results: "Help the next commuter — did this
    route actually run today?" → tap an option → 👍 Still runs / 👎 Gone or
    changed / ✏️ Quick note → POST /api/feedback → toast "Salamat! Your report
    helps future commuters." Alias prompted once, remembered via localStorage.
  - Community strip (bottom of Commute Guide tab): total reports, top Route
    Stewards by corridor (alias + count).
  - Fare Calculator tab preserved verbatim (regression: 7.5 km traditional
    = 18.30).
- Mobile-first 375px, no npm, no maps.

AC: (1) POST round-trip works and jsonl gains exactly one line; (2) duplicate
same-day same-fingerprint confirm counted once; (3) freshness chips render
per tier; (4) disputed option sinks below equal-fare clean option; (5)
calculator regression intact; (6) suite green; (7) no npm artifacts.

#### E7 Gate
Commuter simulation: plan Shaw→Boni → all chips gray → submit confirmations
(≥3 distinct fingerprints) + one dispute via API/UI → re-plan → chips update,
disputed option sinks → jsonl is append-only and parseable. Then merge to
main, push to GitHub, Telegram notify.

### 9. Epic E9: Landmark Anchoring (Grab-style location selection)

**Goal:** anchor origin/destination to the nearest landmark, establishment, or
building — the way Grab does — instead of a vague street segment. Commuters
think "SM Megamall", not "Shaw Boulevard, Wack-Wack Greenhills, Eastern
Manila District".

**Flow (binding UX):**
1. User types a location → geocode candidates (existing behavior).
2. User taps a candidate → NEW anchor step: "Anchor to a landmark" panel
   lists nearby named places sorted by distance: e.g. "Shaw Boulevard MRT
   Station · 80 m", "SM Megamall · 240 m", plus a fallback row
   "Use exact point".
3. User taps a landmark → trip endpoint snaps to the POI coords; the
   confirmed line shows the landmark name (with the area as context).
4. If the POI fetch fails or returns nothing → silently fall back to exact
   point. Landmark anchoring must NEVER block the trip flow.

#### S9.1 — POI layer
- `guide/pois.py`: `nearby_pois(lat, lon, radius_m=250, limit=5)` →
  `[{name, category, distance_m, lat, lon, source}]`.
  - Source 1 (offline, always first): MRT-3 stations from
    data/mrt3_stations.json within radius (zero network).
  - Source 2: Overpass API around-query for named places:
    node/way with amenity|shop|tourism|railway=station|building+name within
    radius. Only entries WITH a name tag. Mirror failover:
    overpass-api.de → overpass.kumi.systems (10s timeout each).
  - Merge, sort by distance, cap at limit. category from primary tag
    (station/mall/restaurant/office/building/...).
  - Persistent cache keyed on coords rounded to ~50m grid
    (`data/poi_cache.json`) — repeat queries for the same spot = zero HTTP.
  - Graceful degradation: any Overpass failure → return offline results
    only (or empty), with a warning; never raise to the caller.
  - User-Agent header per OSM etiquette; max 1 req/sec.
- Tests (mocked HTTP): distance sorting, MRT station inclusion offline,
  cache zero-second-call, degradation when both mirrors fail, name-tag
  filtering.

AC: (1) suite green incl. all pre-existing; (2) nearby_pois around Shaw MRT
coords (offline-only path) includes "Shaw MRT" at ~0 m; (3) mocked Overpass
response merges + sorts correctly; (4) mirror failover + degradation tested;
(5) no live network in the test suite.

#### S9.2 — Anchor API + UI v4
- `web/server.py`: `GET /api/pois?lat=&lon=&radius=250` → JSON
  `{status:"ok", pois:[...]}` (empty list is ok, never 500).
- `web/index.html` v4 (all prior tabs/features intact — chips, feedback
  prompt, calculator, badge cap, show-more):
  - After a geocode candidate is tapped, fetch /api/pois and render the
    anchor panel (name + category icon/label + distance). Tap → snap.
  - "Use exact point" always available as the last row.
  - Confirmed line shows landmark name + area context.
  - Loading state ("Finding landmarks nearby…"), failure state = skip
    silently to exact point.
  - Mobile-first 375px, no npm, no maps.
- Tests for the endpoint + graceful empty/degraded responses.

AC: (1) /api/pois around Shaw MRT coords returns the station; (2) around a
random Mandaluyong point returns ≥1 named POI or empty-ok (never 500);
(3) UI anchor panel renders from the endpoint; (4) all prior regressions
green (chips, feedback, calculator 18.30, badge cap, suite).

#### E9 Gate
Commuter simulation: type "Shaw Boulevard Mandaluyong" → tap candidate →
anchor panel shows Shaw MRT / nearby establishments → tap anchor → plan
uses landmark coords. Failure-path check: POI endpoint degraded → flow
continues on exact point. Merge → push → Telegram.

### 10. README.md

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
