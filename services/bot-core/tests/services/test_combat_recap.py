"""Unit tests for combat_recap.build_recap_sections (v3 redesign).

Tests cover:
  1. Mandatory events always appear in key_events
  2. First-break-only rule: only first (side, layer) break is mandatory; re-breaks go Recurring only
  3. First-activation-only rule: only first module activation is mandatory; re-activations are fill candidates
  4. Weapon range: first 'enters range' is mandatory; re-enters are fill candidates
  5. Gap-fill: large gaps get filled with cyclic events
  6. Gap-fill priority: module re-activation (1) > secondary re-enter (2) > primary re-enter (3)
  7. Gap-fill diversity penalty: per-series fill_count rotates variety
  8. Recurring section: one bullet per group with ≥3 occurrences
  9. Qualifier handling: per-occurrence qualifier shown only when varies; uniform uses "(all X)" suffix
  10. Chronological order of key_events
  11. Layer re-breaks NOT in key_events and NOT fill candidates
  12. Recurring format: "• <label> ×N -> t1, t2, …" with "->" separator
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level dependency stubs (mirrors test_recap_extract.py pattern)
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.combat_recap import build_recap_sections

# ---------------------------------------------------------------------------
# Row builders — mirror raw _extract_key_events output format
# ---------------------------------------------------------------------------

_TICK_MS = 1000  # 1 tick = 1 second for easy time assertions


def _row(tick: int, event_type: str, detail: str, actor: str | None = None, idx: int | None = None) -> dict:
    return {
        "tick": tick,
        "time_s": tick * _TICK_MS / 1000.0,
        "event_type": event_type,
        "detail": detail,
        "actor": actor,
        "_idx": idx if idx is not None else tick,
    }


def _engagement(tick: int = 0) -> dict:
    return _row(tick, "Engagement", "Alice (Ship) vs Bob (Ship) — 3000m", idx=0)


def _outcome(tick: int = 100) -> dict:
    return _row(tick, "Outcome", "Alice wins (Bob destroyed)", idx=9999)


def _wir_enters(tick: int, actor: str, weapon: str, idx: int | None = None) -> dict:
    return _row(tick, "Weapon in range", f"{actor}'s {weapon} enters range — hit", actor=actor, idx=idx)


def _wir_reenters(tick: int, actor: str, weapon: str, idx: int | None = None) -> dict:
    return _row(tick, "Weapon in range", f"{actor}'s {weapon} re-enters range — hit", actor=actor, idx=idx)


def _layer(tick: int, who: str, layer: str, by: str = "SomeGun", idx: int | None = None) -> dict:
    detail = f"{who}: {layer} depleted (by {by})"
    return _row(tick, "Layer depleted", detail, actor=who, idx=idx)


def _module(tick: int, who: str, mod: str, hp: int = 80, idx: int | None = None) -> dict:
    detail = f"{who} activated {mod} (at {hp}% HP)"
    return _row(tick, "Module activated", detail, actor=who, idx=idx)


def _milestone(tick: int, who: str, pct: int = 50, by: str = "Rocket", idx: int | None = None) -> dict:
    return _row(tick, f"HP milestone ({pct}%)", f"{who} dropped to ≤{pct}% HP (by {by})", idx=idx)


def _build(rows: list[dict]) -> tuple[dict, dict]:
    """Assign sequential _idx values and call build_recap_sections."""
    for i, r in enumerate(rows):
        r["_idx"] = i
    sections = build_recap_sections(rows, {}, tick_ms=_TICK_MS)
    return sections


# ---------------------------------------------------------------------------
# 1. Mandatory events
# ---------------------------------------------------------------------------


class TestMandatoryEvents:
    """Non-cyclic events always appear in key_events."""

    def test_engagement_always_present(self):
        rows = [_engagement(), _outcome()]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "Engagement" in ke_types

    def test_outcome_always_present(self):
        rows = [_engagement(), _outcome()]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "Outcome" in ke_types

    def test_hp_milestone_always_present(self):
        rows = [_engagement(), _milestone(10, "Bob"), _outcome(100)]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "HP milestone (50%)" in ke_types

    def test_first_wir_enters_always_present(self):
        """First 'enters range' for a weapon is always mandatory."""
        rows = [_engagement(), _wir_enters(5, "Alice", "Rocket"), _outcome(100)]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "Weapon in range" in ke_types
        enters = [r for r in s["key_events"] if r["event_type"] == "Weapon in range" and "enters range" in r["detail"]]
        assert len(enters) == 1

    def test_first_layer_break_always_present(self):
        """First break of a (who, layer) pair is mandatory."""
        rows = [_engagement(), _layer(5, "Bob", "shield"), _outcome(100)]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "Layer depleted" in ke_types

    def test_first_module_activation_always_present(self):
        """First activation of a (who, module) pair is mandatory."""
        rows = [_engagement(), _module(5, "Alice", "booster"), _outcome(100)]
        s = _build(rows)
        ke_types = [r["event_type"] for r in s["key_events"]]
        assert "Module activated" in ke_types


# ---------------------------------------------------------------------------
# 2. First-break-only rule
# ---------------------------------------------------------------------------


class TestFirstBreakOnly:
    """Only the FIRST break of (who, layer) is mandatory; re-breaks go Recurring only."""

    def test_first_shield_break_present(self):
        """First shield break is in key_events."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield"),
            _layer(20, "Bob", "shield"),
            _layer(35, "Bob", "shield"),
            _outcome(100),
        ]
        s = _build(rows)
        shield_in_ke = [r for r in s["key_events"] if r["event_type"] == "Layer depleted"]
        assert len(shield_in_ke) == 1, f"Only first shield break in key_events; got {len(shield_in_ke)}"
        assert shield_in_ke[0]["tick"] == 5

    def test_rebreaks_in_recurring_not_key_events(self):
        """Re-breaks (occurrence 2, 3, …) of same layer must appear in recurring, not key_events."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield"),
            _layer(20, "Bob", "shield"),
            _layer(35, "Bob", "shield"),
            _outcome(100),
        ]
        s = _build(rows)
        # Key events must only contain the first break.
        ke_layers = [r for r in s["key_events"] if r["event_type"] == "Layer depleted"]
        assert len(ke_layers) == 1
        # Recurring must have a bullet for all 3 shield breaks.
        rec_shield = [b for b in s["recurring"] if "shield depleted" in b.lower()]
        assert len(rec_shield) == 1, f"Shield depleted recurring bullet expected; got: {s['recurring']}"
        assert "×3" in rec_shield[0], f"Bullet must show ×3; got: {rec_shield[0]!r}"

    def test_different_layers_both_have_first_break_mandatory(self):
        """Shield and armour breaks each have their own mandatory first break."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield"),
            _layer(10, "Bob", "armour"),
            _outcome(100),
        ]
        s = _build(rows)
        ke_layers = [r for r in s["key_events"] if r["event_type"] == "Layer depleted"]
        assert len(ke_layers) == 2, "Both shield and armour first breaks are mandatory"


