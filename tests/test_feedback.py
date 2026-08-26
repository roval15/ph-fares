"""Tests for guide.feedback — all with synthetic fixtures, no real data."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone

import pytest

import guide.feedback as fb_mod


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_ledger(tmp_path, monkeypatch):
    """Redirect feedback ledger to a temp file for every test."""
    monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "community_updates.jsonl")


def _fake_now(dt: datetime):
    """Return a callable that monkeypatch._now to always return *dt*."""
    return lambda: dt


def _write_raw(path, lines: list[str]):
    """Write raw lines to the ledger file (for malformed-line tests)."""
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def _iso(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> str:
    """Shorthand for an ISO-8601 UTC timestamp."""
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# append_feedback tests
# ---------------------------------------------------------------------------

class TestAppendFeedback:
    def test_round_trip_generates_ts_and_fingerprint(self, monkeypatch):
        now = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        rec = fb_mod.append_feedback("R1", "confirm", alias="alice")
        assert rec["route_id"] == "R1"
        assert rec["kind"] == "confirm"
        assert rec["ts"] == now.isoformat()
        assert len(rec["fingerprint"]) == 32  # uuid4 hex
        assert rec["alias"] == "alice"

        loaded = fb_mod.load_feedback()
        assert len(loaded) == 1
        assert loaded[0] == rec

    def test_custom_fingerprint_and_note(self, monkeypatch):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        rec = fb_mod.append_feedback(
            "R2", "note", alias="bob", note="looks good", fingerprint="abc123"
        )
        assert rec["fingerprint"] == "abc123"
        assert rec["note"] == "looks good"

    def test_rejects_invalid_kind_without_touching_file(self, monkeypatch):
        monkeypatch.setattr(fb_mod, "_now", _fake_now(datetime.now(timezone.utc)))
        path = fb_mod._LEDGER_PATH
        assert not path.exists()
        with pytest.raises(ValueError, match="Invalid kind"):
            fb_mod.append_feedback("R1", "bogus")
        assert not path.exists()

    def test_rejects_empty_kind(self, monkeypatch):
        monkeypatch.setattr(fb_mod, "_now", _fake_now(datetime.now(timezone.utc)))
        with pytest.raises(ValueError, match="Invalid kind"):
            fb_mod.append_feedback("R1", "")

    def test_alias_not_written_when_none(self, monkeypatch):
        monkeypatch.setattr(fb_mod, "_now", _fake_now(datetime.now(timezone.utc)))
        rec = fb_mod.append_feedback("R1", "confirm")
        assert "alias" not in rec


# ---------------------------------------------------------------------------
# load_feedback — malformed-line tolerance
# ---------------------------------------------------------------------------

class TestLoadFeedbackMalformed:
    def test_mixed_valid_and_garbage(self, monkeypatch):
        path = fb_mod._LEDGER_PATH
        good = json.dumps({
            "ts": _iso(2026, 3, 1), "route_id": "R1",
            "kind": "confirm", "fingerprint": "a1",
        })
        garbage_lines = [
            "not json at all",
            "[1, 2, 3]",                       # JSON array, not object
            json.dumps({"ts": _iso(2026, 3, 1)}),  # missing route_id
            json.dumps({"ts": "", "route_id": "R1", "kind": "confirm", "fingerprint": "x"}),  # empty ts
            "",
            "   ",
        ]
        _write_raw(path, [good] + garbage_lines + [good])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            records = fb_mod.load_feedback()

        assert len(records) == 2
        assert all(r["route_id"] == "R1" for r in records)
        # 4 garbage lines emit warnings; empty/blank lines are silently skipped
        assert len(w) >= 4

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fb_mod, "_LEDGER_PATH", tmp_path / "nonexistent.jsonl")
        assert fb_mod.load_feedback() == []


# ---------------------------------------------------------------------------
# Freshness tier tests
# ---------------------------------------------------------------------------

class TestFreshnessTier:
    def test_green_exactly_3_confirms_in_30d(self, monkeypatch):
        """3 confirms within 30d, no disputes → green."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fp{i}", alias=f"u{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "green"
        assert result["confirmations"] == 3

    def test_green_boundary_disputes_equal_confirms(self, monkeypatch):
        """3 confirms and 3 disputes within 30d → still green (disputes ≤ confirms)."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fc{i}", alias=f"c{i}")
            fb_mod.append_feedback("R1", "dispute", fingerprint=f"fd{i}", alias=f"d{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "green"

    def test_disputed_more_disputes_than_confirms(self, monkeypatch):
        """3 confirms but 4 disputes within 30d → disputed."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fc{i}", alias=f"c{i}")
        for i in range(4):
            fb_mod.append_feedback("R1", "dispute", fingerprint=f"fd{i}", alias=f"d{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "disputed"

    def test_yellow_1_confirm_31_to_90d(self, monkeypatch):
        """1 confirm 45 days ago → yellow."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        past = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(past))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="u1")
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        result = fb_mod.freshness("R1")
        assert result["tier"] == "yellow"

    def test_yellow_exactly_1_confirm_just_outside_30d(self, monkeypatch):
        """1 confirm 31 days ago → yellow (not green)."""
        from datetime import timedelta
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        past = now - timedelta(days=31)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(past))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="u1")
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        result = fb_mod.freshness("R1")
        assert result["tier"] == "yellow"

    def test_gray_never_confirmed(self, monkeypatch):
        """No confirms ever → gray."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        result = fb_mod.freshness("NONEXISTENT_R1")
        assert result["tier"] == "gray"
        assert result["confirmations"] == 0
        assert result["disputes"] == 0

    def test_dispute_only_is_disputed_not_gray(self, monkeypatch):
        """Only disputes, no confirms → disputed (disputes > 0 confirms within 30d)."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "dispute", fingerprint="fp1", alias="u1")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "disputed"
        assert result["confirmations"] == 0
        assert result["disputes"] == 1

    def test_gray_unknown_route(self, monkeypatch):
        """Unknown route_id → gray defaults."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        result = fb_mod.freshness("NONEXISTENT")
        assert result["tier"] == "gray"
        assert result["confirmations"] == 0
        assert result["disputes"] == 0
        assert result["last_confirmed"] is None
        assert result["stewards"] == []

    def test_green_not_when_disputes_exceed_in_30d(self, monkeypatch):
        """4 confirms but 5 disputes in 30d → disputed, not green."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(4):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fc{i}", alias=f"c{i}")
        for i in range(5):
            fb_mod.append_feedback("R1", "dispute", fingerprint=f"fd{i}", alias=f"d{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "disputed"

    def test_disputed_beats_yellow(self, monkeypatch):
        """Dispute-dominant window → disputed even with a recent confirm."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        # 1 confirm, 2 disputes within 30d
        fb_mod.append_feedback("R1", "confirm", fingerprint="fc1", alias="c1")
        fb_mod.append_feedback("R1", "dispute", fingerprint="fd1", alias="d1")
        fb_mod.append_feedback("R1", "dispute", fingerprint="fd2", alias="d2")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "disputed"


# ---------------------------------------------------------------------------
# De-duplication tests
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_same_route_fingerprint_kind_same_day_counts_once(self, monkeypatch):
        now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for _ in range(5):
            fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        result = fb_mod.freshness("R1")
        assert result["confirmations"] == 1
        # Physical lines = 5 (append-only)
        loaded = fb_mod.load_feedback()
        assert len(loaded) == 5

    def test_different_fingerprint_same_day_counts_again(self, monkeypatch):
        now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp2", alias="bob")
        result = fb_mod.freshness("R1")
        assert result["confirmations"] == 2

    def test_same_fingerprint_different_day_counts_again(self, monkeypatch):
        day1 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(day1))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        monkeypatch.setattr(fb_mod, "_now", _fake_now(day2))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        result = fb_mod.freshness("R1")
        assert result["confirmations"] == 2

    def test_notes_never_affect_counts(self, monkeypatch):
        now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(5):
            fb_mod.append_feedback("R1", "note", fingerprint=f"fp{i}", note="hi")
        result = fb_mod.freshness("R1")
        assert result["confirmations"] == 0
        assert result["disputes"] == 0

    def test_dedupe_only_affects_counts_not_appending(self, monkeypatch):
        """Same-day duplicate still appends a physical line."""
        now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1")
        loaded = fb_mod.load_feedback()
        assert len(loaded) == 2  # append-only


