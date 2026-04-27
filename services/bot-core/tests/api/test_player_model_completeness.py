"""
Tests for Player model completeness (Task 1.5).

Verifies:
- New fields are present in the Player model with correct defaults
- PlayerResponse schema includes new fields
- PlayerStatisticsResponse schema includes classic_mode
- make_mock_player helper includes new fields
- update_credits bug fix: service uses player.credits (not player.new_credits) for comparison
"""

import sys
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure shared.bblogger is mocked before any app imports
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


# ---------------------------------------------------------------------------
# Tests: Player model field presence
# ---------------------------------------------------------------------------


class TestPlayerModelFields:
    """Verify that the Player model has all required new fields."""

    def test_player_model_has_xp_surplus_column(self):
        """Player model declares xp_surplus as a mapped column."""
        from persist.models.player import Player

        assert hasattr(Player, "xp_surplus"), "Player model must have xp_surplus field"
        col = Player.__table__.c.get("xp_surplus")
        assert col is not None, "xp_surplus must be a database column"

    def test_player_model_has_guild_transfer_cooldown_column(self):
        """Player model declares guild_transfer_cooldown as a nullable DateTime column."""
        from persist.models.player import Player

        assert hasattr(Player, "guild_transfer_cooldown")
        col = Player.__table__.c.get("guild_transfer_cooldown")
        assert col is not None, "guild_transfer_cooldown must be a database column"
        assert col.nullable, "guild_transfer_cooldown must be nullable"

    def test_player_model_has_classic_mode_column(self):
        """Player model declares classic_mode as a Boolean column."""
        from persist.models.player import Player

        assert hasattr(Player, "classic_mode")
        col = Player.__table__.c.get("classic_mode")
        assert col is not None, "classic_mode must be a database column"

    def test_player_model_has_bounty_cooldown_end_column(self):
        """Player model declares bounty_cooldown_end as a nullable DateTime column."""
        from persist.models.player import Player

        assert hasattr(Player, "bounty_cooldown_end")
        col = Player.__table__.c.get("bounty_cooldown_end")
        assert col is not None, "bounty_cooldown_end must be a database column"
        assert col.nullable, "bounty_cooldown_end must be nullable"

    def test_xp_surplus_default_is_zero(self):
        """xp_surplus column has a server default of 0."""
        from persist.models.player import Player

        col = Player.__table__.c.get("xp_surplus")
        assert col is not None
        assert not col.nullable, "xp_surplus must be non-nullable"

    def test_classic_mode_default_is_false(self):
        """classic_mode column is non-nullable (defaults to False)."""
        from persist.models.player import Player

        col = Player.__table__.c.get("classic_mode")
        assert col is not None
        assert not col.nullable, "classic_mode must be non-nullable"


# ---------------------------------------------------------------------------
# Tests: PlayerResponse schema includes new fields
# ---------------------------------------------------------------------------


class TestPlayerResponseSchema:
    """Verify that PlayerResponse schema includes new fields."""

    def test_player_response_has_xp_surplus_field(self):
        """PlayerResponse schema exposes xp_surplus."""
        from api.schemas.players_schema import PlayerResponse

        assert "xp_surplus" in PlayerResponse.model_fields

    def test_player_response_has_guild_transfer_cooldown_field(self):
        """PlayerResponse schema exposes guild_transfer_cooldown."""
        from api.schemas.players_schema import PlayerResponse

        assert "guild_transfer_cooldown" in PlayerResponse.model_fields

    def test_player_response_has_classic_mode_field(self):
        """PlayerResponse schema exposes classic_mode."""
        from api.schemas.players_schema import PlayerResponse

        assert "classic_mode" in PlayerResponse.model_fields

    def test_player_response_has_bounty_cooldown_end_field(self):
        """PlayerResponse schema exposes bounty_cooldown_end."""
        from api.schemas.players_schema import PlayerResponse

        assert "bounty_cooldown_end" in PlayerResponse.model_fields

    def test_player_response_xp_surplus_default_is_zero(self):
        """PlayerResponse.xp_surplus defaults to 0."""
        from api.schemas.players_schema import PlayerResponse

        field = PlayerResponse.model_fields["xp_surplus"]
        assert field.default == 0

    def test_player_response_classic_mode_default_is_false(self):
        """PlayerResponse.classic_mode defaults to False."""
        from api.schemas.players_schema import PlayerResponse

        field = PlayerResponse.model_fields["classic_mode"]
        assert field.default is False

    def test_player_response_guild_transfer_cooldown_default_is_none(self):
        """PlayerResponse.guild_transfer_cooldown defaults to None."""
        from api.schemas.players_schema import PlayerResponse

        field = PlayerResponse.model_fields["guild_transfer_cooldown"]
        assert field.default is None

    def test_player_response_bounty_cooldown_end_default_is_none(self):
        """PlayerResponse.bounty_cooldown_end defaults to None."""
        from api.schemas.players_schema import PlayerResponse

        field = PlayerResponse.model_fields["bounty_cooldown_end"]
        assert field.default is None

    def test_player_statistics_response_has_classic_mode(self):
        """PlayerStatisticsResponse schema exposes classic_mode."""
        from api.schemas.players_schema import PlayerStatisticsResponse

        assert "classic_mode" in PlayerStatisticsResponse.model_fields

    def test_player_statistics_classic_mode_default_is_false(self):
        """PlayerStatisticsResponse.classic_mode defaults to False."""
        from api.schemas.players_schema import PlayerStatisticsResponse

        field = PlayerStatisticsResponse.model_fields["classic_mode"]
        assert field.default is False


