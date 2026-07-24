"""Issue #51: Bronze combat-bonus fraction scales with prestige.

fraction = min(CAP, BASE + PER_PRESTIGE × prestige_count)
Defaults: 40% at 0★, +10%/★, capped at 100% (reached at 6★).
"""

from __future__ import annotations

import pytest
from services.bounty_service import _bronze_combat_bonus_fraction as f
from services.game_constants import GameConstants


def test_defaults_match_issue_table():
    assert f(0) == pytest.approx(0.40)
    assert f(1) == pytest.approx(0.50)
    assert f(2) == pytest.approx(0.60)


def test_caps_at_100_percent_from_six_stars():
    assert f(6) == pytest.approx(1.00)
    assert f(6) == f(20)  # never exceeds the cap
    assert f(20) <= GameConstants.BRONZE_COMBAT_BONUS_CAP


def test_negative_prestige_floored_to_base():
    assert f(-3) == pytest.approx(0.40)
