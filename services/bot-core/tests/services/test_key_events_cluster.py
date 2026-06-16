"""Regression suite: cluster-missile rendering in the key-events log (§6.2 / §12).

A condensed cluster-missile weapon_fire event carries `hits`/`fired` counts, NOT a
`hit` bool. The key-events builder previously read `data.get("hit", False)` for every
secondary, so a cluster volley that landed sub-munitions still printed "miss"
(e.g. battle #17: six Garuda-IV volleys at 12/24 hits all rendered "miss"), and a
cluster could never register the per-side "First hit" line.

_extract_key_events now shows the landed fraction ("3/4 hit") and credits a volley
landing >=1 sub-munition as a hit. These tests lock that in and guard the plain
rocket/missile path against regression.
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

from src.services.combat_resolver import _extract_key_events

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


def _secondary_fires(result):
    return [e for e in result if str(e["event_type"]).startswith("Secondary fire")]


def _first_hits(result):
    return [e for e in result if e["event_type"] == "First hit"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterKeyEvents:
    def test_partial_volley_shows_landed_fraction(self):
        """3 of 4 sub-munitions land → detail reads "3/4 hit", never "miss"."""
        result = _extract_key_events([_cluster_fire(30, hits=3, fired=4)])
        sec = _secondary_fires(result)
        assert len(sec) == 1
        assert "3/4 hit" in sec[0]["detail"]
        assert "miss" not in sec[0]["detail"]
        assert sec[0]["event_type"] == "Secondary fire (cluster-missile)"

    def test_full_miss_volley_still_reads_miss(self):
        """0 of 4 land → "miss" (a genuine whiff is still a whiff)."""
        result = _extract_key_events([_cluster_fire(20, hits=0, fired=4)])
        sec = _secondary_fires(result)
        assert len(sec) == 1
        assert sec[0]["detail"].endswith("— miss")
        assert "0/4" not in sec[0]["detail"]

    def test_volley_with_hits_registers_first_hit(self):
        """A cluster landing >=1 sub-munition counts as the side's first hit."""
        result = _extract_key_events([_cluster_fire(10, hits=1, fired=4, side=1)])
        fh = _first_hits(result)
        assert len(fh) == 1
        assert "Garuda-IV" in fh[0]["detail"]

    def test_full_miss_volley_does_not_register_first_hit(self):
        """A whiffed volley must not falsely claim a first hit."""
        result = _extract_key_events([_cluster_fire(10, hits=0, fired=4, side=1)])
        assert _first_hits(result) == []

    def test_battle17_style_sequence(self):
        """The real battle-17 volley pattern renders fractions, not six "miss" lines."""
        timeline = [
            _cluster_fire(0, hits=1),
            _cluster_fire(30, hits=3),
            _cluster_fire(60, hits=2),
            _cluster_fire(90, hits=2),
            _cluster_fire(120, hits=2),
            _cluster_fire(150, hits=2),
        ]
        details = [e["detail"] for e in _secondary_fires(_extract_key_events(timeline))]
        assert details == [
            "Nuyang II fired Garuda-IV — 1/4 hit",
            "Nuyang II fired Garuda-IV — 3/4 hit",
            "Nuyang II fired Garuda-IV — 2/4 hit",
            "Nuyang II fired Garuda-IV — 2/4 hit",
            "Nuyang II fired Garuda-IV — 2/4 hit",
            "Nuyang II fired Garuda-IV — 2/4 hit",
        ]
        assert not any("miss" in d for d in details)

    def test_plain_secondary_hit_miss_unchanged(self):
        """Regression guard: rocket/missile (which DO carry `hit`) are untouched."""
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
        details = [e["detail"] for e in _secondary_fires(_extract_key_events([rocket_hit, missile_miss]))]
        assert details == ["Betty fired R1 — hit", "Betty fired M1 — miss"]
