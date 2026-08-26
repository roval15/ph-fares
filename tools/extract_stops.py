"""Extract Mandaluyong stops, stop-sequences, and MRT-3 stations from the sakayph/gtfs feed.

Source feed: https://github.com/sakayph/gtfs (GTFS static data for Philippine
public-transport routes).

Bounding box — the Mandaluyong pilot area from SPEC.md with a 0.02° margin:
  base bbox:  [min_lat=14.555, min_lon=121.025, max_lat=14.600, max_lon=121.065]
  expanded:   [14.535, 121.005, 14.620, 121.085]

Outputs (all written to --data-dir, default ``data/``):
  stops_mandaluyong.json   — every LTFRB-jeepney-served stop inside the expanded bbox
  stop_sequences.json      — per-route representative stop sequences for all 334 routes
  mrt3_stations.json       — MRT-3 (rail) station list ordered by stop_sequence
"""

import argparse
import csv
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------

# Base Mandaluyong bounding box (inclusive)
MIN_LAT = 14.555
MIN_LON = 121.025
MAX_LAT = 14.600
MAX_LON = 121.065

MARGIN = 0.02
EXP_MIN_LAT = MIN_LAT - MARGIN  # 14.535
EXP_MIN_LON = MIN_LON - MARGIN  # 121.005
EXP_MAX_LAT = MAX_LAT + MARGIN  # 14.620
EXP_MAX_LON = MAX_LON + MARGIN  # 121.085


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_csv(path: Path):
    """Yield dicts from a GTFS CSV file (quoted fields)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        yield from reader


def _in_expanded_bbox(lat: float, lon: float) -> bool:
    return EXP_MIN_LAT <= lat <= EXP_MAX_LAT and EXP_MIN_LON <= lon <= EXP_MAX_LON


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract stops, stop sequences, and MRT-3 stations from the "
            "sakayph/gtfs feed."
        )
    )
    parser.add_argument(
        "--feed-dir",
        type=Path,
        default=Path("/tmp/sakayph-gtfs-t18ced628"),
        help="Path to the cloned sakayph/gtfs feed.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory to write output JSON files (default: data).",
    )
    args = parser.parse_args()
    feed: Path = args.feed_dir
    data_dir: Path = args.data_dir

    # ------------------------------------------------------------------
    # 1. Load jeepney routes (route_type=3, agency_id=LTFRB)
    # ------------------------------------------------------------------
    jeepney_route_names: dict[str, str] = {}  # route_id -> route_long_name
    for row in _read_csv(feed / "routes.txt"):
        if row["agency_id"] == "LTFRB" and row["route_type"] == "3":
            jeepney_route_names[row["route_id"]] = row["route_long_name"]

    if not jeepney_route_names:
        print("ERROR: no LTFRB route_type=3 routes found", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Build stop_id -> (lat, lon, name) from stops.txt
    # ------------------------------------------------------------------
    stop_info: dict[str, tuple[float, float, str]] = {}
    for row in _read_csv(feed / "stops.txt"):
        try:
            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
        except (ValueError, KeyError):
            continue
        stop_info[row["stop_id"]] = (lat, lon, row.get("stop_name", ""))

    # ------------------------------------------------------------------
    # 3. Map trip_id -> route_id from trips.txt
    # ------------------------------------------------------------------
    trip_route: dict[str, str] = {}
    for row in _read_csv(feed / "trips.txt"):
        rid = row.get("route_id")
        if rid in jeepney_route_names:
            trip_route[row["trip_id"]] = rid

    # ------------------------------------------------------------------
    # 4. Read stop_times.txt — build per-trip stop lists and per-stop
    #    route_ids
    # ------------------------------------------------------------------
    # trip_id -> list of (int_seq, stop_id, sdt_value)
    trip_stops: dict[str, list[tuple[int, str, str]]] = {}
    # stop_id -> set of jeepney route_ids serving it
    stop_route_ids: dict[str, set[str]] = {}

    for row in _read_csv(feed / "stop_times.txt"):
        tid = row.get("trip_id", "")
        sid = row.get("stop_id", "")
        seq_raw = row.get("stop_sequence", "0")
        try:
            seq = int(seq_raw)
        except ValueError:
            seq = 0
        sdt = row.get("shape_dist_traveled", "").strip()

        # Per-trip stop list
        if tid in trip_route:
            trip_stops.setdefault(tid, []).append((seq, sid, sdt))
            rid = trip_route[tid]
            stop_route_ids.setdefault(sid, set()).add(rid)

    # ------------------------------------------------------------------
    # 5. stops_mandaluyong.json — stops inside expanded bbox that are
    #    served by at least one jeepney route
    # ------------------------------------------------------------------
    stops_list: list[dict] = []
    for sid, (lat, lon, name) in stop_info.items():
        if not _in_expanded_bbox(lat, lon):
            continue
        rids = sorted(stop_route_ids.get(sid, set()))
        if not rids:
            continue
        stops_list.append(
            {
                "stop_id": sid,
                "stop_name": name,
                "lat": lat,
                "lon": lon,
                "route_ids": rids,
            }
        )

    stops_list.sort(key=lambda s: s["stop_id"])
    _write_json(data_dir / "stops_mandaluyong.json", stops_list)

    if not stops_list:
        print("ERROR: stops_mandaluyong.json would be empty", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(stops_list)} stops to {data_dir / 'stops_mandaluyong.json'}")

    # ------------------------------------------------------------------
    # 6. stop_sequences.json — per-route representative trip
    # ------------------------------------------------------------------
    # Build the representative trip for each route:
    #   most stop_time rows → tie-break lexicographically smallest trip_id
    route_best_trip: dict[str, tuple[int, str]] = {}  # rid -> (count, trip_id)
    for tid, rid in trip_route.items():
        count = len(trip_stops.get(tid, []))
        if count < 1:
            continue
        best = route_best_trip.get(rid)
        if best is None or count > best[0] or (count == best[0] and tid < best[1]):
            route_best_trip[rid] = (count, tid)

    # Load routes_mandaluyong.json to get the canonical route list.
    # If the file doesn't exist (e.g. synthetic fixture), derive the list
    # from the feed itself — all jeepney routes with at least 1 stop_time.
    routes_rm_path = data_dir / "routes_mandaluyong.json"
    if routes_rm_path.exists():
        with routes_rm_path.open(encoding="utf-8") as fh:
            routes_rm = json.load(fh)
    else:
        routes_rm = [
            {"route_id": rid, "route_long_name": jeepney_route_names.get(rid, "")}
            for rid in sorted(jeepney_route_names)
            if rid in route_best_trip
        ]

    sequences: list[dict] = []
    for entry in routes_rm:
        rid = entry["route_id"]
        rname = entry["route_long_name"]
        best = route_best_trip.get(rid)
        stops_out: list[dict] = []
        if best is not None:
            tid = best[1]
            rows = trip_stops.get(tid, [])
            rows.sort(key=lambda r: r[0])  # by stop_sequence
            for seq, sid, sdt in rows:
                info = stop_info.get(sid)
                if info is None:
                    lat, lon, sname = 0.0, 0.0, ""
                else:
                    lat, lon, sname = info
                entry_out: dict = {
                    "stop_id": sid,
                    "stop_name": sname,
                    "lat": lat,
                    "lon": lon,
                }
                if sdt:
                    entry_out["shape_dist_traveled"] = float(sdt)
                stops_out.append(entry_out)

        sequences.append(
            {
                "route_id": rid,
                "route_long_name": rname,
                "stops": stops_out,
            }
        )

    _write_json(data_dir / "stop_sequences.json", sequences)

    if not sequences:
        print("ERROR: stop_sequences.json would be empty", file=sys.stderr)
        sys.exit(1)

    # Coverage warning
    n_with_stops = sum(1 for s in sequences if len(s["stops"]) >= 1)
    coverage_pct = (n_with_stops / len(sequences)) * 100 if sequences else 0
    if coverage_pct < 95:
        print(
            f"WARNING: stop_sequences coverage is {coverage_pct:.1f}% "
            f"({n_with_stops}/{len(sequences)} routes have ≥1 stop)",
            file=sys.stderr,
        )

    print(
        f"Wrote {len(sequences)} route sequences "
        f"({n_with_stops} with stops) to {data_dir / 'stop_sequences.json'}"
    )

    # ------------------------------------------------------------------
    # 7. mrt3_stations.json — rail route with "MRT" in short name
    # ------------------------------------------------------------------
    mrt_route = None
    for row in _read_csv(feed / "routes.txt"):
        if row["route_type"] == "2" and "mrt" in row.get("route_short_name", "").lower():
            mrt_route = row
            break

    if mrt_route is None:
        print("ERROR: no rail route with 'MRT' in short name found", file=sys.stderr)
        sys.exit(1)

    mrt_rid = mrt_route["route_id"]

    # Collect all trips for this route, pick the one with most stops
    mrt_trips: dict[str, int] = {}
    for row in _read_csv(feed / "trips.txt"):
        if row["route_id"] == mrt_rid:
            mrt_trips[row["trip_id"]] = 0

    for row in _read_csv(feed / "stop_times.txt"):
        tid = row.get("trip_id", "")
        if tid in mrt_trips:
            mrt_trips[tid] += 1

    # Pick trip with most stops, tie-break lexicographically smallest trip_id
    best_mrt_trip = None
    best_mrt_count = 0
    for tid in sorted(mrt_trips.keys()):
        count = mrt_trips[tid]
        if count > best_mrt_count:
            best_mrt_count = count
            best_mrt_trip = tid

    # Get the stop sequence for that trip
    mrt_stop_rows: list[tuple[int, str]] = []
    for row in _read_csv(feed / "stop_times.txt"):
        if row.get("trip_id") == best_mrt_trip:
            try:
                seq = int(row.get("stop_sequence", "0"))
            except ValueError:
                seq = 0
            mrt_stop_rows.append((seq, row.get("stop_id", "")))

    mrt_stop_rows.sort(key=lambda r: r[0])

    mrt_stations: list[dict] = []
    for seq, sid in mrt_stop_rows:
        info = stop_info.get(sid)
        if info is None:
            lat, lon, sname = 0.0, 0.0, ""
        else:
            lat, lon, sname = info
        mrt_stations.append(
            {
                "stop_id": sid,
                "stop_name": sname,
                "lat": lat,
                "lon": lon,
            }
        )

    mrt3_output = {
        "route_id": mrt_rid,
        "route_short_name": mrt_route["route_short_name"],
        "route_long_name": mrt_route["route_long_name"],
        "stations": mrt_stations,
    }

    _write_json(data_dir / "mrt3_stations.json", mrt3_output)

    # Sanity gate: must have shaw and boni
    names_norm = {s["stop_name"].lower().strip() for s in mrt_stations}
    has_shaw = any("shaw" in n for n in names_norm)
    has_boni = any("boni" in n for n in names_norm)
    if not (has_shaw and has_boni):
        print(
            "ERROR: MRT-3 sanity gate failed — stations must include "
            "'shaw' and 'boni' (normalized)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Wrote MRT-3 stations ({len(mrt_stations)} stops) to "
        f"{data_dir / 'mrt3_stations.json'}"
    )


if __name__ == "__main__":
    main()
