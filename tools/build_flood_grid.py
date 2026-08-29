#!/usr/bin/env python3
"""Build the NOAH Metro Manila 100yr flood raster grid (build-time tool).

Reads the vendored Project NOAH "Metro Manila 100yr flood hazard" shapefile
from data/flood/src/ (Var: 1=LOW 0-0.5 m, 2=MEDIUM 0.5-1.5 m, 3=HIGH >1.5 m)
and rasterizes it onto a regular ~20 m lat/lon grid. Cell value = max Var of
any polygon containing the cell centre (0 = none).

Outputs (committed):
  data/flood/noah_mm_flood_grid.bin.gz
      gzip-compressed uint8 raster (row 0 = southmost), must be < 5 MB
  data/flood/flood_metadata.json
      provenance + grid parameters
  data/flood/ATTRIBUTION.md
      ODbL attribution text

The raster bytes are deterministic for a fixed source shapefile — only
"built_at" in the metadata varies between runs.

Build-time only: needs pyshp + shapely. The runtime consumer
(phfares/flood.py) is stdlib-only and reads only the produced artifacts.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import shapefile
from shapely.geometry import Point, Polygon

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "data" / "flood" / "src"
_SHP_PATH = _SRC_DIR / "MetroManila_Flood_100year.shp"
_OUT_DIR = _REPO_ROOT / "data" / "flood"
_GRID_OUT = _OUT_DIR / "noah_mm_flood_grid.bin.gz"
_META_OUT = _OUT_DIR / "flood_metadata.json"
_ATTR_OUT = _OUT_DIR / "ATTRIBUTION.md"

_LEVELS = {"1": "LOW", "2": "MEDIUM", "3": "HIGH"}

# Verified grid parameters (20 m cell; validated 25/25 against the NOAH
# production oracle).  Keep EXACT — changing these breaks the oracle.
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = (
    120.90673880071594,
    14.351790725957223,
    121.13503641400004,
    14.784375978796906,
)
DLAT = 20.0 / 110574.0
DLON = 20.0 / (111320.0 * math.cos(math.radians((LAT_MIN + LAT_MAX) / 2)))
NROWS = math.ceil((LAT_MAX - LAT_MIN) / DLAT)
NCOLS = math.ceil((LON_MAX - LON_MIN) / DLON)

_ATTRIBUTION = """# Flood hazard data attribution

Flood hazard data (c) Project NOAH / UP Resilience Institute — "Metro Manila
100yr flood hazard" (100-year rain-return flood inundation layer, Var:
1=LOW 0-0.5 m, 2=MEDIUM 0.5-1.5 m, 3=HIGH >1.5 m).

Licensed under the Open Data Commons Open Database License (ODC-ODbL) v1.0.

Source:
  https://drive.google.com/drive/folders/1ALE4-E9c-4AGjm1fqiPprWHrLUskeY9o
License text:
  https://opendatacommons.org/licenses/odbl/1-0/

This product includes database content derived from Project NOAH / UP
Resilience Institute flood hazard data (ODbL). You are free to share and
adapt the content provided you attribute the source and share-alike any
adapted database.
"""


def _rings(shape):
    """Yield the closed rings of a pyshp Polygon shape as point lists."""
    points = shape.points
    parts = shape.parts
    for k, start in enumerate(parts):
        end = parts[k + 1] if k + 1 < len(parts) else len(points)
        yield points[start:end]


def build_grid(reader) -> bytearray:
    """Rasterize all records; cell value = max Var containing cell centre."""
    grid = bytearray(NROWS * NCOLS)
    for rec in reader.iterShapeRecords():
        var = int(round(float(rec.record.as_dict()["Var"])))
        if var <= 0:
            continue
        for ring in _rings(rec.shape):
            if len(ring) < 4:
                continue
            poly = Polygon(ring)
            minx, miny, maxx, maxy = poly.bounds
            r0 = max(0, int(math.floor((miny - LAT_MIN) / DLAT)) - 1)
            r1 = min(NROWS - 1, int(math.ceil((maxy - LAT_MIN) / DLAT)) + 1)
            c0 = max(0, int(math.floor((minx - LON_MIN) / DLON)) - 1)
            c1 = min(NCOLS - 1, int(math.ceil((maxx - LON_MIN) / DLON)) + 1)
            for r in range(r0, r1 + 1):
                lat = LAT_MIN + (r + 0.5) * DLAT
                if not (miny <= lat <= maxy):
                    continue
                for c in range(c0, c1 + 1):
                    lon = LON_MIN + (c + 0.5) * DLON
                    if not (minx <= lon <= maxx):
                        continue
                    idx = r * NCOLS + c
                    if grid[idx] < var and poly.contains(Point(lon, lat)):
                        grid[idx] = var
    return grid


def main() -> int:
    reader = shapefile.Reader(str(_SHP_PATH))
    grid = build_grid(reader)
    raw = bytes(grid)
    if len(raw) != NROWS * NCOLS:
        raise RuntimeError("raster size mismatch")
    with gzip.GzipFile(filename="", mode="wb", fileobj=_GRID_OUT.open("wb"),
                      compresslevel=9, mtime=0) as gz:
        gz.write(raw)

    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    meta = {
        "source": "Project NOAH (UP Resilience Institute)",
        "dataset": "Metro Manila 100yr flood hazard",
        "source_url": "https://drive.google.com/drive/folders/1ALE4-E9c-4AGjm1fqiPprWHrLUskeY9o",
        "license": "ODC-ODbL-1.0",
        "as_of": "2024-08-24",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_script_sha256": script_sha,
        "bbox": [LON_MIN, LAT_MIN, LON_MAX, LAT_MAX],
        "cell_deg": [DLON, DLAT],
        "shape": [NROWS, NCOLS],
        "levels": _LEVELS,
    }
    _META_OUT.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    _ATTR_OUT.write_text(_ATTRIBUTION, encoding="utf-8")

    gz_size = _GRID_OUT.stat().st_size
    print(f"grid:   {_GRID_OUT} ({gz_size} bytes, shape=({NROWS}, {NCOLS}))")
    print(f"meta:   {_META_OUT}")
    print(f"attr:   {_ATTR_OUT}")
    print(f"sha256: {script_sha}")
    if gz_size >= 5 * 1024 * 1024:
        print("ERROR: grid exceeds 5 MB", file=sys.stderr)
        return 1
    if len(raw) != NROWS * NCOLS:
        print("ERROR: raster size mismatch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())