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

MRT-3 uses a station-table model and is not available via this API.

### Error responses

- `400` — missing or invalid parameters, unknown mode, or non-distance mode.
- `404` — unknown path.

## What's in here

- `web/server.py` — Python stdlib HTTP server (no frameworks, no npm).
- `web/index.html` — single static file with inline CSS + JS. Works offline.
- `web/README.md` — this file.

## Limitations

Pilot artifact only. Estimates may differ from actual LTFRB-regulated fares.
Always check the latest LTFRB circulars for authoritative fare information.