# ---------------------------------------------------------------------------
# Steward attribution tests
# ---------------------------------------------------------------------------

class TestStewards:
    def test_top_confirmer_wins(self, monkeypatch):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fp{i}", alias="alice")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp5", alias="bob")
        result = fb_mod.freshness("R1")
        assert len(result["stewards"]) == 2
        assert result["stewards"][0]["alias"] == "alice"
        assert result["stewards"][0]["confirmations"] == 3
        assert result["stewards"][1]["alias"] == "bob"
        assert result["stewards"][1]["confirmations"] == 1

    def test_tie_broken_alphabetically(self, monkeypatch):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="charlie")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp2", alias="alice")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp3", alias="bob")
        result = fb_mod.freshness("R1")
        aliases = [s["alias"] for s in result["stewards"]]
        assert aliases == ["alice", "bob", "charlie"]

    def test_blank_aliases_excluded(self, monkeypatch):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1", alias="alice")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp2")
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp3", alias="  ")
        result = fb_mod.freshness("R1")
        assert len(result["stewards"]) == 1
        assert result["stewards"][0]["alias"] == "alice"


# ---------------------------------------------------------------------------
# Freshness — last_confirmed
# ---------------------------------------------------------------------------

class TestLastConfirmed:
    def test_last_confirmed_returns_most_recent(self, monkeypatch):
        day1 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(day1))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp1")
        monkeypatch.setattr(fb_mod, "_now", _fake_now(day2))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp2")
        monkeypatch.setattr(fb_mod, "_now", _fake_now(day2))
        result = fb_mod.freshness("R1")
        assert result["last_confirmed"] == day2.isoformat()

    def test_last_confirmed_none_when_no_confirms(self, monkeypatch):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        fb_mod.append_feedback("R1", "dispute", fingerprint="fp1")
        result = fb_mod.freshness("R1")
        assert result["last_confirmed"] is None


