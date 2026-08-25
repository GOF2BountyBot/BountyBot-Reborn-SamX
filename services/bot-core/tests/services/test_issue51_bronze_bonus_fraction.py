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


# ---------------------------------------------------------------------------
# Unit C: per-guild override support (issue #70)
# ---------------------------------------------------------------------------


def test_explicit_params_override_defaults():
    """Passing explicit base/per_prestige/cap values bypasses the global defaults."""
    # 20% base, +5%/star, capped at 50%
    result = f(0, base_mult=0.20, per_prestige=0.05, cap=0.50)
    assert result == pytest.approx(0.20)

    result = f(2, base_mult=0.20, per_prestige=0.05, cap=0.50)
    assert result == pytest.approx(0.30)  # 0.20 + 2 × 0.05


def test_cap_clamping_with_custom_values():
    """The cap is enforced regardless of how high prestige count climbs."""
    # Cap of 0.50, so even 100 stars cannot exceed it.
    result = f(100, base_mult=0.20, per_prestige=0.10, cap=0.50)
    assert result == pytest.approx(0.50)


def test_fallback_to_globals_when_cfg_none():
    """Calling with no extra arguments still uses the global GameConstants defaults."""
    # Matches the original behaviour: 0.40 + 0.10×0 capped at 1.0 = 0.40
    assert f(0) == pytest.approx(GameConstants.BRONZE_COMBAT_BONUS_BASE_MULT)

    # 6 stars → should be exactly the global CAP
    assert f(6) == pytest.approx(GameConstants.BRONZE_COMBAT_BONUS_CAP)
