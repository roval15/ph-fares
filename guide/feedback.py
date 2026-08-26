"""Feedback ledger, freshness engine and steward attribution.

Community feedback on route quality is stored as an append-only JSONL
ledger under ``data/community_updates.jsonl``.  The freshness engine
ranks routes from **green** (frequently confirmed) to **gray** (never
confirmed) so that S7.2 can surface reliable options first.

Privacy note
------------
Aliases are **public free text** — no other personally-identifiable
information is collected.  Corrections and disputes are **never**
auto-applied to the dataset; they are stored, surfaced, and ranked for
later human or swarm review.

Level-B reuse
-------------
``freshness`` is consumed by the web layer (S7.2) to sink disputed
options below equal-fare options and to surface the route steward
label.
"""

from __future__ import annotations

import json
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEDGER_PATH = _DATA_DIR / "community_updates.jsonl"

_VALID_KINDS = frozenset({"confirm", "dispute", "note"})


# ---------------------------------------------------------------------------
# Clock helper (patchable in tests)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return current UTC time.  Patchable for deterministic tests."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------

def append_feedback(
    route_id: str,
    kind: str,
    alias: str | None = None,
    note: str | None = None,
    fingerprint: str | None = None,
) -> dict:
    """Append exactly one feedback record to the ledger.

    Parameters
    ----------
    route_id:
        The route this feedback is about.
    kind:
        One of ``"confirm"``, ``"dispute"``, or ``"note"``.
    alias:
        Optional free-text public alias of the contributor.
    note:
        Optional free-text note (only meaningful for kind="note").
    fingerprint:
        Optional hex identifier for de-duplication.  A ``uuid4`` hex is
        generated when omitted.

    Returns
    -------
    dict
        The record that was written (includes generated ``ts`` and
        ``fingerprint``).

    Raises
    ------
    ValueError
        If *kind* is not one of the three valid values.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"Invalid kind {kind!r}; must be one of {sorted(_VALID_KINDS)}"
        )

    ts = _now().isoformat()
    fp = fingerprint if fingerprint else uuid.uuid4().hex

    record: dict = {
        "ts": ts,
        "route_id": route_id,
        "kind": kind,
    }
    if alias is not None:
        record["alias"] = alias
    if note is not None:
        record["note"] = note
    record["fingerprint"] = fp

    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_feedback() -> list[dict]:
    """Load all valid records from the ledger.

    Missing file returns an empty list.  Malformed lines are skipped
    with a warning (including the 1-based line number) and never cause
    an exception.  A valid line is a JSON object with non-empty
    ``route_id``, ``ts``, ``fingerprint``, and ``kind`` fields.
    """
    if not _LEDGER_PATH.exists():
        return []

    records: list[dict] = []
    with open(_LEDGER_PATH, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                warnings.warn(
                    f"Malformed feedback line {lineno}: not valid JSON",
                    stacklevel=2,
                )
                continue
            if not isinstance(obj, dict):
                warnings.warn(
                    f"Malformed feedback line {lineno}: not a JSON object",
                    stacklevel=2,
                )
                continue
            if not all(obj.get(k) for k in ("route_id", "ts", "fingerprint", "kind")):
                warnings.warn(
                    f"Malformed feedback line {lineno}: missing required fields",
                    stacklevel=2,
                )
                continue
            records.append(obj)
    return records


# ---------------------------------------------------------------------------
# De-duplication helpers
# ---------------------------------------------------------------------------

def _calendar_day(ts_str: str) -> str:
    """Return the UTC calendar-day string ``YYYY-MM-DD`` for an ISO timestamp."""
    return datetime.fromisoformat(ts_str).astimezone(timezone.utc).strftime("%Y-%m-%d")


def _dedupe_records(records: list[dict]) -> list[dict]:
    """Remove same-(route_id, fingerprint, kind)-same-day duplicates.

    The *first* occurrence wins; the ledger stays append-only.
    """
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict] = []
    for r in records:
        key = (r["route_id"], r["fingerprint"], r["kind"], _calendar_day(r["ts"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Freshness engine
# ---------------------------------------------------------------------------

def freshness(route_id: str) -> dict:
    """Return freshness info for *route_id*.

    Returns
    -------
    dict
        ``{tier, confirmations, disputes, last_confirmed, stewards}``

    Tier precedence (when multiple match):
        green → disputed → yellow → gray

    * **green** — ≥ 3 deduped confirmations within 30 days **and**
      disputes ≤ confirmations within those 30 days.
    * **disputed** — deduped disputes > deduped confirms within the
      last 30 days.
    * **yellow** — ≥ 1 deduped confirmation within 90 days.
    * **gray** — never confirmed (the 2017-feed default).  Also the
      default for an unknown *route_id*.

    The ``stewards`` list ranks aliases by deduped confirmation count
    (descending), ties broken alphabetically.  Blank/None/whitespace
    aliases are excluded.
    """
    all_records = load_feedback()
    route_records = [r for r in all_records if r["route_id"] == route_id]
    deduped = _dedupe_records(route_records)

    confirms = [r for r in deduped if r["kind"] == "confirm"]
    disputes = [r for r in deduped if r["kind"] == "dispute"]

    now = _now()

    # --- Windows (30 days and 90 days) ---
    def _within(dt_str: str, days: int) -> bool:
        rec_time = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
        return (now - rec_time).days <= days

    confirms_30 = [r for r in confirms if _within(r["ts"], 30)]
    disputes_30 = [r for r in disputes if _within(r["ts"], 30)]
    confirms_90 = [r for r in confirms if _within(r["ts"], 90)]

    n_confirms_30 = len(confirms_30)
    n_disputes_30 = len(disputes_30)
    n_confirms_90 = len(confirms_90)

    # last_confirmed
    last_confirmed: str | None = None
    if confirms:
        last_confirmed = max(r["ts"] for r in confirms)

    # stewards
    alias_counts: dict[str, int] = {}
    for r in confirms:
        alias = (r.get("alias") or "").strip()
        if not alias:
            continue
        alias_counts[alias] = alias_counts.get(alias, 0) + 1
    stewards = sorted(
        [{"alias": a, "confirmations": c} for a, c in alias_counts.items()],
        key=lambda s: (-s["confirmations"], s["alias"]),
    )

    # --- Tier determination ---
    tier = "gray"
    if n_confirms_30 >= 3 and n_disputes_30 <= n_confirms_30:
        tier = "green"
    elif n_disputes_30 > n_confirms_30:
        tier = "disputed"
    elif n_confirms_90 >= 1:
        tier = "yellow"

    return {
        "tier": tier,
        "confirmations": len(confirms),
        "disputes": len(disputes),
        "last_confirmed": last_confirmed,
        "stewards": stewards,
    }