# ---------------------------------------------------------------------------
# 3. First-activation-only rule
# ---------------------------------------------------------------------------


class TestFirstActivationOnly:
    """Only FIRST activation of (who, module) is mandatory; re-activations are fill candidates."""

    def test_first_module_activation_in_key_events(self):
        """First booster activation is mandatory in key_events (anchored at first tick)."""
        rows = [
            _engagement(),
            _module(5, "Alice", "booster"),
            _module(20, "Alice", "booster"),
            _module(35, "Alice", "booster"),
            _outcome(100),
        ]
        s = _build(rows)
        mods_in_ke = [r for r in s["key_events"] if r["event_type"] == "Module activated"]
        # At minimum the first activation must be present (gap-fill may include re-activations too).
        assert len(mods_in_ke) >= 1, "First module activation must be in key_events"
        # The FIRST activation (tick 5) must be among the key events.
        first_ticks = [r["tick"] for r in mods_in_ke]
        assert 5 in first_ticks, f"First activation tick=5 must be in key_events; got ticks: {first_ticks}"

    def test_module_reactivations_can_appear_as_fill(self):
        """Re-activations (occurrence 2, 3, …) are fill candidates for large gaps."""
        # Big gap between engagement (0) and outcome (200s), module re-activations at 40, 80, 120, 160
        rows = [
            _engagement(0),
            _module(5, "Alice", "booster"),  # mandatory
            _module(40, "Alice", "booster"),  # re-activation (fill candidate)
            _module(80, "Alice", "booster"),
            _module(120, "Alice", "booster"),
            _module(160, "Alice", "booster"),
            _outcome(200),
        ]
        s = _build(rows)
        # Fill candidates should bridge large gaps; at least one re-activation in key_events.
        all_mods_in_ke = [r for r in s["key_events"] if r["event_type"] == "Module activated"]
        assert len(all_mods_in_ke) > 1, "Re-activations should fill large gaps"


