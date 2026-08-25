#!/usr/bin/env python3
"""Pilot web server for ph-fares Mandaluyong fare estimates.

Usage:
    python3 web/server.py          # binds 127.0.0.1:8330
    HOST=0.0.0.0 python3 web/server.py  # override bind address
"""
from __future__ import annotations

import json
import os
import sys
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

_HTML_PATH = Path(__file__).resolve().parent / "index.html"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8330"))

DISTANCE_MODES = ["jeepney_traditional", "jeepney_modern"]


class FareHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_index()
        elif path == "/api/fare":
            self._serve_fare(parsed.query)
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

    def _send_json(self, code: int, body: dict):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
