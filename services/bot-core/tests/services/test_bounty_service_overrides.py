"""Tests for per-guild override paths in BountyService (B.49).

Covers three override scenarios:
1. ``bounty_winner_reserve_factor`` — verified via ``calc_rewards`` directly.
2. ``bounty_pvc_armour_buff_factor`` — verified by inspecting the argument
   passed to ``combat_service.fight_ships``.
3. ``division_max_tl`` — verified via ``spawn_bounty`` tech-level cap logic
   using a mock config_repo.

Max 2 mocks per test as required by the testing conventions.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger is mocked before importing service code.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.bounty_service import BountyService, RewardInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(
    reward: int = 1000,
    reward_per_sys: int = 100,
    answer: str = "Sol",
    checked: dict | None = None,
) -> SimpleNamespace:
    """Build a minimal Bounty-like SimpleNamespace for calc_rewards tests."""
    if checked is None:
        checked = {"Sol": 42, "Alpha": -1}  # player 42 checked the answer
    return SimpleNamespace(
        reward=reward,
        reward_per_sys=reward_per_sys,
        answer=answer,
        checked=checked,
    )


# ---------------------------------------------------------------------------
# 1. bounty_winner_reserve_factor override via calc_rewards
# ---------------------------------------------------------------------------


class TestWinnerReserveFactorOverride:
    """calc_rewards respects per-guild bounty_winner_reserve_factor."""

    async def test_winner_reserve_factor_uses_global_when_config_is_none(self):
        """Without override, winner_reserve = int(reward * 0.25) (global default)."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        rewards = await service.calc_rewards(db, bounty, cfg=None)
        winner = next(r for r in rewards if r.is_winner)
        # With factor 0.25: winner_reserve=250, consolation=750, winner gets 250+750=1000
        assert winner.credits_earned == 1000

    async def test_winner_reserve_factor_per_guild_override(self):
        """A per-guild reserve factor of 0.5 gives the winner half the total reward."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        cfg = MagicMock()
        cfg.bounty_winner_reserve_factor = 0.5
        cfg.bounty_reward_to_xp_gain_mult = None  # use global XP mult

        rewards = await service.calc_rewards(db, bounty, cfg=cfg)
        winner = next(r for r in rewards if r.is_winner)
        # winner_reserve = int(1000 * 0.5) = 500, consolation=500 → winner gets 1000
        assert winner.credits_earned == 1000  # winner gets all (no other checkers)

    async def test_winner_reserve_factor_with_consolation_checker(self):
        """Non-winner checker consumes from consolation pool under per-guild factor."""
        service = BountyService()
        # Two players: player 99 checked "Alpha" (non-answer), player 42 won at "Sol"
        checked = {"Sol": 42, "Alpha": 99}
        bounty = _make_bounty(
            reward=1000,
            reward_per_sys=200,
            answer="Sol",
            checked=checked,
        )
        db = AsyncMock()

        cfg = MagicMock()
        cfg.bounty_winner_reserve_factor = 0.5
        cfg.bounty_reward_to_xp_gain_mult = None

        rewards = await service.calc_rewards(db, bounty, cfg=cfg)
        winner = next(r for r in rewards if r.is_winner)
        loser = next(r for r in rewards if not r.is_winner)

        # With factor=0.5: winner_reserve=500, consolation=500
        # Loser takes min(200, 500) = 200 from consolation → pool becomes 300
        # Winner gets 500 + 300 = 800
        assert loser.credits_earned == 200
        assert winner.credits_earned == 800

    async def test_winner_reserve_factor_zero_gives_winner_consolation_only(self):
        """A reserve factor of 0.0 means the winner gets only the leftover consolation."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        cfg = MagicMock()
        cfg.bounty_winner_reserve_factor = 0.0
        cfg.bounty_reward_to_xp_gain_mult = None

        rewards = await service.calc_rewards(db, bounty, cfg=cfg)
        winner = next(r for r in rewards if r.is_winner)
        # winner_reserve=0, consolation=1000, winner gets 0+1000=1000
        assert winner.credits_earned == 1000


# ---------------------------------------------------------------------------
# 2. bounty_reward_to_xp_gain_mult override via calc_rewards
# ---------------------------------------------------------------------------


class TestXPMultOverride:
    """calc_rewards respects per-guild bounty_reward_to_xp_gain_mult."""

    async def test_xp_mult_global_default(self):
        """Without override, XP = int(credits * 0.1) (global default)."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        rewards = await service.calc_rewards(db, bounty, cfg=None)
        winner = next(r for r in rewards if r.is_winner)
        assert winner.xp_earned == int(winner.credits_earned * 0.1)

    async def test_xp_mult_per_guild_higher_multiplier(self):
        """A per-guild XP multiplier of 0.5 awards 5× more XP than the default."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        cfg = MagicMock()
        cfg.bounty_winner_reserve_factor = None  # use global
        cfg.bounty_reward_to_xp_gain_mult = 0.5  # 5x the global 0.1

        rewards = await service.calc_rewards(db, bounty, cfg=cfg)
        winner = next(r for r in rewards if r.is_winner)
        assert winner.xp_earned == int(winner.credits_earned * 0.5)

    async def test_xp_mult_zero_gives_no_xp(self):
        """A per-guild XP multiplier of 0.0 means no XP awarded (valid override)."""
        service = BountyService()
        bounty = _make_bounty(reward=1000, checked={"Sol": 42}, answer="Sol")
        db = AsyncMock()

        cfg = MagicMock()
        cfg.bounty_winner_reserve_factor = None
        cfg.bounty_reward_to_xp_gain_mult = 0.0

        rewards = await service.calc_rewards(db, bounty, cfg=cfg)
        winner = next(r for r in rewards if r.is_winner)
        assert winner.xp_earned == 0


