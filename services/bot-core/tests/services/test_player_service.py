"""
Unit tests for PlayerService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root which handles this).
"""

import sys
import types
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

from services.player_service import PlayerService  # noqa: I001


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
    async def test_returns_existing_player_when_found(
        self, service, mock_db, mock_user_repo, mock_player_repo
    ):
        """When a player already exists for the guild, return it without creating a new one."""
        user = _make_user()
        player = _make_player()
        mock_user_repo.get_or_create_user.return_value = user
        mock_player_repo.get_by_user_and_guild.return_value = player

        result = await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)

        assert result is player
        mock_user_repo.get_or_create_user.assert_awaited_once_with(mock_db, 111, None)
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

        result = await service.get_or_create_player(
            mock_db, discord_id=111, guild_id=999, discord_username="TestUser"
        )

        assert result is new_player
        mock_player_repo.add.assert_awaited_once()
        service._create_starter_loadout.assert_awaited_once_with(mock_db, new_player)

    @pytest.mark.asyncio
    async def test_passes_username_to_user_repo(
        self, service, mock_db, mock_user_repo, mock_player_repo
    ):
        """discord_username is forwarded to user_repo.get_or_create_user."""
        user = _make_user()
        player = _make_player()
        mock_user_repo.get_or_create_user.return_value = user
        mock_player_repo.get_by_user_and_guild.return_value = player

        await service.get_or_create_player(mock_db, discord_id=111, guild_id=999, discord_username="Alice")

        mock_user_repo.get_or_create_user.assert_awaited_once_with(mock_db, 111, "Alice")

    @pytest.mark.asyncio
    async def test_uses_zero_credits_when_no_config(
        self, service, mock_db, mock_user_repo, mock_player_repo, mock_config_repo
    ):
        """When no guild config exists, new player gets 0 starting credits."""
        user = _make_user()
        mock_user_repo.get_or_create_user.return_value = user
        mock_player_repo.get_by_user_and_guild.return_value = None
        mock_config_repo.get_by_guild_id.return_value = None

        created_player = _make_player(credits=0)
        mock_player_repo.add.return_value = created_player
        service._create_starter_loadout = AsyncMock()

        await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)

        # Verify a Player was added with credits=0
        added_player = mock_player_repo.add.call_args[0][1]
        assert added_player.credits == 0

    @pytest.mark.asyncio
    async def test_re_raises_exception_from_user_repo(self, service, mock_db, mock_user_repo):
        """Exceptions from user_repo bubble up."""
        mock_user_repo.get_or_create_user.side_effect = RuntimeError("DB down")

        with pytest.raises(RuntimeError, match="DB down"):
            await service.get_or_create_player(mock_db, discord_id=111, guild_id=999)


# ===========================================================================
# Tests: _create_new_player  (internal, tested via integration path)
# ===========================================================================


