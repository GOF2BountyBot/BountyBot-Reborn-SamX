"""Regression suite: cluster-missile rendering in the key-events log (§6.2 / §12).

A condensed cluster-missile weapon_fire event carries `hits`/`fired` counts, NOT a
`hit` bool. The key-events builder now emits these as "Weapon in range" range-in lines
showing the landed fraction ("3/4 hit") rather than the old "Secondary fire (cluster-missile)"
lines. A cluster volley that whiffs renders "miss"; one landing >=1 sub-munition renders
the fraction. These tests lock in the new behavior and guard the rocket/missile path.

NEW behavior (DESIGN_COMBAT_LOG_RECAP):
- Cluster-missile fires → "Weapon in range" event_type (R1 / R2 range-in beats)
- Detail: "{actor}'s {weapon} enters range — {hits}/{fired} hit" or "miss"
- Re-enters range: "{actor}'s {weapon} re-enters range — {hits}/{fired} hit"
- "Secondary fire (cluster-missile)" event_type is GONE
- "First hit" event_type is GONE
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level dependency stubs (same pattern as the other resolver suites)
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

from services.combat_resolver import _extract_key_events

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster_fire(tick, *, hits, fired=4, weapon="Garuda-IV", actor="Nuyang II", side=1):
    return {
        "tick": tick,
        "type": "weapon_fire",
        "actor": actor,
        "target": "Dace",
        "data": {
            "slot": "secondary",
            "subtype": "cluster-missile",
            "weapon": weapon,
            "fired": fired,
            "hits": hits,
            "accuracy": 0.6,
            "branch": "tier_bc",
            "side": side,
        },
    }


def _range_in_events(result):
    """Return 'Weapon in range' events from the result."""
    return [e for e in result if e["event_type"] == "Weapon in range"]


def _first_hits(result):
    """Verify 'First hit' is gone — always returns []."""
    return [e for e in result if e["event_type"] == "First hit"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterKeyEvents:
    def test_partial_volley_shows_landed_fraction(self):
        """3 of 4 sub-munitions land → 'Weapon in range' detail reads '3/4 hit', never 'miss'."""
        result = _extract_key_events([_cluster_fire(30, hits=3, fired=4)])
        wir = _range_in_events(result)
        assert len(wir) == 1
        assert "3/4 hit" in wir[0]["detail"]
        assert "miss" not in wir[0]["detail"]
        assert wir[0]["event_type"] == "Weapon in range"
        # "enters range" in detail (first fire = R1)
        assert "enters range" in wir[0]["detail"]

    def test_full_miss_volley_still_reads_miss(self):
        """0 of 4 land → 'Weapon in range' detail ends with 'miss' (genuine whiff)."""
        result = _extract_key_events([_cluster_fire(20, hits=0, fired=4)])
        wir = _range_in_events(result)
        assert len(wir) == 1
        assert "miss" in wir[0]["detail"]
        assert "0/4" not in wir[0]["detail"]
        assert wir[0]["event_type"] == "Weapon in range"

    def test_volley_with_hits_does_not_produce_first_hit(self):
        """'First hit' event_type is GONE — cluster hits do NOT produce First hit lines."""
        result = _extract_key_events([_cluster_fire(10, hits=1, fired=4, side=1)])
        fh = _first_hits(result)
        assert fh == [], f"Expected no 'First hit' events, got: {fh}"

    def test_full_miss_volley_also_no_first_hit(self):
        """Whiffed volleys never produce First hit (double-check the gone path)."""
        result = _extract_key_events([_cluster_fire(10, hits=0, fired=4, side=1)])
        assert _first_hits(result) == []

    def test_no_secondary_fire_event_type(self):
        """'Secondary fire (cluster-missile)' event_type is GONE — only 'Weapon in range' appears."""
        result = _extract_key_events([_cluster_fire(30, hits=3)])
        old_style = [e for e in result if str(e["event_type"]).startswith("Secondary fire")]
        assert old_style == [], f"Old 'Secondary fire' events must be gone, got: {old_style}"

    def test_battle17_style_sequence_produces_single_range_in(self):
        """The real battle-17 volley pattern (6 volleys, all same weapon) produces exactly
        ONE 'Weapon in range' event (the first fire = R1 range-in; subsequent same-tick-or-
        continuous fires collapse to a single initial entry since no displacement occurs).
        """
        timeline = [
            _cluster_fire(0, hits=1),
            _cluster_fire(30, hits=3),
            _cluster_fire(60, hits=2),
            _cluster_fire(90, hits=2),
            _cluster_fire(120, hits=2),
            _cluster_fire(150, hits=2),
        ]
        wir = _range_in_events(_extract_key_events(timeline))
        # Only the first fire triggers range-in (no distance events between shots → no re-enter)
        assert len(wir) == 1
        assert "enters range" in wir[0]["detail"]
        assert "1/4 hit" in wir[0]["detail"]
        # No "miss" in any event detail (all volleys landed at least 1)
        for ev in _extract_key_events(timeline):
            assert "miss" not in ev["detail"]

    def test_plain_rocket_hit_miss_via_weapon_in_range(self):
        """Regression guard: rocket/missile (which carry `hit`) appear as 'Weapon in range'."""
        rocket_hit = {
            "tick": 5,
            "type": "weapon_fire",
            "actor": "Betty",
            "target": "Opp",
            "data": {"slot": "secondary", "subtype": "rocket", "weapon": "R1", "hit": True, "side": 1},
        }
        missile_miss = {
            "tick": 6,
            "type": "weapon_fire",
            "actor": "Betty",
            "target": "Opp",
            "data": {"slot": "secondary", "subtype": "missile", "weapon": "M1", "hit": False, "side": 1},
        }
        result = _extract_key_events([rocket_hit, missile_miss])
        wir = _range_in_events(result)
        assert len(wir) == 2
        # Rocket (hit=True) → "hit" in detail; missile (hit=False) → "miss"
        r1_ev = next(e for e in wir if "R1" in e["detail"])
        m1_ev = next(e for e in wir if "M1" in e["detail"])
        assert "hit" in r1_ev["detail"]
        assert "miss" in m1_ev["detail"]
