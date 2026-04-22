"""
Unit tests for PlayerService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root which handles this).
"""

import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure shared.bblogger and sqlalchemy_utils are mocked before importing
# service code. conftest.py at the tests/ root mocks shared.bblogger, but
# sqlalchemy_utils (used by discord_message.py via models/__init__.py) also
# needs mocking as the package is not installed in the test environment.
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(
    player_id: int = 1,
    user_id: int = 111,
    guild_id: int = 999,
    tier: str = "Bronze",
    xp: int = 0,
    credits: int = 500,
    lifetime_credits: int = 500,
    prestige_count: int = 0,
    duel_wins: int = 3,
    duel_losses: int = 1,
    duel_credits_won: int = 300,
    duel_credits_lost: int = 100,
    systems_checked: int = 5,
    bounty_wins: int = 2,
) -> MagicMock:
    """Return a mock Player object with sensible defaults."""
    player = MagicMock()
    player.id = player_id
    player.user_id = user_id
    player.guild_id = guild_id
    player.tier = tier
    player.tier_level = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}.get(tier, 1)
    player.xp = xp
    player.credits = credits
    player.new_credits = credits
    player.lifetime_credits = lifetime_credits
    player.prestige_count = prestige_count
    player.duel_wins = duel_wins
    player.duel_losses = duel_losses
    player.duel_credits_won = duel_credits_won
    player.duel_credits_lost = duel_credits_lost
    player.systems_checked = systems_checked
    player.bounty_wins = bounty_wins
    player.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    player.updated_at = datetime(2025, 6, 1, tzinfo=UTC)
    return player


def _make_user(discord_id: int = 111, username: str = "TestUser") -> MagicMock:
    user = MagicMock()
    user.id = discord_id
    user.discord_username = username
    return user


def _make_config(starting_credits: int = 500, xp_thresholds: dict | None = None) -> MagicMock:
    config = MagicMock()
    config.starting_credits = starting_credits
    config.xp_thresholds = xp_thresholds or {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
    return config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    db.begin = _mock_begin
    return db


@pytest.fixture
def mock_user_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_or_create_user = AsyncMock()
    return repo


@pytest.fixture
def mock_player_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_user_and_guild = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_id_for_update = AsyncMock()
    repo.add = AsyncMock()
    repo.update_active_ship = AsyncMock()
    repo.get_players_by_guild = AsyncMock()
    repo.update_credits = AsyncMock()
    return repo


@pytest.fixture
def mock_config_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_guild_id = AsyncMock()
    return repo


@pytest.fixture
def service(mock_user_repo, mock_player_repo, mock_config_repo) -> PlayerService:
    """PlayerService with all repositories replaced by mocks."""
    svc = PlayerService()
    svc.user_repo = mock_user_repo
    svc.player_repo = mock_player_repo
    svc.config_repo = mock_config_repo
    return svc


# ===========================================================================
# Tests: get_or_create_player
# ===========================================================================


class TestGetOrCreatePlayer:
    """Tests for PlayerService.get_or_create_player."""

    @pytest.mark.asyncio
    async def test_returns_existing_player_when_found(self, service, mock_db, mock_user_repo, mock_player_repo):
        """When a player already exists for the guild, return it without creating a new one.

        New behavior: player lookup comes BEFORE user_repo call.
        Existing players are returned immediately without touching user_repo or config_repo.
        """
        player = _make_player()
        mock_player_repo.get_by_user_and_guild.return_value = player

        result = await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)

        assert result is player
        # In the new implementation, user_repo is NOT called for existing players
        mock_user_repo.get_or_create_user.assert_not_awaited()
        mock_player_repo.get_by_user_and_guild.assert_awaited_once_with(mock_db, 111, 999)
        mock_player_repo.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_player_when_not_found(
        self, service, mock_db, mock_user_repo, mock_player_repo, mock_config_repo
    ):
        """When no player exists, create one with starter configuration."""
        user = _make_user()
        config = _make_config(starting_credits=500)
        new_player = _make_player(player_id=42)

        mock_user_repo.get_or_create_user.return_value = user
        mock_player_repo.get_by_user_and_guild.return_value = None
        mock_config_repo.get_by_guild_id.return_value = config
        mock_player_repo.add.return_value = new_player

        # Patch _create_starter_loadout so we don't need ship/inventory repos
        service._create_starter_loadout = AsyncMock()

        result = await service.get_or_create_player(mock_db, discord_id=111, guild_id=999, discord_username="TestUser")

        assert result is new_player
        mock_player_repo.add.assert_awaited_once()
        service._create_starter_loadout.assert_awaited_once_with(mock_db, new_player)

    @pytest.mark.asyncio
    async def test_passes_username_to_user_repo(
        self, service, mock_db, mock_user_repo, mock_player_repo, mock_config_repo
    ):
        """discord_username is forwarded to user_repo.get_or_create_user for NEW player creation.

        New behavior: username is used when creating a new player (no existing player found).
        """
        user = _make_user()
        config = _make_config(starting_credits=500)
        new_player = _make_player(player_id=42)

        mock_user_repo.get_or_create_user.return_value = user
        mock_player_repo.get_by_user_and_guild.return_value = None  # no existing player
        mock_config_repo.get_by_guild_id.return_value = config
        mock_player_repo.add.return_value = new_player
        service._create_starter_loadout = AsyncMock()

        await service.get_or_create_player(mock_db, discord_id=111, guild_id=999, discord_username="Alice")

        mock_user_repo.get_or_create_user.assert_awaited_once_with(mock_db, 111, "Alice")

    @pytest.mark.asyncio
    async def test_raises_guild_not_configured_when_no_config(
        self, service, mock_db, mock_user_repo, mock_player_repo, mock_config_repo
    ):
        """When no guild config exists, GuildNotConfiguredError is raised (no auto-create).

        This replaces the old behavior that created a player with 0 credits.
        """
        from services.config_service import GuildNotConfiguredError

        mock_player_repo.get_by_user_and_guild.return_value = None
        mock_config_repo.get_by_guild_id.return_value = None

        with pytest.raises(GuildNotConfiguredError) as exc_info:
            await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)

        assert exc_info.value.guild_id == 999
        mock_player_repo.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_raises_exception_from_user_repo(
        self, service, mock_db, mock_user_repo, mock_player_repo, mock_config_repo
    ):
        """Exceptions from user_repo bubble up when creating new player."""

        # New player path: config check passes, then user_repo is called
        config = _make_config(starting_credits=0)
        mock_player_repo.get_by_user_and_guild.return_value = None
        mock_config_repo.get_by_guild_id.return_value = config
        mock_user_repo.get_or_create_user.side_effect = RuntimeError("DB down")

        with pytest.raises(RuntimeError, match="DB down"):
            await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)


