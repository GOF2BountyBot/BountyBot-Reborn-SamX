"""Unit tests for Sub-task D: demote_player credit penalty.

Acceptance criteria:
- demote_player deducts 10% of credits (penalty = int(credits * 0.10))
- player.credits is clamped to max(0, credits - penalty)
- returned dict includes 'penalty' key
- penalty is 0 when player has 0 credits
"""

import sys
import types
import unittest.mock
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger before importing service code
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services.player_service import PlayerService

# Convenience alias to keep patch strings under the 120-char line limit.
_SCRUB_TARGET = "services.player_service.PlayerService._scrub_orphaned_checks_after_tier_change"


def _make_player(player_id=1, guild_id=999, tier="Silver", credits=1000):
    player = MagicMock()
    player.id = player_id
    player.guild_id = guild_id
    player.tier = tier
    player.credits = credits
    player.xp = 5000
    player.tier_change_cooldown_end = None
    return player


def _make_config():
    config = MagicMock()
    config.tier_change_cooldown = None
    config.demotion_credit_penalty_pct = None  # NULL → falls back to GameConstants default (10%)
    return config


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    db.begin = _mock_begin
    return db


@pytest.fixture
def service():
    svc = PlayerService()
    svc.player_repo = AsyncMock()
    svc.config_repo = AsyncMock()
    svc.user_repo = AsyncMock()
    return svc


class TestDemotePlayerPenalty:
    """Tests for the 10% credit penalty applied by demote_player (Sub-task D)."""

    @pytest.mark.asyncio
    async def test_demote_deducts_10_percent_of_credits(self, service, mock_db):
        """demote_player deducts 10% of the player's current credits."""
        player = _make_player(tier="Silver", credits=1000)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        expected_penalty = int(1000 * 0.10)  # 100
        assert result["penalty"] == expected_penalty
        assert player.credits == 1000 - expected_penalty  # 900

    @pytest.mark.asyncio
    async def test_demote_penalty_is_zero_when_no_credits(self, service, mock_db):
        """penalty should be 0 when player has 0 credits (max(0, 0 - 0) = 0)."""
        player = _make_player(tier="Silver", credits=0)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert result["penalty"] == 0
        assert player.credits == 0

    @pytest.mark.asyncio
    async def test_demote_credits_clamped_to_zero(self, service, mock_db):
        """Credits are clamped to 0 via max(0, credits - penalty)."""
        # Edge case: credits=1, penalty=int(1*0.10)=0 → credits stays 1
        # More realistic: credits=5, penalty=0 → credits stays 5
        player = _make_player(tier="Silver", credits=5)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        penalty = int(5 * 0.10)  # 0 (integer division)
        assert result["penalty"] == penalty
        assert player.credits == max(0, 5 - penalty)

    @pytest.mark.asyncio
    async def test_demote_result_includes_penalty_key(self, service, mock_db):
        """The returned dict always includes the 'penalty' key."""
        player = _make_player(tier="Gold", credits=2500)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert "penalty" in result
        assert isinstance(result["penalty"], int)

    @pytest.mark.asyncio
    async def test_demote_returns_old_and_new_tier(self, service, mock_db):
        """Existing return shape is preserved after adding penalty field."""
        player = _make_player(tier="Gold", credits=800)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert result["old_tier"] == "Gold"
        assert result["new_tier"] == "Silver"
        assert result["player_id"] == 1
        assert "xp" in result
        assert "penalty" in result

    @pytest.mark.asyncio
    async def test_demote_raises_for_bronze_player(self, service, mock_db):
        """Bronze players cannot be demoted — raises ValueError."""
        player = _make_player(tier="Bronze", credits=500)

        service.player_repo.get_by_id_for_update.return_value = player

        with pytest.raises(ValueError, match="minimum tier"):
            await service.demote_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_demote_large_credits_penalty_is_10_percent(self, service, mock_db):
        """10% penalty on large credit amounts is computed correctly."""
        player = _make_player(tier="Platinum", credits=50000)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        expected_penalty = int(50000 * 0.10)  # 5000
        assert result["penalty"] == expected_penalty
        assert player.credits == 50000 - expected_penalty


class TestDemotePlayerPenaltyAdversarial:
    """Adversarial and boundary tests for demote_player credit penalty."""

    @pytest.mark.asyncio
    async def test_demote_1_credit_penalty_is_zero(self, service, mock_db):
        """1 credit → int(1 * 0.10) = 0 penalty, credits stay at 1."""
        player = _make_player(tier="Silver", credits=1)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert result["penalty"] == 0  # int(1 * 0.10) = 0
        assert player.credits == 1  # unchanged

    @pytest.mark.asyncio
    async def test_demote_9_credits_penalty_is_zero(self, service, mock_db):
        """9 credits → int(9 * 0.10) = 0 penalty (truncated), credits stay at 9."""
        player = _make_player(tier="Gold", credits=9)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert result["penalty"] == 0  # int(9 * 0.10) = int(0.9) = 0
        assert player.credits == 9

    @pytest.mark.asyncio
    async def test_demote_10_credits_penalty_is_1(self, service, mock_db):
        """10 credits → int(10 * 0.10) = 1 penalty, credits drop to 9."""
        player = _make_player(tier="Silver", credits=10)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert result["penalty"] == 1
        assert player.credits == 9

    @pytest.mark.asyncio
    async def test_demote_credits_never_go_negative(self, service, mock_db):
        """Credits are always clamped to 0 via max(0, credits - penalty)."""
        # This shouldn't happen normally (10% of 0 = 0), but let's verify the guard.
        # Simulate an edge case where player has very low credits.
        player = _make_player(tier="Platinum", credits=0)
        config = _make_config()

        service.player_repo.get_by_id_for_update.return_value = player
        service.config_repo.get_by_guild_id.return_value = config

        with unittest.mock.patch(_SCRUB_TARGET, new=AsyncMock(return_value=0)):
            result = await service.demote_player(mock_db, player_id=1)

        assert player.credits >= 0
        assert result["penalty"] == 0