# ---------------------------------------------------------------------------
# Tier precedence edge cases
# ---------------------------------------------------------------------------

class TestTierPrecedence:
    def test_green_and_disputed_cant_both_happen(self, monkeypatch):
        """Green requires disputes ≤ confirms; if disputes > confirms, it's disputed."""
        # This is tested implicitly by test_green_boundary_disputes_equal_confirms
        # and test_disputed_more_disputes_than_confirms. Verify the precedence:
        # green → disputed → yellow → gray
        # If green doesn't apply (disputes > confirms), disputed takes over.
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        # 3 confirms, 4 disputes within 30d → disputed (not green)
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fc{i}", alias=f"c{i}")
        for i in range(4):
            fb_mod.append_feedback("R1", "dispute", fingerprint=f"fd{i}", alias=f"d{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "disputed"

    def test_yellow_then_becomes_green(self, monkeypatch):
        """Old confirm → yellow; add more recent confirms → green."""
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        # 1 confirm 60 days ago → yellow
        past = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(fb_mod, "_now", _fake_now(past))
        fb_mod.append_feedback("R1", "confirm", fingerprint="fp_old", alias="u1")
        # Now add 3 recent confirms
        monkeypatch.setattr(fb_mod, "_now", _fake_now(now))
        for i in range(3):
            fb_mod.append_feedback("R1", "confirm", fingerprint=f"fp{i}", alias=f"u{i}")
        result = fb_mod.freshness("R1")
        assert result["tier"] == "green"