# ===========================================================================
# Tests: _create_new_player  (internal, tested via integration path)
# ===========================================================================


class TestCreateNewPlayer:
    """Tests for PlayerService._create_new_player."""

    @pytest.mark.asyncio
    async def test_creates_player_with_config_credits(self, service, mock_db, mock_config_repo, mock_player_repo):
        """New player receives starting_credits from guild config."""
        user = _make_user()
        config = _make_config(starting_credits=1000)
        mock_config_repo.get_by_guild_id.return_value = config

        expected_player = _make_player(credits=1000)
        mock_player_repo.add.return_value = expected_player
        service._create_starter_loadout = AsyncMock()

        result = await service._create_new_player(mock_db, user, guild_id=999)

        assert result is expected_player
        added_player = mock_player_repo.add.call_args[0][1]
        assert added_player.credits == 1000
        assert added_player.tier == "Bronze"
        assert added_player.xp == 0

    @pytest.mark.asyncio
    async def test_creates_player_with_zero_credits_when_no_config(
        self, service, mock_db, mock_config_repo, mock_player_repo
    ):
        """When config is absent, player starts with 0 credits."""
        user = _make_user()
        mock_config_repo.get_by_guild_id.return_value = None

        new_player = _make_player(credits=0)
        mock_player_repo.add.return_value = new_player
        service._create_starter_loadout = AsyncMock()

        await service._create_new_player(mock_db, user, guild_id=999)

        added_player = mock_player_repo.add.call_args[0][1]
        assert added_player.credits == 0

    @pytest.mark.asyncio
    async def test_calls_create_starter_loadout(self, service, mock_db, mock_config_repo, mock_player_repo):
        """_create_starter_loadout is called after player is added."""
        user = _make_user()
        mock_config_repo.get_by_guild_id.return_value = _make_config()
        new_player = _make_player()
        mock_player_repo.add.return_value = new_player
        service._create_starter_loadout = AsyncMock()

        await service._create_new_player(mock_db, user, guild_id=999)

        service._create_starter_loadout.assert_awaited_once_with(mock_db, new_player)

    @pytest.mark.asyncio
    async def test_reraises_exception_on_error(self, service, mock_db, mock_config_repo, mock_player_repo):
        """Exceptions from player_repo.add propagate out of _create_new_player."""
        user = _make_user()
        mock_config_repo.get_by_guild_id.return_value = _make_config()
        mock_player_repo.add.side_effect = RuntimeError("insert failed")

        with pytest.raises(RuntimeError, match="insert failed"):
            await service._create_new_player(mock_db, user, guild_id=999)


# ===========================================================================
# Tests: update_player_credits
# ===========================================================================


class TestUpdatePlayerCredits:
    """Tests for PlayerService.update_player_credits."""

    @pytest.mark.asyncio
    async def test_updates_credits_successfully(self, service, mock_db, mock_player_repo):
        """Credits are updated on the player object and committed."""
        player = _make_player(credits=500)
        player.new_credits = 500
        player.lifetime_credits = 500
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_credits(mock_db, player_id=1, new_credits=800)

        assert player.new_credits == 800
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(player)

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.update_player_credits(mock_db, player_id=99, new_credits=100)

    @pytest.mark.asyncio
    async def test_raises_when_credits_negative(self, service, mock_db, mock_player_repo):
        """ValueError raised for negative credits."""
        player = _make_player()
        mock_player_repo.get_by_id.return_value = player

        with pytest.raises(ValueError, match="Credits cannot be negative"):
            await service.update_player_credits(mock_db, player_id=1, new_credits=-50)

    @pytest.mark.asyncio
    async def test_increments_lifetime_credits_on_increase(self, service, mock_db, mock_player_repo):
        """lifetime_credits increases when new_credits exceeds current new_credits."""
        player = _make_player()
        player.new_credits = 500
        player.lifetime_credits = 500
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_credits(mock_db, player_id=1, new_credits=700, update_lifetime=True)

        assert player.lifetime_credits == 700  # 500 + (700 - 500)

    @pytest.mark.asyncio
    async def test_does_not_increment_lifetime_credits_on_decrease(self, service, mock_db, mock_player_repo):
        """lifetime_credits unchanged when new_credits is lower than current."""
        player = _make_player()
        player.new_credits = 500
        player.lifetime_credits = 500
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_credits(mock_db, player_id=1, new_credits=300, update_lifetime=True)

        assert player.lifetime_credits == 500  # Unchanged

    @pytest.mark.asyncio
    async def test_skips_lifetime_update_when_flag_false(self, service, mock_db, mock_player_repo):
        """lifetime_credits unchanged when update_lifetime=False even on increase."""
        player = _make_player()
        player.new_credits = 500
        player.lifetime_credits = 500
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_credits(mock_db, player_id=1, new_credits=1000, update_lifetime=False)

        assert player.lifetime_credits == 500


