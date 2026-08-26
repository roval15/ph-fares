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

        self._send_json(200, result)

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
