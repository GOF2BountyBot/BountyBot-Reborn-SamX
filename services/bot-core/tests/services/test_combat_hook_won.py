"""The real combat event hook passes won= to event_service.record (issue #120).

must_win filtering in record() depends on this kwarg; without it every must_win event
silently stops filtering. Drives CombatService._increment_player_stats directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from services.combat_models import FightResults, FightStats
from services.combat_service import CombatService

_STATS = FightStats(ship_name="S", raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=10.0)
_SUMMARY = {"summary": {"duration_ticks": 50, "combatants": {"1": {"shots_fired": 3}, "2": {"shots_fired": 2}}}}


class _Session:
    async def flush(self):
        return None


def _player(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        user_id=user_id,
        guild_id=1,
        tier="Silver",
        total_fights=0,
        total_nukes_fired=0,
        total_module_activations=0,
    )


async def _won_by_user(fight: FightResults) -> dict[int, bool | None]:
    with (
        patch(
            "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
            new=AsyncMock(side_effect=lambda _s, uid, _g: _player(uid)),
        ),
        patch("services.event_service.record", new_callable=AsyncMock) as rec,
    ):
        await CombatService()._increment_player_stats(
            session=_Session(),
            fight_results=fight,
            combatant1_user_id=11,
            combatant2_user_id=22,
            guild_id=1,
            context="duel",
            stakes=5000,
        )
    return {c.args[1].user_id: c.kwargs.get("won") for c in rec.call_args_list}


@pytest.mark.parametrize(
    ("winner_side", "stalemate", "expected"),
    [(1, False, {11: True, 22: False}), (2, False, {11: False, 22: True}), (None, True, {11: False, 22: False})],
)
async def test_hook_passes_won_per_side(winner_side, stalemate, expected):
    fight = FightResults(
        winner_name=None,
        loser_name=None,
        is_stalemate=stalemate,
        ship1_stats=_STATS,
        ship2_stats=_STATS,
        winner_side=winner_side,
        metadata=_SUMMARY,
    )
    assert await _won_by_user(fight) == expected