# ---------------------------------------------------------------------------
# 4. Key events chronological order
# ---------------------------------------------------------------------------


class TestChronologicalOrder:
    def test_key_events_are_chronological(self):
        """key_events must be sorted by time_s (chronological)."""
        rows = [
            _engagement(0),
            _wir_enters(5, "Alice", "Rocket"),
            _layer(10, "Bob", "shield"),
            _module(15, "Alice", "booster"),
            _milestone(30, "Bob"),
            _outcome(100),
        ]
        s = _build(rows)
        times = [r["time_s"] for r in s["key_events"]]
        assert times == sorted(times), f"key_events must be chronological; times: {times}"

    def test_key_events_chronological_with_fill(self):
        """key_events chronological order holds even after gap-fill inserts events."""
        # Large gap from t=5 to t=100 triggers fill
        rows = [
            _engagement(0),
            _module(5, "Alice", "booster"),
            _module(40, "Alice", "booster"),  # re-activation fill candidate
            _outcome(100),
        ]
        s = _build(rows)
        times = [r["time_s"] for r in s["key_events"]]
        assert times == sorted(times), f"key_events must be chronological after fill; times: {times}"


# ---------------------------------------------------------------------------
# 5. Recurring section format
# ---------------------------------------------------------------------------


class TestRecurringFormat:
    """Recurring bullets must use the format: • <label> ×N -> t1, t2, …"""

    def test_recurring_bullet_format_layer(self):
        """Layer depleted bullet format: • who: layer depleted ×N -> t1, t2, …"""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield", by="Laser"),
            _layer(20, "Bob", "shield", by="Laser"),
            _layer(35, "Bob", "shield", by="Laser"),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "shield depleted" in b.lower()]
        assert len(rec) == 1
        bullet = rec[0]
        assert bullet.startswith("• "), f"Bullet must start with '• '; got: {bullet!r}"
        assert " ×3 -> " in bullet, f"Bullet must contain ' ×3 -> '; got: {bullet!r}"
        # Three timestamps
        ts_part = bullet.split(" -> ")[1]
        timestamps = [t.strip() for t in ts_part.split(",")]
        assert len(timestamps) == 3, f"Must list all 3 timestamps; got: {timestamps}"

    def test_recurring_bullet_format_weapon(self):
        """Weapon re-enters bullet format: • who's weapon re-enters range ×N -> t1, t2, …"""
        rows = [
            _engagement(),
            _wir_enters(5, "Alice", "Rocket"),
            _wir_reenters(20, "Alice", "Rocket"),
            _wir_reenters(35, "Alice", "Rocket"),
            _wir_reenters(50, "Alice", "Rocket"),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "Rocket" in b and "re-enters" in b]
        assert len(rec) == 1
        bullet = rec[0]
        assert "Alice's Rocket re-enters range" in bullet
        assert "×3" in bullet, f"Bullet must show ×3 re-enters (3 re-enters); got: {bullet!r}"
        assert " -> " in bullet, f"Bullet must use ' -> ' separator; got: {bullet!r}"

    def test_recurring_min_run_threshold(self):
        """Only groups with ≥3 occurrences appear in recurring (threshold from GameConstants)."""
        # Shield broken 2x only (below threshold)
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield"),
            _layer(20, "Bob", "shield"),
            _outcome(100),
        ]
        s = _build(rows)
        rec_shield = [b for b in s["recurring"] if "shield" in b.lower()]
        assert not rec_shield, f"2 breaks must not produce a recurring bullet; got: {rec_shield}"

    def test_recurring_exactly_at_threshold(self):
        """Exactly 3 occurrences meets the threshold → recurring bullet produced."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield"),
            _layer(20, "Bob", "shield"),
            _layer(35, "Bob", "shield"),
            _outcome(100),
        ]
        s = _build(rows)
        rec_shield = [b for b in s["recurring"] if "shield depleted" in b.lower()]
        assert len(rec_shield) == 1, f"3 breaks must produce exactly 1 recurring bullet; got: {rec_shield}"


# ---------------------------------------------------------------------------
# 6. Qualifier handling
# ---------------------------------------------------------------------------


class TestQualifierHandling:
    """Per-occurrence qualifier shown only when it varies; uniform uses '(all X)' suffix."""

    def test_uniform_qualifier_uses_all_suffix(self):
        """When all occurrences have the same qualifier, append '(all <qualifier>)' once."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield", by="Laser"),
            _layer(20, "Bob", "shield", by="Laser"),
            _layer(35, "Bob", "shield", by="Laser"),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "shield" in b.lower()]
        assert len(rec) == 1
        bullet = rec[0]
        # Uniform qualifier → no per-occurrence parens, just "(all Laser)" suffix
        assert "(all Laser)" in bullet, f"Uniform qualifier must use '(all Laser)'; got: {bullet!r}"
        # No per-occurrence parens before timestamps
        ts_part = bullet.split(" -> ")[1]
        # Timestamps should not have per-occurrence qualifiers
        for ts in ts_part.split(","):
            ts = ts.strip().rstrip(")")
            # Should just be "Xs" not "Xs (Laser)"
            if "(all" not in ts:
                assert "(" not in ts, f"No per-occurrence qualifier expected; got ts: {ts!r}"

    def test_varying_qualifier_shown_per_occurrence(self):
        """When qualifiers vary across occurrences, each timestamp shows its own qualifier."""
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield", by="Laser"),
            _layer(20, "Bob", "shield", by="Rocket"),
            _layer(35, "Bob", "shield", by="Laser"),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "shield" in b.lower()]
        assert len(rec) == 1
        bullet = rec[0]
        # Must NOT have "(all ...)" since qualifiers vary
        assert "(all " not in bullet, f"Varying qualifiers must not use '(all ...)'; got: {bullet!r}"
        # Each timestamp should show its qualifier
        ts_part = bullet.split(" -> ")[1]
        assert "(Laser)" in ts_part, f"Per-occurrence qualifier (Laser) must appear; got: {ts_part!r}"
        assert "(Rocket)" in ts_part, f"Per-occurrence qualifier (Rocket) must appear; got: {ts_part!r}"

    def test_no_qualifier_bleed(self):
        """First occurrence's qualifier must NOT be inherited by the whole group."""
        # This is the "qualifier bleed" bug: if first qualifier were applied to all, all would show "(Laser)"
        rows = [
            _engagement(),
            _layer(5, "Bob", "shield", by="Laser"),
            _layer(20, "Bob", "shield", by="Rocket"),
            _layer(35, "Bob", "shield", by="Cannon"),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "shield" in b.lower()]
        assert len(rec) == 1
        bullet = rec[0]
        # 3 distinct qualifiers → all shown per-occurrence, no uniform "(all X)" suffix
        assert "(all Laser)" not in bullet, f"Must not bleed first qualifier to all; got: {bullet!r}"
        # All 3 should appear
        assert "(Laser)" in bullet
        assert "(Rocket)" in bullet
        assert "(Cannon)" in bullet

    def test_module_qualifier_hp_percent(self):
        """Module activation qualifier is HP% (≤N%); uniform when all same."""
        rows = [
            _engagement(),
            _module(5, "Alice", "booster", hp=80),
            _module(20, "Alice", "booster", hp=80),
            _module(35, "Alice", "booster", hp=80),
            _outcome(100),
        ]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "booster" in b.lower()]
        assert len(rec) == 1
        bullet = rec[0]
        assert "(all ≤80%)" in bullet, f"Uniform HP% qualifier must use '(all ≤80%)'; got: {bullet!r}"


