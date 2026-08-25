# ph-fares — Project Charter

**Open-source Philippine transit fare data, piloting in Mandaluyong City.**

## Problem

Ride-hailing apps (Grab, Angkas, Move It) keep fare data closed and in-app. The community Metro Manila GTFS feed (`sakayph/gtfs`) has routes and stops but **no `fares.txt`** — the fare layer is missing from the only established open dataset. Commuters have no way to compare fares before they leave.

Fare structures for franchised modes (jeepney, bus) are LTFRB-regulated public facts. Encoding them is legal and useful.

## Goals / Non-Goals

**Pilot goals (Mandaluyong only):**

- A versioned, machine-readable fare dataset (`data/fares.json`) for modes relevant to a Mandaluyong commuter.
- A tiny Python library (`phfares`) answering "how much does it cost for mode X over distance Y?"
- A GTFS `fares.txt` generator (`tools/gtfs_fares.py`) — the open-contribution artifact.
- A mobile-first single-page pilot UI (`web/`) for comparing fares across modes.

**Non-goals:**

- Full map integration or route planning.
- Live fare tracking or real-time data.
- Angkas, Move It, or Grab fare numbers — no published data exists to encode.

## Target Users

- **Commuters** — via the pilot web UI, to compare fares before they leave.
- **Developers** — via the `phfares` Python library, to build fare-aware tools.
- **Open-transit community** — via the `fares.txt` artifact contributed back to `sakayph/gtfs`.

## Success Criteria

1. Dataset (`data/fares.json`) with every entry carrying `source`, `as_of`, and `status`.
2. Library (`phfares`) with passing tests covering the specified fare cases and edge cases.
3. GTFS artifact (`tools/gtfs_fares.py`) producing valid `fares.txt` output.
4. Pilot UI (`web/`) usable at mobile width (375px), showing mode comparison for preset trips.
5. Published to GitHub as `roval15/ph-fares` under MIT license.

## Data Rules

Every fare entry in the dataset **must** carry `source` (URL or "LTFRB MC &lt;no&gt;"), `as_of` (date), and `status`. Unverified entries carry `status: unverified`. QA checks this mechanically. Angkas, Move It, and Grab are excluded — no published data exists to reference.

**Honesty principle:** fares are estimates derived from LTFRB formulas and publicly available sources. LTFRB circulars supersede anything in this dataset.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Fare data drifts over time (LTFRB adjustments) | Every entry carries `as_of` dating; community PRs welcomed to update figures |
| LTFRB circulars supersede estimates in this dataset | Stated honestly in README and this charter — always cite the source |
| GTFS feed quality may be stale or incomplete | Bbox sanity checks on route extraction; synthetic stop tests in suite |
| MRT-3 fare table citation may be hard to verify | Include with `status: unverified` if source cannot be pinned; document the gap |

## Open Questions

- **MRT-3 citation status:** Fare table must be verified against a citable source (Wikipedia, LRTA news). If unverifiable, the entry ships as `status: unverified`.
- **Future crowd-sourced tier:** Motorcycle-app fares (Angkas, Move It, Grab) could be crowd-sourced once a submission pipeline exists. Out of scope for this pilot.

## License

MIT. See `LICENSE` in repo root.
