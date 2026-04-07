"""Integration tests for ConfigRepository using SQLite in-memory database."""

import pytest
from persist.models.guild_config import GuildConfig
from persist.repositories.config_repository import ConfigRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def repo() -> ConfigRepository:
    return ConfigRepository()


# -- get_by_id -----------------------------------------------------------------


async def test_get_by_id_returns_config(db_session: AsyncSession, repo: ConfigRepository):
    config = GuildConfig(guild_id=1000, starting_credits=500)
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    result = await repo.get_by_id(db_session, config.id)

    assert result is not None
    assert result.guild_id == 1000
    assert result.starting_credits == 500


async def test_get_by_id_returns_none_for_missing(db_session: AsyncSession, repo: ConfigRepository):
    result = await repo.get_by_id(db_session, 999)
    assert result is None


# -- get_by_name ---------------------------------------------------------------


async def test_get_by_name_raises_not_implemented(db_session: AsyncSession, repo: ConfigRepository):
    with pytest.raises(NotImplementedError):
        await repo.get_by_name(db_session, "anything")


# -- list_all ------------------------------------------------------------------


async def test_list_all(db_session: AsyncSession, repo: ConfigRepository):
    db_session.add(GuildConfig(guild_id=100))
    db_session.add(GuildConfig(guild_id=200))
    await db_session.commit()

    configs = await repo.list_all(db_session)
    assert len(configs) == 2


async def test_list_all_empty(db_session: AsyncSession, repo: ConfigRepository):
    configs = await repo.list_all(db_session)
    assert configs == []


# -- add -----------------------------------------------------------------------


async def test_add_persists_config(db_session: AsyncSession, repo: ConfigRepository):
    config = GuildConfig(guild_id=300, starting_credits=100)
    result = await repo.add(db_session, config)

    assert result.id is not None
    assert result.guild_id == 300

    fetched = await repo.get_by_id(db_session, result.id)
    assert fetched is not None


# -- remove --------------------------------------------------------------------


async def test_remove_deletes_config(db_session: AsyncSession, repo: ConfigRepository):
    config = GuildConfig(guild_id=400)
    await repo.add(db_session, config)

    await repo.remove(db_session, config)

    result = await repo.get_by_guild_id(db_session, 400)
    assert result is None


# -- create_or_update ----------------------------------------------------------


async def test_create_or_update_creates_new(db_session: AsyncSession, repo: ConfigRepository):
    result = await repo.create_or_update(
        db_session,
        {
            "guild_id": 500,
            "starting_credits": 1000,
        },
    )

    assert result.guild_id == 500
    assert result.starting_credits == 1000


async def test_create_or_update_updates_existing(db_session: AsyncSession, repo: ConfigRepository):
    await repo.add(db_session, GuildConfig(guild_id=600, starting_credits=0))

    result = await repo.create_or_update(
        db_session,
        {
            "guild_id": 600,
            "starting_credits": 2000,
        },
    )

    assert result.guild_id == 600
    assert result.starting_credits == 2000


async def test_create_or_update_raises_without_guild_id(db_session: AsyncSession, repo: ConfigRepository):
    with pytest.raises(ValueError, match="guild_id is required"):
        await repo.create_or_update(db_session, {"starting_credits": 100})


# -- get_by_guild_id -----------------------------------------------------------


async def test_get_by_guild_id(db_session: AsyncSession, repo: ConfigRepository):
    await repo.add(db_session, GuildConfig(guild_id=700, starting_credits=50))

    result = await repo.get_by_guild_id(db_session, 700)

    assert result is not None
    assert result.guild_id == 700
    assert result.starting_credits == 50


async def test_get_by_guild_id_not_found(db_session: AsyncSession, repo: ConfigRepository):
    result = await repo.get_by_guild_id(db_session, 999)
    assert result is None


# -- create_default_config -----------------------------------------------------


async def test_create_default_config(db_session: AsyncSession, repo: ConfigRepository):
    config = await repo.create_default_config(db_session, guild_id=800)

    assert config.guild_id == 800
    assert config.starting_credits == 0
    assert config.sale_price_factor == 0.8
    assert config.ship_count_range == {"min": 3, "max": 5}
    assert config.xp_thresholds == {"Silver": 1000, "Gold": 5000, "Platinum": 15000}


# -- update_shop_config --------------------------------------------------------


async def test_update_shop_config(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=900)

    result = await repo.update_shop_config(
        db_session,
        {
            "guild_id": 900,
            "sale_price_factor": 0.5,
            "ship_count_range": {"min": 1, "max": 2},
        },
    )

    assert result.sale_price_factor == 0.5
    assert result.ship_count_range == {"min": 1, "max": 2}


async def test_update_shop_config_raises_without_guild_id(db_session: AsyncSession, repo: ConfigRepository):
    with pytest.raises(ValueError, match="guild_id is required"):
        await repo.update_shop_config(db_session, {"sale_price_factor": 0.5})