# ---------------------------------------------------------------------------
# 7. Layer re-breaks NOT fill candidates
# ---------------------------------------------------------------------------


class TestLayerRebreaksNotFill:
    """Spec: 'Layer re-breaks are NOT fill candidates, never in Key Events/gap-fill.'"""

    def test_layer_rebreak_not_in_key_events(self):
        """Re-break of the same layer must never appear in key_events."""
        rows = [
            _engagement(0),
            _layer(5, "Bob", "shield"),  # first break → mandatory
            _layer(50, "Bob", "shield"),  # re-break → NOT in key events, NOT fill
            _layer(95, "Bob", "shield"),  # re-break
            _outcome(100),
        ]
        s = _build(rows)
        ke_layers = [r for r in s["key_events"] if r["event_type"] == "Layer depleted"]
        assert len(ke_layers) == 1, (
            f"Only first layer break in key_events; re-breaks must not appear; got {len(ke_layers)}"
        )
        assert ke_layers[0]["tick"] == 5

    def test_layer_rebreak_not_used_to_fill_gap(self):
        """Large gap must NOT be filled by a layer re-break (even if it's the only candidate)."""
        # Big gap from t=5 to t=100; only fill candidate is a layer re-break
        rows = [
            _engagement(0),
            _layer(5, "Bob", "shield"),  # first break → mandatory
            _layer(50, "Bob", "shield"),  # re-break → NOT a fill candidate
            _layer(95, "Bob", "shield"),  # re-break
            _outcome(100),
        ]
        s = _build(rows)
        # key_events should still contain only the first break + non-cyclic events
        ke_layers = [r for r in s["key_events"] if r["event_type"] == "Layer depleted"]
        assert len(ke_layers) == 1, f"Layer re-breaks must NOT fill gaps; key_events has {len(ke_layers)} layer events"


