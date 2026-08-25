"""Extract Mandaluyong jeepney routes from the sakayph/gtfs community feed.

Source feed: https://github.com/sakayph/gtfs (GTFS static data for Philippine
public-transport routes).

Files read (from the feed directory):
  - agency.txt    — agency_id lookup (jeepney = agency_id "LTFRB")
  - routes.txt    — route metadata (route_type, route_long_name, etc.)
  - trips.txt     — maps trip_id -> route_id
  - stop_times.txt — maps trip_id -> stop_id
  - stops.txt     — stop_id -> stop_lat, stop_lon

"Jeepney" identification:
  There is no explicit "jeepney" label in the feed.  Jeepneys are road-based
  public-utility buses operated under LTFRB, so we select rows where
  route_type == 3 (bus) AND agency_id == "LTFRB".

Bounding box (Mandaluyong area from SPEC.md §Deliverables #1):
  [min_lat=14.555, min_lon=121.025, max_lat=14.600, max_lon=121.065]

Output: JSON list of {"route_id", "route_long_name", "n_stops_in_bbox"}
        sorted by n_stops_in_bbox descending.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

# Mandaluyong bounding box (inclusive)
MIN_LAT = 14.555
MIN_LON = 121.025
MAX_LAT = 14.600
MAX_LON = 121.065

SANITY_KEYWORDS = ["shaw", "edsa", "boni", "ortigas"]


def _read_csv(path: Path):
    """Yield dicts from a GTFS CSV file (quoted fields)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        yield from reader


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract jeepney routes with stops inside the Mandaluyong bounding box."
    )
    parser.add_argument(
        "--feed-dir",
        type=Path,
        default=Path("/tmp/sakayph-gtfs-t18ced628"),
        help="Path to the cloned sakayph/gtfs feed (default: /tmp/sakayph-gtfs-t18ced628)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/routes_mandaluyong.json"),
        help="Output JSON path (default: data/routes_mandaluyong.json)",
    )
    args = parser.parse_args()
    feed: Path = args.feed_dir
    out: Path = args.output

    # --- 1. Load jeepney routes (route_type=3, agency_id=LTFRB) ---------------
    jeepney_routes: dict[str, str] = {}  # route_id -> route_long_name
    for row in _read_csv(feed / "routes.txt"):
        if row["agency_id"] == "LTFRB" and row["route_type"] == "3":
            jeepney_routes[row["route_id"]] = row["route_long_name"]

    if not jeepney_routes:
        print("ERROR: no LTFRB route_type=3 routes found", file=sys.stderr)
        sys.exit(1)

    # --- 2. Build stop_id -> (lat, lon) from stops.txt ------------------------
    stop_coords: dict[str, tuple[float, float]] = {}
    for row in _read_csv(feed / "stops.txt"):
        try:
            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
        except (ValueError, KeyError):
            continue
        stop_coords[row["stop_id"]] = (lat, lon)

    # --- 3. Map trip_id -> route_id from trips.txt ---------------------------
    trip_route: dict[str, str] = {}
    for row in _read_csv(feed / "trips.txt"):
        rid = row.get("route_id")
        if rid in jeepney_routes:
            trip_route[row["trip_id"]] = rid

    # --- 4. Collect stops per route via stop_times.txt -----------------------
    route_stops: dict[str, set[str]] = {rid: set() for rid in jeepney_routes}

    for row in _read_csv(feed / "stop_times.txt"):
        tid = row.get("trip_id")
        if tid in trip_route:
            rid = trip_route[tid]
            sid = row.get("stop_id")
            if sid:
                route_stops[rid].add(sid)

    # --- 5. Count stops inside the bounding box per route --------------------
    results: list[dict] = []
    for rid, stops in route_stops.items():
        n = sum(
            1
            for sid in stops
            if sid in stop_coords
            and MIN_LAT <= stop_coords[sid][0] <= MAX_LAT
            and MIN_LON <= stop_coords[sid][1] <= MAX_LON
        )
        if n >= 1:
            results.append(
                {
                    "route_id": rid,
                    "route_long_name": jeepney_routes[rid],
                    "n_stops_in_bbox": n,
                }
            )

    # Sort deterministically: by n_stops_in_bbox DESC, then route_id ASC for stability
    results.sort(key=lambda r: (-r["n_stops_in_bbox"], r["route_id"]))

    # --- 6. Sanity gate ------------------------------------------------------
    if not results:
        print(
            "ERROR: output would be empty — no jeepney routes have stops in the bbox",
            file=sys.stderr,
        )
        sys.exit(1)

    has_keyword = any(
        kw in entry["route_long_name"].lower()
        for entry in results
        for kw in SANITY_KEYWORDS
    )
    if not has_keyword:
        print(
            "ERROR: no route_long_name contains Shaw/EDSA/Boni/Ortigas — "
            "output may be incorrect",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- 7. Write output (idempotent) ----------------------------------------
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")  # trailing newline for clean diffs

    print(f"Wrote {len(results)} routes to {out}")


if __name__ == "__main__":
    main()