async def test_update_shop_config_raises_for_missing_config(db_session: AsyncSession, repo: ConfigRepository):
    with pytest.raises(ValueError, match="Config not found"):
        await repo.update_shop_config(db_session, {"guild_id": 99999, "sale_price_factor": 0.5})


# -- reset_to_defaults ---------------------------------------------------------


async def test_reset_to_defaults(db_session: AsyncSession, repo: ConfigRepository):
    # Create and modify a config
    await repo.create_default_config(db_session, guild_id=1100)
    await repo.update_starting_credits(db_session, guild_id=1100, new_credits=9999)

    # Reset
    config = await repo.reset_to_defaults(db_session, guild_id=1100)

    assert config.starting_credits == 0
    assert config.sale_price_factor == 0.8


async def test_reset_to_defaults_creates_if_missing(db_session: AsyncSession, repo: ConfigRepository):
    config = await repo.reset_to_defaults(db_session, guild_id=1200)
    assert config.guild_id == 1200
    assert config.starting_credits == 0


# -- update_admin_role ---------------------------------------------------------


async def test_update_admin_role(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=1300)

    result = await repo.update_admin_role(db_session, guild_id=1300, role_id=555)

    assert result.admin_role_id == 555


async def test_update_admin_role_creates_config_if_missing(db_session: AsyncSession, repo: ConfigRepository):
    result = await repo.update_admin_role(db_session, guild_id=1400, role_id=777)

    assert result.guild_id == 1400
    assert result.admin_role_id == 777


# -- update_starting_credits --------------------------------------------------


async def test_update_starting_credits(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=1500)

    result = await repo.update_starting_credits(db_session, guild_id=1500, new_credits=5000)

    assert result.starting_credits == 5000


async def test_update_starting_credits_negative_raises(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=1600)

    with pytest.raises(ValueError, match="Starting new_credits cannot be negative"):
        await repo.update_starting_credits(db_session, guild_id=1600, new_credits=-100)


async def test_update_starting_credits_creates_config_if_missing(db_session: AsyncSession, repo: ConfigRepository):
    result = await repo.update_starting_credits(db_session, guild_id=1700, new_credits=1000)

    assert result.guild_id == 1700
    assert result.starting_credits == 1000


# -- update_xp_thresholds -----------------------------------------------------


async def test_update_xp_thresholds(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=1800)

    thresholds = {"Silver": 500, "Gold": 2000, "Platinum": 10000}
    result = await repo.update_xp_thresholds(db_session, guild_id=1800, thresholds=thresholds)

    assert result.xp_thresholds == thresholds


async def test_update_xp_thresholds_invalid_order(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=1900)

    with pytest.raises(ValueError, match="ascending order"):
        await repo.update_xp_thresholds(
            db_session,
            guild_id=1900,
            thresholds={
                "Silver": 5000,
                "Gold": 1000,
                "Platinum": 15000,
            },
        )


async def test_update_xp_thresholds_missing_tier(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=2000)

    with pytest.raises(ValueError, match="Invalid threshold"):
        await repo.update_xp_thresholds(
            db_session,
            guild_id=2000,
            thresholds={
                "Silver": 500,
                "Gold": 2000,
            },
        )


# -- get_config_summary --------------------------------------------------------


async def test_get_config_summary(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=2100)

    summary = await repo.get_config_summary(db_session, guild_id=2100)

    assert summary["guild_id"] == 2100
    assert summary["configured"] is True
    assert summary["starting_credits"] == 0
    assert "shop_config" in summary


async def test_get_config_summary_unconfigured(db_session: AsyncSession, repo: ConfigRepository):
    summary = await repo.get_config_summary(db_session, guild_id=9999)

    assert summary["guild_id"] == 9999
    assert summary["configured"] is False


# -- get_all_guild_configs -----------------------------------------------------


async def test_get_all_guild_configs(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=2200)
    await repo.create_default_config(db_session, guild_id=2300)

    configs = await repo.get_all_guild_configs(db_session)

    assert len(configs) == 2
    guild_ids = {c["guild_id"] for c in configs}
    assert guild_ids == {2200, 2300}


# -- delete_guild_config -------------------------------------------------------


async def test_delete_guild_config(db_session: AsyncSession, repo: ConfigRepository):
    await repo.create_default_config(db_session, guild_id=2400)

    deleted = await repo.delete_guild_config(db_session, guild_id=2400)

    assert deleted is True

    result = await repo.get_by_guild_id(db_session, 2400)
    assert result is None


async def test_delete_guild_config_not_found(db_session: AsyncSession, repo: ConfigRepository):
    deleted = await repo.delete_guild_config(db_session, guild_id=9999)
    assert deleted is False