# ===========================================================================
# Tests: update_player_xp
# ===========================================================================


class TestUpdatePlayerXp:
    """Tests for PlayerService.update_player_xp."""

    @pytest.mark.asyncio
    async def test_updates_xp_successfully(self, service, mock_db, mock_player_repo):
        """XP is set on the player object and committed."""
        player = _make_player(xp=0, tier="Bronze")
        player.tier = "Bronze"
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_xp(mock_db, player_id=1, xp=500)

        assert player.xp == 500
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 7 not found"):
            await service.update_player_xp(mock_db, player_id=7, xp=100)

    @pytest.mark.asyncio
    async def test_clamps_negative_xp_to_zero(self, service, mock_db, mock_player_repo):
        """Negative XP is clamped to 0."""
        player = _make_player(xp=100)
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_xp(mock_db, player_id=1, xp=-50)

        assert player.xp == 0

    @pytest.mark.asyncio
    async def test_clamps_xp_to_max(self, service, mock_db, mock_player_repo):
        """XP above 1,000,000 is clamped to 1,000,000."""
        player = _make_player(xp=0)
        mock_player_repo.get_by_id.return_value = player

        await service.update_player_xp(mock_db, player_id=1, xp=9_999_999)

        assert player.xp == 1_000_000

    @pytest.mark.asyncio
    async def test_does_not_advance_tier_when_xp_threshold_reached(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
        """Tier does NOT auto-advance when XP crosses a threshold (AC-1, AC-11)."""
        player = _make_player(xp=0, tier="Bronze")
        player.tier = "Bronze"
        mock_player_repo.get_by_id.return_value = player

        # Even with config present, setting XP should not change tier
        config = _make_config(xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        mock_config_repo.get_by_guild_id.return_value = config

        await service.update_player_xp(mock_db, player_id=1, xp=1500)

        # Tier must NOT change — manual promotion required
        assert player.tier == "Bronze"

    @pytest.mark.asyncio
    async def test_does_not_change_tier_without_config(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Tier stays unchanged when no guild config is available."""
        player = _make_player(xp=0, tier="Bronze")
        player.tier = "Bronze"
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = None

        await service.update_player_xp(mock_db, player_id=1, xp=20000)

        assert player.tier == "Bronze"


# ===========================================================================
# Tests: _calculate_tier_from_xp
# ===========================================================================


class TestCalculateTierFromXp:
    """Tests for PlayerService._calculate_tier_from_xp (synchronous helper)."""

    def test_returns_bronze_below_silver_threshold(self):
        svc = PlayerService.__new__(PlayerService)
        result = svc._calculate_tier_from_xp(500, {"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        assert result == "Bronze"

    def test_returns_silver_at_threshold(self):
        svc = PlayerService.__new__(PlayerService)
        result = svc._calculate_tier_from_xp(1000, {"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        assert result == "Silver"

    def test_returns_gold_at_threshold(self):
        svc = PlayerService.__new__(PlayerService)
        result = svc._calculate_tier_from_xp(5000, {"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        assert result == "Gold"

    def test_returns_platinum_at_threshold(self):
        svc = PlayerService.__new__(PlayerService)
        result = svc._calculate_tier_from_xp(15000, {"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        assert result == "Platinum"

    def test_uses_defaults_for_missing_thresholds(self):
        """When a threshold key is absent, the hard-coded default is used."""
        svc = PlayerService.__new__(PlayerService)
        result = svc._calculate_tier_from_xp(20000, {})
        assert result == "Platinum"


# ===========================================================================
# Tests: prestige_player
# ===========================================================================


class TestPrestigePlayer:
    """Tests for PlayerService.prestige_player."""

    @pytest.mark.asyncio
    async def test_prestige_resets_tier_xp_surplus_and_credits(self, service, mock_db, mock_player_repo):
        """Level-10 player is fully reset: tier=Bronze, xp=0, xp_surplus=0, credits=0, prestige_count incremented."""
        player = _make_player(xp=1_000_000, credits=5000, prestige_count=0)
        player.xp = 1_000_000
        player.xp_surplus = 500
        player.credits = 5000
        player.prestige_count = 0
        mock_player_repo.get_by_id.return_value = player

        with patch(
            "persist.repositories.inventory_repository.InventoryRepository.clear_player_inventory",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await service.prestige_player(mock_db, player_id=1)

        assert player.tier == "Bronze"
        assert player.xp == 0
        assert player.xp_surplus == 0
        assert player.credits == 0
        assert player.prestige_count == 1
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(player)
        # Verify return dict structure
        assert result["player_id"] == 1
        assert result["prestige_count"] == 1
        assert result["level_before"] == 10
        assert isinstance(result["division_before"], str)

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 5 not found"):
            await service.prestige_player(mock_db, player_id=5)

    @pytest.mark.asyncio
    async def test_raises_when_player_below_level_10(self, service, mock_db, mock_player_repo):
        """ValueError raised when player is below level 10."""
        player = _make_player(xp=500)  # level 0
        player.xp = 500
        mock_player_repo.get_by_id.return_value = player

        with pytest.raises(ValueError, match="must be level 10"):
            await service.prestige_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_error_message_includes_current_level(self, service, mock_db, mock_player_repo):
        """ValueError message includes the player's current level."""
        player = _make_player(xp=18000)  # level 6
        player.xp = 18000
        mock_player_repo.get_by_id.return_value = player

        with pytest.raises(ValueError, match="current level: 6"):
            await service.prestige_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_increments_prestige_count_correctly(self, service, mock_db, mock_player_repo):
        """prestige_count increments from existing value."""
        player = _make_player(xp=1_000_000, prestige_count=3)
        player.xp = 1_000_000
        player.xp_surplus = 0
        player.credits = 100
        player.prestige_count = 3
        mock_player_repo.get_by_id.return_value = player

        with patch(
            "persist.repositories.inventory_repository.InventoryRepository.clear_player_inventory",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await service.prestige_player(mock_db, player_id=1)

        assert player.prestige_count == 4
        assert result["prestige_count"] == 4

    @pytest.mark.asyncio
    async def test_prestige_preserves_lifetime_credits(self, service, mock_db, mock_player_repo):
        """lifetime_credits is NOT reset during prestige."""
        player = _make_player(xp=1_000_000, credits=1000, lifetime_credits=99999)
        player.xp = 1_000_000
        player.xp_surplus = 0
        player.credits = 1000
        player.lifetime_credits = 99999
        player.prestige_count = 0
        mock_player_repo.get_by_id.return_value = player

        with patch(
            "persist.repositories.inventory_repository.InventoryRepository.clear_player_inventory",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await service.prestige_player(mock_db, player_id=1)

        # lifetime_credits must NOT be reset
        assert player.lifetime_credits == 99999

    @pytest.mark.asyncio
    async def test_prestige_returns_level_and_division_before(self, service, mock_db, mock_player_repo):
        """Return dict contains level_before and division_before reflecting pre-prestige state."""
        player = _make_player(xp=1_000_000)
        player.xp = 1_000_000
        player.xp_surplus = 0
        player.credits = 0
        player.prestige_count = 0
        mock_player_repo.get_by_id.return_value = player

        with patch(
            "persist.repositories.inventory_repository.InventoryRepository.clear_player_inventory",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await service.prestige_player(mock_db, player_id=1)

        assert result["level_before"] == 10
        assert "division_before" in result
        assert isinstance(result["division_before"], str)


# ===========================================================================
# Tests: get_player_statistics
# ===========================================================================


class TestGetPlayerStatistics:
    """Tests for PlayerService.get_player_statistics."""

    @pytest.mark.asyncio
    async def test_returns_comprehensive_stats(self, service, mock_db, mock_player_repo):
        """Statistics dict contains all expected keys with correct values."""
        player = _make_player(
            player_id=1,
            tier="Silver",
            xp=1500,
            credits=750,
            lifetime_credits=1000,
            prestige_count=0,
            duel_wins=4,
            duel_losses=2,
            duel_credits_won=400,
            duel_credits_lost=150,
            systems_checked=10,
            bounty_wins=3,
        )
        player.tier_level = 2
        mock_player_repo.get_by_id.return_value = player

        stats = await service.get_player_statistics(mock_db, player_id=1)

        assert stats["player_id"] == 1
        assert stats["tier"] == "Silver"
        assert stats["tier_level"] == 2
        assert stats["xp"] == 1500
        assert stats["credits"] == 750
        assert stats["lifetime_credits"] == 1000
        assert stats["prestige_count"] == 0
        assert stats["bounty_stats"]["systems_checked"] == 10
        assert stats["bounty_stats"]["bounty_wins"] == 3
        assert stats["duel_stats"]["wins"] == 4
        assert stats["duel_stats"]["losses"] == 2
        assert stats["duel_stats"]["win_rate"] == pytest.approx(66.67, abs=0.01)
        assert stats["duel_stats"]["net_credits"] == 250
        assert "created_at" in stats
        assert "updated_at" in stats

    @pytest.mark.asyncio
    async def test_win_rate_zero_when_no_duels(self, service, mock_db, mock_player_repo):
        """Win rate is 0 when player has never duelled."""
        player = _make_player(duel_wins=0, duel_losses=0)
        mock_player_repo.get_by_id.return_value = player

        stats = await service.get_player_statistics(mock_db, player_id=1)

        assert stats["duel_stats"]["win_rate"] == 0

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.get_player_statistics(mock_db, player_id=99)


# ===========================================================================
# Tests: get_players_by_tier
# ===========================================================================


class TestGetPlayersByTier:
    """Tests for PlayerService.get_players_by_tier."""

    @pytest.mark.asyncio
    async def test_returns_only_players_with_matching_tier(self, service, mock_db, mock_player_repo):
        """Only players whose tier matches the filter are returned."""
        bronze = _make_player(player_id=1, tier="Bronze")
        bronze.tier = "Bronze"
        silver = _make_player(player_id=2, tier="Silver")
        silver.tier = "Silver"
        gold = _make_player(player_id=3, tier="Gold")
        gold.tier = "Gold"

        mock_player_repo.get_players_by_guild.return_value = [bronze, silver, gold]

        result = await service.get_players_by_tier(mock_db, guild_id=999, tier="Silver")

        assert result == [silver]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_match(self, service, mock_db, mock_player_repo):
        """Empty list when no players match the tier."""
        bronze = _make_player(tier="Bronze")
        bronze.tier = "Bronze"
        mock_player_repo.get_players_by_guild.return_value = [bronze]

        result = await service.get_players_by_tier(mock_db, guild_id=999, tier="Platinum")

        assert result == []

    @pytest.mark.asyncio
    async def test_passes_guild_id_to_repo(self, service, mock_db, mock_player_repo):
        """guild_id is correctly forwarded to player_repo."""
        mock_player_repo.get_players_by_guild.return_value = []

        await service.get_players_by_tier(mock_db, guild_id=12345, tier="Bronze")

        mock_player_repo.get_players_by_guild.assert_awaited_once_with(mock_db, 12345)

    @pytest.mark.asyncio
    async def test_re_raises_repo_exception(self, service, mock_db, mock_player_repo):
        """Exceptions from the repository bubble up."""
        mock_player_repo.get_players_by_guild.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError, match="connection lost"):
            await service.get_players_by_tier(mock_db, guild_id=999, tier="Bronze")


# ===========================================================================
# Tests: _create_starter_loadout
# ===========================================================================


class TestCreateStarterLoadout:
    """Tests for PlayerService._create_starter_loadout.

    _create_starter_loadout uses local imports:
        from persist.repositories.player_ship_repository import PlayerShipRepository
        from persist.repositories.inventory_repository import InventoryRepository
    We intercept them by pre-populating sys.modules with mock modules.
    """

    def _make_player_ship_repo_mock(self, player_ship_repo_mock):
        """Return a mock module that exposes the given repo mock as PlayerShipRepository."""
        import types as _types

        ps_mod = _types.ModuleType("persist.repositories.player_ship_repository")
        ps_mod.PlayerShipRepository = MagicMock(return_value=player_ship_repo_mock)
        return ps_mod

    def _make_inventory_repo_mock(self, inv_repo_mock):
        """Return a mock module that exposes the given repo mock as InventoryRepository."""
        import types as _types

        inv_mod = _types.ModuleType("persist.repositories.inventory_repository")
        inv_mod.InventoryRepository = MagicMock(return_value=inv_repo_mock)
        return inv_mod

    def _make_repo_mocks(self):
        """Create and return (mock_ps_repo, mock_inv_repo, starter_ship, ps_mod, inv_mod)."""
        mock_ps_repo = AsyncMock()
        starter_ship = MagicMock()
        starter_ship.id = 42
        mock_ps_repo.create_or_update.return_value = starter_ship

        mock_inv_repo = AsyncMock()
        mock_inv_repo.add_item.return_value = MagicMock()

        ps_mod = self._make_player_ship_repo_mock(mock_ps_repo)
        inv_mod = self._make_inventory_repo_mock(mock_inv_repo)
        return mock_ps_repo, mock_inv_repo, starter_ship, ps_mod, inv_mod

    @pytest.mark.asyncio
    async def test_creates_betty_with_default_loadout(self, service, mock_db, mock_player_repo):
        """PlayerShipRepository.create_or_update is called with Betty ship data."""
        player = _make_player(player_id=7)

        mock_ps_repo, _inv_repo, starter_ship, ps_mod, inv_mod = self._make_repo_mocks()
        starter_ship.id = 42

        with patch.dict(
            sys.modules,
            {
                "persist.repositories.player_ship_repository": ps_mod,
                "persist.repositories.inventory_repository": inv_mod,
            },
        ):
            await service._create_starter_loadout(mock_db, player)

        mock_ps_repo.create_or_update.assert_awaited_once()
        call_args = mock_ps_repo.create_or_update.call_args[0]
        ship_data = call_args[1]
        assert ship_data["ship_name"] == "Betty"
        assert ship_data["player_id"] == 7
        assert ship_data["is_active"] is True

    @pytest.mark.asyncio
    async def test_updates_active_ship_after_creation(self, service, mock_db, mock_player_repo):
        """player_repo.update_active_ship is called with the new PlayerShip id."""
        player = _make_player(player_id=7)

        _ps_repo, _inv_repo, starter_ship, ps_mod, inv_mod = self._make_repo_mocks()
        starter_ship.id = 99

        with patch.dict(
            sys.modules,
            {
                "persist.repositories.player_ship_repository": ps_mod,
                "persist.repositories.inventory_repository": inv_mod,
            },
        ):
            await service._create_starter_loadout(mock_db, player)

        mock_player_repo.update_active_ship.assert_awaited_once_with(mock_db, 7, 99)

    @pytest.mark.asyncio
    async def test_starter_loadout_includes_weapons_and_modules(self, service, mock_db, mock_player_repo):
        """Starter ship data includes the correct default weapons and modules."""
        player = _make_player(player_id=7)

        mock_ps_repo, _inv_repo, _starter_ship, ps_mod, inv_mod = self._make_repo_mocks()

        with patch.dict(
            sys.modules,
            {
                "persist.repositories.player_ship_repository": ps_mod,
                "persist.repositories.inventory_repository": inv_mod,
            },
        ):
            await service._create_starter_loadout(mock_db, player)

        ship_data = mock_ps_repo.create_or_update.call_args[0][1]
        assert "Nirai Impulse EX 1" in ship_data["weapons"]
        assert "E2 Exoclad" in ship_data["modules"]
        assert "Telta Quickscan" in ship_data["modules"]
        assert ship_data["turrets"] == []

    @pytest.mark.asyncio
    async def test_starter_loadout_adds_micro_gun_to_cargo(self, service, mock_db, mock_player_repo):
        """Micro Gun MK I is added to player cargo inventory after ship creation."""
        player = _make_player(player_id=7)

        _ps_repo, mock_inv_repo, _starter_ship, ps_mod, inv_mod = self._make_repo_mocks()

        with patch.dict(
            sys.modules,
            {
                "persist.repositories.player_ship_repository": ps_mod,
                "persist.repositories.inventory_repository": inv_mod,
            },
        ):
            await service._create_starter_loadout(mock_db, player)

        mock_inv_repo.add_item.assert_awaited_once_with(mock_db, 7, "primary_weapon", "Micro Gun MK I", quantity=1)

    @pytest.mark.asyncio
    async def test_reraises_exception_on_error(self, service, mock_db):
        """Exceptions raised in _create_starter_loadout propagate."""
        player = _make_player(player_id=7)

        mock_ps_repo = AsyncMock()
        mock_ps_repo.create_or_update.side_effect = RuntimeError("ship create failed")

        ps_mod = self._make_player_ship_repo_mock(mock_ps_repo)

        mock_inv_repo = AsyncMock()
        inv_mod = self._make_inventory_repo_mock(mock_inv_repo)

        with (
            patch.dict(
                sys.modules,
                {
                    "persist.repositories.player_ship_repository": ps_mod,
                    "persist.repositories.inventory_repository": inv_mod,
                },
            ),
            pytest.raises(RuntimeError, match="ship create failed"),
        ):
            await service._create_starter_loadout(mock_db, player)


# ---------------------------------------------------------------------------
# TestTransferCredits
# ---------------------------------------------------------------------------


class TestTransferCredits:
    """Tests for PlayerService.transfer_credits()."""

    @pytest.mark.asyncio
    async def test_valid_transfer_returns_correct_dict(self, service, mock_db):
        """Happy path: transfers credits and returns correct result dict."""
        source = _make_player(player_id=1, credits=500)
        target = _make_player(player_id=2, credits=100)

        # transfer_credits uses get_by_id_for_update inside the transaction,
        # locking in sorted ID order: player 1 then player 2.
        service.player_repo.get_by_id_for_update = AsyncMock(side_effect=[source, target])
        service.player_repo.update_credits = AsyncMock()

        result = await service.transfer_credits(mock_db, 1, 2, 200)

        assert result["source_player_id"] == 1
        assert result["target_player_id"] == 2
        assert result["amount"] == 200
        assert result["source_remaining_credits"] == 300
        assert result["target_new_credits"] == 300

    @pytest.mark.asyncio
    async def test_valid_transfer_calls_update_credits_twice(self, service, mock_db):
        """Verify update_credits is called for both source and target."""
        source = _make_player(player_id=1, credits=500)
        target = _make_player(player_id=2, credits=100)

        service.player_repo.get_by_id_for_update = AsyncMock(side_effect=[source, target])
        service.player_repo.update_credits = AsyncMock()

        await service.transfer_credits(mock_db, 1, 2, 200)

        assert service.player_repo.update_credits.call_count == 2
        service.player_repo.update_credits.assert_any_call(mock_db, 1, 300, commit=False)
        service.player_repo.update_credits.assert_any_call(mock_db, 2, 300, commit=False)

    @pytest.mark.asyncio
    async def test_minimum_amount_one_credit_succeeds(self, service, mock_db):
        """Edge case: amount=1 is the minimum valid transfer."""
        source = _make_player(player_id=1, credits=50)
        target = _make_player(player_id=2, credits=10)

        service.player_repo.get_by_id_for_update = AsyncMock(side_effect=[source, target])
        service.player_repo.update_credits = AsyncMock()

        result = await service.transfer_credits(mock_db, 1, 2, 1)

        assert result["amount"] == 1
        assert result["source_remaining_credits"] == 49
        assert result["target_new_credits"] == 11

    @pytest.mark.asyncio
    async def test_zero_amount_raises_value_error(self, service, mock_db):
        """Validation: amount=0 must raise ValueError."""
        with pytest.raises(ValueError, match="at least 1 credit"):
            await service.transfer_credits(mock_db, 1, 2, 0)

    @pytest.mark.asyncio
    async def test_negative_amount_raises_value_error(self, service, mock_db):
        """Validation: negative amount must raise ValueError."""
        with pytest.raises(ValueError, match="at least 1 credit"):
            await service.transfer_credits(mock_db, 1, 2, -10)

    @pytest.mark.asyncio
    async def test_self_transfer_raises_value_error(self, service, mock_db):
        """Validation: source == target must raise ValueError."""
        with pytest.raises(ValueError, match="Cannot transfer credits to yourself"):
            await service.transfer_credits(mock_db, 5, 5, 100)

    @pytest.mark.asyncio
    async def test_insufficient_credits_raises_value_error(self, service, mock_db):
        """Validation: source has fewer credits than amount raises ValueError."""
        source = _make_player(player_id=1, credits=50)
        target = _make_player(player_id=2, credits=100)

        # IDs [1, 2] sorted → player 1 locked first, then player 2.
        service.player_repo.get_by_id_for_update = AsyncMock(side_effect=[source, target])

        with pytest.raises(ValueError, match="Insufficient credits"):
            await service.transfer_credits(mock_db, 1, 2, 100)

    @pytest.mark.asyncio
    async def test_source_not_found_raises_value_error(self, service, mock_db):
        """Validation: source player does not exist raises ValueError."""
        # IDs [2, 99] sorted → 2 locked first (returns None → raises)
        service.player_repo.get_by_id_for_update = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await service.transfer_credits(mock_db, 99, 2, 50)

    @pytest.mark.asyncio
    async def test_target_not_found_raises_value_error(self, service, mock_db):
        """Validation: target player does not exist raises ValueError."""
        source = _make_player(player_id=1, credits=200)

        # IDs [1, 88] sorted → player 1 locked first (returns source), then 88 (None).
        service.player_repo.get_by_id_for_update = AsyncMock(side_effect=[source, None])

        with pytest.raises(ValueError, match="not found"):
            await service.transfer_credits(mock_db, 1, 88, 50)


# ===========================================================================
# Tests: get_level (pure logic, 0 mocks)
# ===========================================================================


class TestGetLevel:
    """Tests for PlayerService.get_level — pure static logic, no mocks required."""

    def test_xp_zero_is_level_1(self):
        """XP = 0 should map to level 1."""
        assert PlayerService.get_level(0) == 1

    def test_xp_1050_is_level_2(self):
        """XP = 1050 is exactly at the level-2 boundary."""
        assert PlayerService.get_level(1050) == 2

    def test_xp_3499_is_level_3(self):
        """XP = 3499 is just below the level-4 boundary (3500)."""
        assert PlayerService.get_level(3499) == 3

    def test_xp_3500_is_level_4(self):
        """XP = 3500 is exactly at the level-4 boundary."""
        assert PlayerService.get_level(3500) == 4

    def test_xp_90000_is_level_9_or_10(self):
        """XP = 90000 is exactly at the level-9 boundary so returns level 9."""
        level = PlayerService.get_level(90000)
        assert level in (9, 10)

    def test_xp_negative_is_level_0(self):
        """Negative XP (-5) should yield level 0."""
        assert PlayerService.get_level(-5) == 0

    def test_xp_max_is_level_10(self):
        """XP = 1_000_000 (top boundary) should yield level 10."""
        assert PlayerService.get_level(1_000_000) == 10


# ===========================================================================
# Tests: check_level_up (pure logic, 0 mocks)
# ===========================================================================


class TestCheckLevelUp:
    """Tests for PlayerService.check_level_up — pure static logic, no mocks required."""

    def test_xp_500_to_1500_level_1_to_2_no_division_change(self):
        """XP 500 → 1500: level 1 → 2, both in bronze — no division change."""
        result = PlayerService.check_level_up(500, 1500)

        assert result["level_before"] == 1
        assert result["level_after"] == 2
        assert result["leveled_up"] is True
        assert result["division_before"] == "bronze"
        assert result["division_after"] == "bronze"
        assert result["division_changed"] is False

    def test_xp_3000_to_4000_level_3_to_4_bronze_to_silver(self):
        """XP 3000 → 4000: level 3 → 4, bronze → silver — division changes."""
        result = PlayerService.check_level_up(3000, 4000)

        assert result["level_before"] == 3
        assert result["level_after"] == 4
        assert result["leveled_up"] is True
        assert result["division_before"] == "bronze"
        assert result["division_after"] == "silver"
        assert result["division_changed"] is True

    def test_xp_3000_to_3200_no_level_change(self):
        """XP 3000 → 3200: same level — no level-up, no division change."""
        result = PlayerService.check_level_up(3000, 3200)

        assert result["level_before"] == 3
        assert result["level_after"] == 3
        assert result["leveled_up"] is False
        assert result["division_before"] == "bronze"
        assert result["division_after"] == "bronze"
        assert result["division_changed"] is False

    def test_xp_70000_to_91000_silver_to_gold(self):
        """XP 70000 → 91000: level 7 → 9, silver → gold — division changes.

        DIVISION_BOUNDARIES = [(0,3),(4,7),(8,10)] so level 7 is silver,
        level 9 is gold (boundary 90000 at index 9, first > 91000 is 1000000 at i=10,
        returns level 9).
        """
        result = PlayerService.check_level_up(70000, 91000)

        # XP 70000 → level 7 (first boundary > 70000 is 71000 at index 8, so i-1=7)
        assert result["level_before"] == 7
        # XP 91000 → level 9 (first boundary > 91000 is 1000000 at index 10, so i-1=9)
        assert result["level_after"] == 9
        assert result["leveled_up"] is True
        # Level 7 is silver (4-7), level 9 is gold (8-10)
        assert result["division_before"] == "silver"
        assert result["division_after"] == "gold"
        assert result["division_changed"] is True


# ===========================================================================
# Tests: add_xp (async, ≤2 mocks)
# ===========================================================================


class TestAddXp:
    """Tests for PlayerService.add_xp — async, uses mock for player_repo."""

    @pytest.mark.asyncio
    async def test_add_xp_no_level_up(self, service, mock_db):
        """Adding XP that does not cross a level boundary — no level-up."""
        player = _make_player(player_id=1, xp=500)
        player.xp_surplus = 0
        service.player_repo.get_by_id = AsyncMock(return_value=player)

        result = await service.add_xp(mock_db, player_id=1, xp_amount=200)

        assert result["player_id"] == 1
        assert result["xp_added"] == 200
        assert result["level_before"] == 1
        assert result["level_after"] == 1
        assert result["leveled_up"] is False
        assert result["division_before"] == "bronze"
        assert result["division_after"] == "bronze"
        assert result["division_changed"] is False
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(player)

    @pytest.mark.asyncio
    async def test_add_xp_causes_level_up_and_sets_surplus(self, service, mock_db):
        """Adding XP that crosses a level boundary sets xp_surplus correctly."""
        # Player starts at 900 XP (level 1), adding 200 pushes to 1100 (level 2)
        # Level 2 boundary = 1050, so surplus = 1100 - 1050 = 50
        player = _make_player(player_id=2, xp=900)
        player.xp_surplus = 0
        service.player_repo.get_by_id = AsyncMock(return_value=player)

        result = await service.add_xp(mock_db, player_id=2, xp_amount=200)

        assert result["level_before"] == 1
        assert result["level_after"] == 2
        assert result["leveled_up"] is True
        # xp_surplus should have been set to 1100 - 1050 = 50
        assert player.xp_surplus == 50
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_xp_causes_division_change(self, service, mock_db):
        """Adding XP that crosses the bronze/silver boundary (level 3→4) triggers division_changed."""
        # Player at 3400 XP (level 3, bronze), adding 200 → 3600 XP (level 4, silver)
        player = _make_player(player_id=3, xp=3400)
        player.xp_surplus = 0
        service.player_repo.get_by_id = AsyncMock(return_value=player)

        result = await service.add_xp(mock_db, player_id=3, xp_amount=200)

        assert result["level_before"] == 3
        assert result["level_after"] == 4
        assert result["leveled_up"] is True
        assert result["division_before"] == "bronze"
        assert result["division_after"] == "silver"
        assert result["division_changed"] is True
        # xp_surplus = 3600 - 3500 = 100
        assert player.xp_surplus == 100

    @pytest.mark.asyncio
    async def test_add_xp_player_not_found_raises(self, service, mock_db):
        """Raises ValueError when the player does not exist."""
        service.player_repo.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.add_xp(mock_db, player_id=99, xp_amount=100)


# ===========================================================================
# Tests: get_promotion_status
# ===========================================================================


class TestGetPromotionStatus:
    """Tests for PlayerService.get_promotion_status."""

    @pytest.mark.asyncio
    async def test_bronze_player_no_xp_not_eligible(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Bronze player with 0 XP is not eligible for promotion."""
        player = _make_player(xp=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.get_promotion_status(mock_db, player_id=1)

        assert result["player_id"] == 1
        assert result["current_tier"] == "Bronze"
        assert result["current_tier_level"] == 1
        assert result["eligible_tier"] == "Bronze"
        assert result["next_tier"] == "Silver"
        assert result["can_promote"] is False
        assert result["xp"] == 0
        assert result["xp_threshold_for_next"] == 1000
        assert result["xp_surplus_for_next"] is None

    @pytest.mark.asyncio
    async def test_bronze_player_sufficient_xp_eligible(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Bronze player with 1500 XP is eligible for Silver promotion."""
        player = _make_player(xp=1500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.get_promotion_status(mock_db, player_id=1)

        assert result["current_tier"] == "Bronze"
        assert result["eligible_tier"] == "Silver"
        assert result["next_tier"] == "Silver"
        assert result["can_promote"] is True
        assert result["xp_threshold_for_next"] == 1000
        assert result["xp_surplus_for_next"] == 500  # 1500 - 1000

    @pytest.mark.asyncio
    async def test_platinum_player_no_next_tier(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Platinum player has no next tier and cannot promote."""
        player = _make_player(xp=20000, tier="Platinum")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.get_promotion_status(mock_db, player_id=1)

        assert result["current_tier"] == "Platinum"
        assert result["next_tier"] is None
        assert result["can_promote"] is False
        assert result["xp_threshold_for_next"] is None
        assert result["xp_surplus_for_next"] is None

    @pytest.mark.asyncio
    async def test_uses_default_thresholds_when_no_config(self, service, mock_db, mock_player_repo, mock_config_repo):
        """When no guild config exists, default thresholds are used."""
        player = _make_player(xp=500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = None

        result = await service.get_promotion_status(mock_db, player_id=1)

        assert result["can_promote"] is False
        assert result["xp_threshold_for_next"] == 1000  # Default Silver threshold

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.get_promotion_status(mock_db, player_id=99)

    @pytest.mark.asyncio
    async def test_bronze_player_with_high_xp_eligible_for_silver_not_gold(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
        """Bronze player with 20000 XP: eligible_tier=Platinum, but next_tier=Silver, can_promote=True."""
        player = _make_player(xp=20000, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.get_promotion_status(mock_db, player_id=1)

        assert result["current_tier"] == "Bronze"
        assert result["eligible_tier"] == "Platinum"
        assert result["next_tier"] == "Silver"
        assert result["can_promote"] is True  # eligible_level(4) >= next_level(2)


# ===========================================================================
# Tests: promote_player
# ===========================================================================


class TestPromotePlayer:
    """Tests for PlayerService.promote_player."""

    @pytest.mark.asyncio
    async def test_promotes_bronze_to_silver_when_eligible(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Bronze player with 1500 XP promotes to Silver."""
        player = _make_player(xp=1500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.promote_player(mock_db, player_id=1)

        assert player.tier == "Silver"
        assert result["old_tier"] == "Bronze"
        assert result["new_tier"] == "Silver"
        assert result["xp"] == player.xp
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(player)

    @pytest.mark.asyncio
    async def test_promotes_silver_to_gold_when_eligible(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Silver player with 6000 XP promotes to Gold."""
        player = _make_player(xp=6000, tier="Silver")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.promote_player(mock_db, player_id=1)

        assert player.tier == "Gold"
        assert result["old_tier"] == "Silver"
        assert result["new_tier"] == "Gold"

    @pytest.mark.asyncio
    async def test_promote_does_not_skip_tier(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Bronze player with 20000 XP promotes to Silver only (no skipping)."""
        player = _make_player(xp=20000, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.promote_player(mock_db, player_id=1)

        assert player.tier == "Silver"
        assert result["new_tier"] == "Silver"
        assert result["eligible_for_next"] is True  # Still eligible for Gold
        assert result["next_tier"] == "Gold"

    @pytest.mark.asyncio
    async def test_raises_when_at_platinum(self, service, mock_db, mock_player_repo):
        """Platinum player cannot promote further."""
        player = _make_player(xp=20000, tier="Platinum")
        mock_player_repo.get_by_id.return_value = player

        with pytest.raises(ValueError, match="Already at maximum tier"):
            await service.promote_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_raises_when_not_eligible(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Bronze player with insufficient XP gets clear error message."""
        player = _make_player(xp=500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        with pytest.raises(ValueError, match="Not eligible for promotion"):
            await service.promote_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_error_message_includes_threshold_and_current_xp(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
        """Error message includes XP threshold and current XP."""
        player = _make_player(xp=500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        with pytest.raises(ValueError, match="1,000"):
            await service.promote_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.promote_player(mock_db, player_id=99)

    @pytest.mark.asyncio
    async def test_xp_preserved_after_promotion(self, service, mock_db, mock_player_repo, mock_config_repo):
        """XP is not modified during promotion (AC-7)."""
        player = _make_player(xp=2500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.promote_player(mock_db, player_id=1)

        assert result["xp"] == player.xp  # XP unchanged
        assert player.xp == 2500  # Original XP preserved

    @pytest.mark.asyncio
    async def test_eligible_for_next_false_when_not_enough_xp(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
        """After promoting, eligible_for_next is False if XP doesn't reach the tier after next."""
        player = _make_player(xp=1500, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        )

        result = await service.promote_player(mock_db, player_id=1)

        assert result["new_tier"] == "Silver"
        assert result["eligible_for_next"] is False  # 1500 < 5000 (Gold threshold)
        assert result["next_tier"] == "Gold"

    @pytest.mark.asyncio
    async def test_uses_default_thresholds_when_no_config(self, service, mock_db, mock_player_repo, mock_config_repo):
        """When no guild config, default thresholds determine eligibility."""
        player = _make_player(xp=1200, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = None

        result = await service.promote_player(mock_db, player_id=1)

        assert result["new_tier"] == "Silver"