class TestCreateNewPlayer:
    """Tests for PlayerService._create_new_player."""

    @pytest.mark.asyncio
    async def test_creates_player_with_config_credits(
        self, service, mock_db, mock_config_repo, mock_player_repo
    ):
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
    async def test_calls_create_starter_loadout(
        self, service, mock_db, mock_config_repo, mock_player_repo
    ):
        """_create_starter_loadout is called after player is added."""
        user = _make_user()
        mock_config_repo.get_by_guild_id.return_value = _make_config()
        new_player = _make_player()
        mock_player_repo.add.return_value = new_player
        service._create_starter_loadout = AsyncMock()

        await service._create_new_player(mock_db, user, guild_id=999)

        service._create_starter_loadout.assert_awaited_once_with(mock_db, new_player)

    @pytest.mark.asyncio
    async def test_reraises_exception_on_error(
        self, service, mock_db, mock_config_repo, mock_player_repo
    ):
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
    async def test_updates_xp_successfully(self, service, mock_db, mock_player_repo, mock_config_repo):
        """XP is set on the player object and committed."""
        player = _make_player(xp=0, tier="Bronze")
        player.tier = "Bronze"
        mock_player_repo.get_by_id.return_value = player
        # No config - skip tier update
        mock_config_repo.get_by_guild_id.return_value = None

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
    async def test_clamps_negative_xp_to_zero(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Negative XP is clamped to 0."""
        player = _make_player(xp=100)
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = None

        await service.update_player_xp(mock_db, player_id=1, xp=-50)

        assert player.xp == 0

    @pytest.mark.asyncio
    async def test_clamps_xp_to_max(self, service, mock_db, mock_player_repo, mock_config_repo):
        """XP above 1,000,000 is clamped to 1,000,000."""
        player = _make_player(xp=0)
        mock_player_repo.get_by_id.return_value = player
        mock_config_repo.get_by_guild_id.return_value = None

        await service.update_player_xp(mock_db, player_id=1, xp=9_999_999)

        assert player.xp == 1_000_000

    @pytest.mark.asyncio
    async def test_advances_tier_when_xp_threshold_reached(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
        """Tier is updated when XP crosses a threshold."""
        player = _make_player(xp=0, tier="Bronze")
        player.tier = "Bronze"
        mock_player_repo.get_by_id.return_value = player

        config = _make_config(xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000})
        mock_config_repo.get_by_guild_id.return_value = config

        await service.update_player_xp(mock_db, player_id=1, xp=1500)

        assert player.tier == "Silver"

    @pytest.mark.asyncio
    async def test_does_not_change_tier_without_config(
        self, service, mock_db, mock_player_repo, mock_config_repo
    ):
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
    async def test_prestige_resets_tier_and_xp(self, service, mock_db, mock_player_repo):
        """Platinum player is reset to Bronze tier with XP 0 and prestige_count incremented."""
        player = _make_player(tier="Platinum", xp=20000, prestige_count=0)
        player.tier = "Platinum"
        player.xp = 20000
        player.prestige_count = 0
        mock_player_repo.get_by_id.return_value = player

        await service.prestige_player(mock_db, player_id=1)

        assert player.tier == "Bronze"
        assert player.xp == 0
        assert player.prestige_count == 1
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(player)

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 5 not found"):
            await service.prestige_player(mock_db, player_id=5)

    @pytest.mark.asyncio
    async def test_raises_when_player_not_platinum(self, service, mock_db, mock_player_repo):
        """ValueError raised when player is not Platinum tier."""
        player = _make_player(tier="Gold")
        player.tier = "Gold"
        mock_player_repo.get_by_id.return_value = player

        with pytest.raises(ValueError, match="must be Platinum"):
            await service.prestige_player(mock_db, player_id=1)

    @pytest.mark.asyncio
    async def test_increments_prestige_count_correctly(self, service, mock_db, mock_player_repo):
        """prestige_count increments from existing value."""
        player = _make_player(tier="Platinum", prestige_count=3)
        player.tier = "Platinum"
        player.prestige_count = 3
        mock_player_repo.get_by_id.return_value = player

        await service.prestige_player(mock_db, player_id=1)

        assert player.prestige_count == 4


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
        from persist.repositories.inventory_repository import InventoryRepository
        from persist.repositories.ship_repository import ShipRepository
    We intercept these by pre-populating sys.modules with mock modules.
    """

    def _make_repo_mocks(self, ship_repo_mock, inv_repo_mock):
        """Return (mock_ship_module, mock_inv_module) that expose the given mocks as classes."""
        import types as _types

        ship_mod = _types.ModuleType("persist.repositories.ship_repository")
        ship_mod.ShipRepository = MagicMock(return_value=ship_repo_mock)

        inv_mod = _types.ModuleType("persist.repositories.inventory_repository")
        inv_mod.InventoryRepository = MagicMock(return_value=inv_repo_mock)

        return ship_mod, inv_mod

    @pytest.mark.asyncio
    async def test_creates_betty_with_default_loadout(
        self, service, mock_db, mock_player_repo
    ):
        """ShipRepository.create_or_update is called with Betty ship data."""
        player = _make_player(player_id=7)

        mock_ship_repo = AsyncMock()
        mock_inv_repo = AsyncMock()
        starter_ship = MagicMock()
        starter_ship.id = 42
        mock_ship_repo.create_or_update.return_value = starter_ship

        ship_mod, inv_mod = self._make_repo_mocks(mock_ship_repo, mock_inv_repo)

        with patch.dict(sys.modules, {
            "persist.repositories.ship_repository": ship_mod,
            "persist.repositories.inventory_repository": inv_mod,
        }):
            await service._create_starter_loadout(mock_db, player)

        mock_ship_repo.create_or_update.assert_awaited_once()
        call_args = mock_ship_repo.create_or_update.call_args[0]
        ship_data = call_args[1]
        assert ship_data["ship_name"] == "Betty"
        assert ship_data["player_id"] == 7
        assert ship_data["is_active"] is True

    @pytest.mark.asyncio
    async def test_updates_active_ship_after_creation(
        self, service, mock_db, mock_player_repo
    ):
        """player_repo.update_active_ship is called with the new ship id."""
        player = _make_player(player_id=7)

        mock_ship_repo = AsyncMock()
        mock_inv_repo = AsyncMock()
        starter_ship = MagicMock()
        starter_ship.id = 99
        mock_ship_repo.create_or_update.return_value = starter_ship

        ship_mod, inv_mod = self._make_repo_mocks(mock_ship_repo, mock_inv_repo)

        with patch.dict(sys.modules, {
            "persist.repositories.ship_repository": ship_mod,
            "persist.repositories.inventory_repository": inv_mod,
        }):
            await service._create_starter_loadout(mock_db, player)

        mock_player_repo.update_active_ship.assert_awaited_once_with(mock_db, 7, 99)

    @pytest.mark.asyncio
    async def test_reraises_exception_on_error(
        self, service, mock_db
    ):
        """Exceptions raised in _create_starter_loadout propagate."""
        player = _make_player(player_id=7)

        mock_ship_repo = AsyncMock()
        mock_inv_repo = AsyncMock()
        mock_ship_repo.create_or_update.side_effect = RuntimeError("ship create failed")

        ship_mod, inv_mod = self._make_repo_mocks(mock_ship_repo, mock_inv_repo)

        with patch.dict(sys.modules, {
            "persist.repositories.ship_repository": ship_mod,
            "persist.repositories.inventory_repository": inv_mod,
        }), pytest.raises(RuntimeError, match="ship create failed"):
            await service._create_starter_loadout(mock_db, player)