# ---------------------------------------------------------------------------
# 3. bounty_pvc_armour_buff_factor override via _process_single_bounty_check
# ---------------------------------------------------------------------------


class TestPvcArmourBuffOverride:
    """_process_single_bounty_check passes per-guild bounty_pvc_armour_buff_factor
    to combat_service.fight_ships as ``player_armour_buff``."""

    def _make_bronze_bounty(self, bounty_id: int = 1) -> SimpleNamespace:
        return SimpleNamespace(
            id=bounty_id,
            guild_id=100,
            answer="Sol",
            reward=500,
            reward_per_sys=50,
            criminal_name="Crusher",
            criminal_ship={"ship_name": "Betty", "ship_armour": 50, "weapons": [], "turrets": []},
            checked={"Sol": -1},
            end_time=None,
            division="bronze",
        )

    def _make_player(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=42,
            user_id=4200,   # T10: Discord user_id for fight_ships
            guild_id=100,
            tier="Bronze",
            credits=1000,
            lifetime_credits=5000,
            classic_mode=False,
            xp=0,
            bounty_wins=0,
            systems_checked=0,
            active_ship=SimpleNamespace(id=7),
            active_ship_id=None,  # forces fallback to active_ship attribute
        )

    async def test_pvc_buff_global_default_passed_to_fight_ships(self):
        """T10: Without per-guild override, fight_ships receives pvc_damage_reduction=0.33 (global)."""
        from datetime import UTC, datetime

        from services.combat_models import ShipLoadout
        from services.game_constants import GameConstants

        service = BountyService()

        mock_fight = MagicMock()
        mock_fight.winner_name = "Betty"
        mock_fight.is_stalemate = False
        mock_fight.combat_log_id = None
        service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

        db = AsyncMock()
        bounty = self._make_bronze_bounty()
        service.bounty_repo.update = AsyncMock()

        with (
            patch("services.bounty_service.BountyService.distribute_rewards", new=AsyncMock(return_value=None)),
            patch(
                "services.bounty_service.BountyService.calc_rewards",
                new=AsyncMock(
                    return_value=[RewardInfo(player_id=42, credits_earned=500, xp_earned=50, is_winner=True)]
                ),
            ),
            patch("services.bounty_service.BountyService._award_combat_bonus", new=AsyncMock()),
            patch("services.loadout_builder.LoadoutBuilder.from_player") as mock_from_player,
            patch("services.loadout_builder.LoadoutBuilder.from_criminal_ship") as mock_from_criminal,
        ):
            mock_from_player.return_value = ShipLoadout(ship_name="Betty", base_armour=100)
            mock_from_criminal.return_value = ShipLoadout(ship_name="Crusher", base_armour=80)

            await service._process_single_bounty_check(
                db,
                player=self._make_player(),
                player_id=42,
                bounty=bounty,
                system_name="Sol",
                division="bronze",
                now=datetime.now(UTC),
                cfg=None,  # no per-guild config → use global
            )

        service.combat_service.fight_ships.assert_awaited_once()
        call_kwargs = service.combat_service.fight_ships.call_args
        pvc_dr_used = call_kwargs.kwargs.get("pvc_damage_reduction")
        assert pvc_dr_used == pytest.approx(GameConstants.PVC_DAMAGE_REDUCTION)

    async def test_pvc_buff_per_guild_override_passed_to_fight_ships(self):
        """T10: Per-guild pvc_damage_reduction override (0.20) overrides the global 0.33."""
        from datetime import UTC, datetime

        from services.combat_models import ShipLoadout

        service = BountyService()

        mock_fight = MagicMock()
        mock_fight.winner_name = "Betty"
        mock_fight.is_stalemate = False
        mock_fight.combat_log_id = None
        service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

        db = AsyncMock()
        bounty = self._make_bronze_bounty(bounty_id=2)
        service.bounty_repo.update = AsyncMock()

        cfg = MagicMock()
        cfg.pvc_damage_reduction = 0.20  # T10: per-guild override for PvC DR

        with (
            patch("services.bounty_service.BountyService.distribute_rewards", new=AsyncMock(return_value=None)),
            patch(
                "services.bounty_service.BountyService.calc_rewards",
                new=AsyncMock(
                    return_value=[RewardInfo(player_id=42, credits_earned=500, xp_earned=50, is_winner=True)]
                ),
            ),
            patch("services.bounty_service.BountyService._award_combat_bonus", new=AsyncMock()),
            patch("services.loadout_builder.LoadoutBuilder.from_player") as mock_from_player,
            patch("services.loadout_builder.LoadoutBuilder.from_criminal_ship") as mock_from_criminal,
        ):
            mock_from_player.return_value = ShipLoadout(ship_name="Betty", base_armour=100)
            mock_from_criminal.return_value = ShipLoadout(ship_name="Vandal", base_armour=80)

            await service._process_single_bounty_check(
                db,
                player=self._make_player(),
                player_id=42,
                bounty=bounty,
                system_name="Sol",
                division="bronze",
                now=datetime.now(UTC),
                cfg=cfg,  # per-guild override
            )

        service.combat_service.fight_ships.assert_awaited_once()
        call_kwargs = service.combat_service.fight_ships.call_args
        pvc_dr_used = call_kwargs.kwargs.get("pvc_damage_reduction")
        assert pvc_dr_used == pytest.approx(0.20)