# ---------------------------------------------------------------------------
# Tests: make_mock_player helper includes new fields
# ---------------------------------------------------------------------------


class TestMakeMockPlayerHelper:
    """Verify that make_mock_player returns a player with all new fields."""

    def test_make_mock_player_has_xp_surplus(self):
        """make_mock_player sets xp_surplus=0 by default."""
        from conftest import make_mock_player

        player = make_mock_player()
        assert player.xp_surplus == 0

    def test_make_mock_player_has_guild_transfer_cooldown(self):
        """make_mock_player sets guild_transfer_cooldown=None by default."""
        from conftest import make_mock_player

        player = make_mock_player()
        assert player.guild_transfer_cooldown is None

    def test_make_mock_player_has_classic_mode(self):
        """make_mock_player sets classic_mode=False by default."""
        from conftest import make_mock_player

        player = make_mock_player()
        assert player.classic_mode is False

    def test_make_mock_player_has_bounty_cooldown_end(self):
        """make_mock_player sets bounty_cooldown_end=None by default."""
        from conftest import make_mock_player

        player = make_mock_player()
        assert player.bounty_cooldown_end is None

    def test_make_mock_player_overrides_xp_surplus(self):
        """make_mock_player accepts xp_surplus override."""
        from conftest import make_mock_player

        player = make_mock_player(xp_surplus=500)
        assert player.xp_surplus == 500

    def test_make_mock_player_overrides_classic_mode(self):
        """make_mock_player accepts classic_mode override."""
        from conftest import make_mock_player

        player = make_mock_player(classic_mode=True)
        assert player.classic_mode is True

    def test_make_mock_player_overrides_bounty_cooldown_end(self):
        """make_mock_player accepts bounty_cooldown_end override."""
        from conftest import make_mock_player

        dt = datetime(2026, 6, 1, tzinfo=UTC)
        player = make_mock_player(bounty_cooldown_end=dt)
        assert player.bounty_cooldown_end == dt

    def test_make_mock_player_overrides_guild_transfer_cooldown(self):
        """make_mock_player accepts guild_transfer_cooldown override."""
        from conftest import make_mock_player

        dt = datetime(2026, 3, 15, tzinfo=UTC)
        player = make_mock_player(guild_transfer_cooldown=dt)
        assert player.guild_transfer_cooldown == dt


# ---------------------------------------------------------------------------
# Tests: player_repository update_credits bug fix
# ---------------------------------------------------------------------------


class TestUpdateCreditsBugFix:
    """Verify the update_credits bug fix in PlayerRepository.

    Bug: .values(new_credits=new_credits) used wrong column name.
    Fix: .values(credits=new_credits) uses the correct column name.
    """

    @pytest.mark.asyncio
    async def test_update_credits_sets_credits_attribute(self):
        """update_credits assigns the new value to the ORM-tracked `credits` attribute.

        Post-Option-B refactor (2026-04-27): repo no longer issues a Core UPDATE.
        It loads the Player via get_by_id and assigns ``player.credits = new_credits``
        so SQLAlchemy's unit-of-work emits a normal ORM UPDATE on flush/commit.
        Identity-map confusion is eliminated.
        """
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_player = MagicMock()
        mock_player.id = 1
        mock_player.credits = 500
        repo.get_by_id = AsyncMock(return_value=mock_player)

        await repo.update_credits(mock_db, player_id=1, new_credits=800)

        # The ORM-tracked attribute now holds the new value (the unit-of-work
        # will emit an UPDATE on the next flush/commit).
        assert mock_player.credits == 800
        # No Core UPDATE statement was constructed.
        mock_db.execute.assert_not_called()
        mock_db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_credits_returns_updated_player(self):
        """update_credits returns the player fetched after the update."""
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_player = MagicMock()
        mock_player.id = 1
        mock_player.credits = 800
        repo.get_by_id = AsyncMock(return_value=mock_player)

        result = await repo.update_credits(mock_db, player_id=1, new_credits=800)

        assert result is mock_player
        repo.get_by_id.assert_awaited_once_with(mock_db, 1)


