# ph-fares — Pilot Web UI

Mobile-first pilot web experience for Mandaluyong City fare estimates.

## Quick start

From the **repo root**:

```bash
python3 web/server.py
```

The server starts on **http://127.0.0.1:8330** (fixed port).

### Override bind address

```bash
HOST=0.0.0.0 PORT=9000 python3 web/server.py
```

## API

```
GET /api/fare?mode=jeepney_traditional&km=7.5
```

Returns JSON:

```json
{
  "mode": "jeepney_traditional",
  "km": 7.5,
  "fare": 18.3,
  "currency": "PHP",
  "status": "ok"
}
```

### Available distance modes

| Mode | Base fare | Base km | Per km |
|------|-----------|---------|--------|
| `jeepney_traditional` | ₱12 | 4 | ₱1.80 |
| `jeepney_modern` | ₱14 | 4 | ₱2.20 |

MRT-3 pricing is available through `GET /api/plan` (see below).

### Error responses

- `400` — missing or invalid parameters, unknown mode, or non-distance mode.
- `404` — unknown path.

## Commute Guide API

### Geocode

```
GET /api/geocode?q=<text>
```

Resolves a place name to coordinate candidates using OpenStreetMap
Nominatim (with a local cache and rate limiting).

Returns JSON:

```json
{
  "status": "ok",
  "query": "Shaw Blvd Mandaluyong",
  "candidates": [
    {
      "display_name": "Shaw Boulevard, Mandaluyong, Metro Manila, Philippines",
      "lat": 14.5813,
      "lon": 121.0491
    }
  ]
}
```

A blank or missing `q` returns `400`. When no match is found the response
still has `"status": "ok"` with an empty `candidates` list. If
Nominatim is unreachable the response is `503`.

### Plan

```
GET /api/plan?from_lat=<n>&from_lon=<n>&to_lat=<n>&to_lon=<n>
```

All four parameters are required finite numbers. Plans a trip between two
coordinate pairs and returns options sorted cheapest-first.

Successful response:

```json
{
  "status": "ok",
  "options": [
    {
      "legs": [
        {
          "type": "walk",
          "from": {"lat": 14.58, "lon": 121.05},
          "to": {"lat": 14.58, "lon": 121.05},
          "distance_m": 350
        },
        {
          "type": "ride",
          "mode": "jeepney_traditional",
          "route_id": "MJ001",
          "route_long_name": "Shaw–Boni",
          "board_stop": {
            "stop_id": "LTFRB-1234",
            "stop_name": "Shaw Blvd",
            "lat": 14.5813,
            "lon": 121.0491,
            "distance_m": 120.5
          },
          "alight_stop": {
            "stop_id": "LTFRB-5678",
            "stop_name": "Boni Ave",
            "lat": 14.5876,
            "lon": 121.0352,
            "distance_m": 95.0
          },
          "distance_km": 2.1
        },
        {
          "type": "walk",
          "from": {"lat": 14.58, "lon": 121.05},
          "to": {"lat": 14.59, "lon": 121.05},
          "distance_m": 420
        }
      ],
      "fare_breakdown": {"jeepney_traditional": 12.0},
      "total_fare": 12.0,
      "notes": []
    }
  ]
}
```

Each leg is either `"type": "walk"` (with `distance_m` in meters) or
`"type": "ride"` (with `mode`, `route_id`, `route_long_name`, `board_stop`,
`alight_stop`, and `distance_km`). `board_stop` and `alight_stop` are stop
objects carrying `stop_id`, `stop_name`, `lat`, `lon`, and `distance_m` (the
walking distance from the caller's point to the stop, in meters). Walk legs
are free; ride fares follow the LTFRB distance formula or the MRT-3 station
table.

Invalid parameters return `400`. When no direct route connects the points
the response is `200` with `"status": "no_route"` and a human-readable
message.

## What's in here

- `web/server.py` — Python stdlib HTTP server (no frameworks, no npm).
- `web/index.html` — single static file with inline CSS + JS. Works offline.
- `web/README.md` — this file.

## Limitations

Pilot artifact only. Estimates may differ from actual LTFRB-regulated fares.
Always check the latest LTFRB circulars for authoritative fare information.