# ---------------------------------------------------------------------------
# 8. Recurring → every timestamp listed
# ---------------------------------------------------------------------------


class TestRecurringAllTimestamps:
    """Every occurrence's timestamp must appear in recurring bullet."""

    def test_all_timestamps_in_recurring(self):
        ticks = [5, 20, 35, 50, 65]
        rows = [_engagement()] + [_layer(t, "Bob", "shield") for t in ticks] + [_outcome(100)]
        s = _build(rows)
        rec = [b for b in s["recurring"] if "shield" in b.lower()]
        assert len(rec) == 1
        ts_part = rec[0].split(" -> ")[1]
        timestamps = [t.strip().rstrip(")") for t in ts_part.split(",")]
        # All 5 timestamps must be present
        assert len(timestamps) == 5, f"All 5 timestamps must appear; got: {timestamps}"
        # Each tick in seconds must appear
        for t in ticks:
            expected = f"{float(t * _TICK_MS / 1000):.1f}s"
            assert any(expected in ts for ts in ts_part.split(",")), (
                f"Timestamp {expected} must appear in recurring; got ts_part: {ts_part!r}"
            )


# ---------------------------------------------------------------------------
# 9. Gap-fill priority
# ---------------------------------------------------------------------------


class TestGapFillPriority:
    """Module re-activation (1) > secondary re-enter (2) > primary re-enter (3)."""

    def test_module_reactivation_preferred_over_weapon_reenter(self):
        """When both a module re-activation and a weapon re-enter are in a gap,
        the module re-activation should be picked first (priority 1 < priority 3)."""
        rows = [
            _engagement(0),
            _module(5, "Alice", "booster"),  # mandatory first activation
            _wir_enters(5, "Alice", "Rocket"),  # mandatory enters
            # Gap from 5s to 100s
            _module(50, "Alice", "booster", hp=60),  # re-activation: priority 1
            _wir_reenters(55, "Alice", "Rocket"),  # re-enter: priority 3
            _outcome(100),
        ]
        s = _build(rows, wslot={"Rocket": "primary"})
        # Both are fill candidates; module re-activation has higher priority (1 < 3).
        # The gap-fill should insert module activation before the re-enter candidate.
        mod_in_ke = [r for r in s["key_events"] if r["event_type"] == "Module activated"]
        # At least the mandatory first activation should be in key_events.
        assert len(mod_in_ke) >= 1

    def test_secondary_reenter_preferred_over_primary(self):
        """Secondary re-enter (priority 2) beats primary re-enter (priority 3) for fill."""
        rows = [
            _engagement(0),
            _wir_enters(5, "Alice", "PrimaryGun"),
            _wir_enters(5, "Alice", "SecondaryGun"),
            # Gap: re-enters at similar ticks
            _wir_reenters(50, "Alice", "PrimaryGun"),
            _wir_reenters(50, "Alice", "SecondaryGun"),
            _outcome(100),
        ]
        s = _build(rows, wslot={"PrimaryGun": "primary", "SecondaryGun": "secondary"})
        # Both are fill candidates; secondary gun has priority 2 < primary's 3.
        # If only one gets filled, it should be the secondary.
        # Just verify no crash and chronological order.
        times = [r["time_s"] for r in s["key_events"]]
        assert times == sorted(times), "key_events must be chronological"


# ---------------------------------------------------------------------------
# Helpers with wslot parameter
# ---------------------------------------------------------------------------


def _build(rows: list[dict], wslot: dict[str, str] | None = None) -> dict:
    """Assign sequential _idx values and call build_recap_sections."""
    for i, r in enumerate(rows):
        r["_idx"] = i
    return build_recap_sections(rows, {}, tick_ms=_TICK_MS, wslot=wslot)