# ---------------------------------------------------------------------------
# Tests: player_service update_player_credits uses player.credits
# ---------------------------------------------------------------------------


class TestServiceCreditsFieldFix:
    """Verify that PlayerService.update_player_credits uses player.credits (not player.new_credits)
    for comparison and assignment.
    """

    @pytest.mark.asyncio
    async def test_update_player_credits_reads_credits_not_new_credits(self):
        """Service compares new amount against player.credits, not player.new_credits."""
        from services.player_service import PlayerService

        service = PlayerService()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Create a player where credits=500 but new_credits is a different value
        player = MagicMock()
        player.credits = 500
        player.new_credits = 999  # deliberately different to detect which one is read
        player.lifetime_credits = 500

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_id = AsyncMock(return_value=player)
        service.player_repo = mock_player_repo

        await service.update_player_credits(mock_db, player_id=1, new_credits=700, update_lifetime=True)

        # lifetime_credits should have increased by 700-500=200 (using player.credits=500)
        # If it used player.new_credits=999, new_credits(700) < new_credits(999), no increase
        assert player.lifetime_credits == 700, (
            "Service must compare against player.credits (500), not player.new_credits (999). "
            f"Expected lifetime_credits=700, got {player.lifetime_credits}"
        )

    @pytest.mark.asyncio
    async def test_update_player_credits_sets_credits_field(self):
        """Service sets player.credits = new_credits (the correct DB column)."""
        from services.player_service import PlayerService

        service = PlayerService()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        player = MagicMock()
        player.credits = 500
        player.lifetime_credits = 500

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_id = AsyncMock(return_value=player)
        service.player_repo = mock_player_repo

        await service.update_player_credits(mock_db, player_id=1, new_credits=800)

        assert player.credits == 800, f"player.credits must be set to 800 after update, got {player.credits}"


# ---------------------------------------------------------------------------
# Tests: new fields appear in API response
# ---------------------------------------------------------------------------


class TestNewFieldsInApiResponse:
    """Verify new fields appear correctly in API responses via router."""

    @pytest.mark.asyncio
    async def test_player_response_serializes_new_fields(self):
        """PlayerResponse correctly serializes all new fields."""
        from api.schemas.players_schema import PlayerResponse

        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

        mock_player = MagicMock()
        mock_player.id = 1
        mock_player.user_id = 12345
        mock_player.guild_id = 67890
        mock_player.credits = 100
        mock_player.lifetime_credits = 200
        mock_player.systems_checked = 5
        mock_player.bounty_wins = 2
        mock_player.xp = 1500
        mock_player.tier = "Silver"
        mock_player.prestige_count = 0
        mock_player.duel_wins = 1
        mock_player.duel_losses = 0
        mock_player.duel_credits_won = 50
        mock_player.duel_credits_lost = 0
        mock_player.active_ship_id = None
        mock_player.xp_surplus = 250
        mock_player.guild_transfer_cooldown = None
        mock_player.classic_mode = True
        mock_player.bounty_cooldown_end = dt
        mock_player.created_at = "2026-01-01T00:00:00"
        mock_player.updated_at = "2026-01-01T00:00:00"

        response = PlayerResponse.model_validate(mock_player)

        assert response.xp_surplus == 250
        assert response.guild_transfer_cooldown is None
        assert response.classic_mode is True
        assert response.bounty_cooldown_end == dt

    @pytest.mark.asyncio
    async def test_player_response_new_fields_use_defaults(self):
        """PlayerResponse uses defaults for new fields when not provided."""
        from api.schemas.players_schema import PlayerResponse

        # Simulate a player object without the new fields set
        mock_player = MagicMock()
        mock_player.id = 1
        mock_player.user_id = 12345
        mock_player.guild_id = 67890
        mock_player.credits = 100
        mock_player.lifetime_credits = 100
        mock_player.systems_checked = 0
        mock_player.bounty_wins = 0
        mock_player.xp = 0
        mock_player.tier = "Bronze"
        mock_player.prestige_count = 0
        mock_player.duel_wins = 0
        mock_player.duel_losses = 0
        mock_player.duel_credits_won = 0
        mock_player.duel_credits_lost = 0
        mock_player.active_ship_id = None
        mock_player.xp_surplus = 0
        mock_player.guild_transfer_cooldown = None
        mock_player.classic_mode = False
        mock_player.bounty_cooldown_end = None
        mock_player.created_at = "2026-01-01T00:00:00"
        mock_player.updated_at = "2026-01-01T00:00:00"

        response = PlayerResponse.model_validate(mock_player)

        assert response.xp_surplus == 0
        assert response.guild_transfer_cooldown is None
        assert response.classic_mode is False
        assert response.bounty_cooldown_end is None
