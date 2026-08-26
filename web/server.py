#!/usr/bin/env python3
"""Pilot web server for ph-fares Mandaluyong fare estimates.

Usage:
    python3 web/server.py          # binds 127.0.0.1:8330
    HOST=0.0.0.0 python3 web/server.py  # override bind address
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Make phfares importable from repo root (one level up from web/)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from phfares import fare  # noqa: E402

import importlib
guide_geocode = importlib.import_module("guide.geocode")  # noqa: E402
import guide.planner as guide_planner  # noqa: E402
import guide.feedback as guide_feedback  # noqa: E402
from guide.feedback import (
    _dedupe_records,
    _calendar_day,
    _VALID_KINDS,
    freshness as feedback_freshness,
)  # noqa: E402

_HTML_PATH = Path(__file__).resolve().parent / "index.html"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8330"))

DISTANCE_MODES = ["jeepney_traditional", "jeepney_modern"]


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class FareHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_index()
        elif path == "/api/fare":
            self._serve_fare(parsed.query)
        elif path == "/api/geocode":
            self._serve_geocode(parsed.query)
        elif path == "/api/plan":
            self._serve_plan(parsed.query)
        elif path == "/api/freshness":
            self._serve_freshness(parsed.query)
        elif path == "/api/community":
            self._serve_community()
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/feedback":
            self._serve_feedback_post()
        else:
            self._send_json(404, {"error": "Not found"})

    # -- helpers ---------------------------------------------------------------

    def _serve_index(self):
        try:
            html = _HTML_PATH.read_text(encoding="utf-8")
            self._send_response(200, "text/html; charset=utf-8", html.encode())
        except FileNotFoundError:
            self._send_json(500, {"error": "index.html not found"})

    def _serve_fare(self, query_string: str):
        params = parse_qs(query_string)
        mode_raw = params.get("mode", [None])[0]
        km_raw = params.get("km", [None])[0]

        if not mode_raw or not km_raw:
            self._send_json(
                400,
                {"error": "Missing required query parameters: mode, km"},
            )
            return

        try:
            km = float(km_raw)
        except (ValueError, TypeError):
            self._send_json(
                400,
                {"error": f"Invalid km value: '{km_raw}'. Must be a number."},
            )
            return

        try:
            result = fare(mode_raw, km)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except KeyError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(500, {"error": f"Internal error: {exc}"})
            return

        self._send_json(
            200,
            {
                "mode": mode_raw,
                "km": km,
                "fare": float(result),
                "currency": "PHP",
                "status": "ok",
            },
        )

    def _serve_geocode(self, query_string: str):
        params = parse_qs(query_string)
        q = params.get("q", [None])[0]

        if not q or not q.strip():
            self._send_json(
                400,
                {"status": "error", "message": "Missing required query parameter: q"},
            )
            return

        try:
            candidates = guide_geocode.geocode(q.strip())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._send_json(
                503,
                {"status": "error", "message": "Geocoding service is unavailable right now. Please try again later."},
            )
            return
        except Exception as exc:
            self._send_json(
                500,
                {"status": "error", "message": "An unexpected error occurred while geocoding."},
            )
            return

        self._send_json(200, {
            "status": "ok",
            "query": q,
            "candidates": candidates,
        })

    def _serve_plan(self, query_string: str):
        params = parse_qs(query_string)
        errors = []
        for name in ("from_lat", "from_lon", "to_lat", "to_lon"):
            raw = params.get(name, [None])[0]
            if raw is None or raw.strip() == "":
                errors.append(f"Missing required parameter: {name}")
                continue
            try:
                val = float(raw)
                if not math.isfinite(val):
                    errors.append(f"Parameter {name} must be a finite number, got '{raw}'")
            except (ValueError, TypeError):
                errors.append(f"Parameter {name} is not a valid number: '{raw}'")

        if errors:
            self._send_json(400, {"status": "error", "message": "; ".join(errors)})
            return

        from_lat = float(params["from_lat"][0])
        from_lon = float(params["from_lon"][0])
        to_lat = float(params["to_lat"][0])
        to_lon = float(params["to_lon"][0])

        try:
            result = guide_planner.plan(from_lat, from_lon, to_lat, to_lon)
        except Exception as exc:
            self._send_json(
                500,
                {"status": "error", "message": "An unexpected error occurred while planning your trip."},
            )
            return

        if result.get("status") == "ok":
            route_freshness: dict[str, dict] = {}
            route_ids_seen: set[str] = set()
            for opt in result.get("options", []):
                for leg in opt.get("legs", []):
                    if leg.get("type") == "ride" and "route_id" in leg:
                        route_ids_seen.add(leg["route_id"])
            for rid in route_ids_seen:
                try:
                    f = feedback_freshness(rid)
                    route_freshness[rid] = {
                        "tier": f["tier"],
                        "confirmations": f["confirmations"],
                        "disputes": f["disputes"],
                    }
                except Exception:
                    route_freshness[rid] = {"tier": "gray", "confirmations": 0, "disputes": 0}
            result["route_freshness"] = route_freshness

        self._send_json(200, result)

    def _serve_feedback_post(self):
        content_length_raw = self.headers.get("Content-Length")
        if content_length_raw is None:
            self._send_json(400, {"status": "error", "message": "Missing Content-Length header."})
            return
        try:
            content_length = int(content_length_raw)
        except (ValueError, TypeError):
            self._send_json(400, {"status": "error", "message": "Invalid Content-Length."})
            return
        if content_length > 10240:
            self._send_json(400, {"status": "error", "message": "Request body too large (max 10 KB)."})
            return
        try:
            body_bytes = self.rfile.read(content_length)
        except Exception:
            self._send_json(400, {"status": "error", "message": "Failed to read request body."})
            return
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"status": "error", "message": "Invalid JSON in request body."})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"status": "error", "message": "Request body must be a JSON object."})
            return

        route_id = body.get("route_id")
        kind = body.get("kind")
        alias = body.get("alias") or None
        note = body.get("note") or None

        if not route_id or not isinstance(route_id, str) or not route_id.strip():
            self._send_json(400, {
                "status": "error",
                "message": "Missing or empty 'route_id'. Please specify which route you are reporting on.",
            })
            return

        if kind not in _VALID_KINDS:
            self._send_json(400, {
                "status": "error",
                "message": f"Invalid kind {kind!r}. Must be one of: confirm, dispute, note.",
            })
            return

        try:
            guide_feedback.append_feedback(route_id, kind, alias=alias, note=note)
            f = feedback_freshness(route_id)
            self._send_json(200, {"ok": True, "freshness": f})
        except Exception as exc:
            self._send_json(500, {
                "status": "error",
                "message": "An unexpected error occurred while saving your feedback.",
            })

    def _serve_freshness(self, query_string: str):
        params = parse_qs(query_string)
        route_id = params.get("route_id", [None])[0]
        if not route_id or not route_id.strip():
            self._send_json(400, {
                "status": "error",
                "message": "Missing required parameter: route_id",
            })
            return
        try:
            f = feedback_freshness(route_id.strip())
            self._send_json(200, f)
        except Exception:
            self._send_json(500, {
                "status": "error",
                "message": "An unexpected error occurred while fetching freshness.",
            })

    def _serve_community(self):
        try:
            records = guide_feedback.load_feedback()
            deduped = _dedupe_records(records)

            # Count total reports (all deduped kinds)
            total_reports = len(deduped)

            # Group by route_id
            route_map: dict[str, list[dict]] = {}
            for r in deduped:
                rid = r["route_id"]
                route_map.setdefault(rid, []).append(r)

            corridors = []
            for rid, r_records in route_map.items():
                confirms = [r for r in r_records if r["kind"] == "confirm"]
                alias_counts: dict[str, int] = {}
                for r in confirms:
                    alias = (r.get("alias") or "").strip()
                    if not alias:
                        continue
                    alias_counts[alias] = alias_counts.get(alias, 0) + 1
                top_stewards = sorted(
                    [{"alias": a, "confirmations": c} for a, c in alias_counts.items()],
                    key=lambda s: (-s["confirmations"], s["alias"]),
                )[:3]
                corridors.append({
                    "route_id": rid,
                    "route_long_name": None,
                    "report_count": len(r_records),
                    "top_stewards": top_stewards,
                })

            corridors.sort(key=lambda c: -c["report_count"])

            self._send_json(200, {
                "total_reports": total_reports,
                "corridors": corridors,
            })
        except Exception:
            self._send_json(500, {
                "status": "error",
                "message": "An unexpected error occurred while fetching community data.",
            })

    def _send_json(self, code: int, body: dict):
        payload = json.dumps(body, ensure_ascii=False, default=_json_default).encode("utf-8")
        self._send_response(code, "application/json; charset=utf-8", payload)

    def _send_response(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Keep logs minimal
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def main():
    server = ThreadingHTTPServer((HOST, PORT), FareHandler)
    print(f"Serving ph-fares pilot UI at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
