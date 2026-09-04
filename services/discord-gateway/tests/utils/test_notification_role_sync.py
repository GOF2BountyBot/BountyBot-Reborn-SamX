"""Tests for sync_guild_notification_roles / sync_notification_roles_all_guilds and job registration.

Covers:
- notification-role-sync recurring job registered with hours interval
- notification-role-sync-startup one-shot job at T+60s registered
- member with missing event role → add_roles called
- member opted out of event → add_roles NOT called
- Member not found in guild → counted as not_found, skipped
- bounty_notifications_enabled=False with bronze_role_id → bronze role NOT added
- bounty_notifications_enabled=True with missing bronze role → bronze role added
- dry_run=True → counts would-be adds, add_roles never called
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger and shared.http_retry before any application imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

_mock_http_retry = types.ModuleType("shared.http_retry")


async def _passthrough(fn, *args, **kwargs):
    return await fn(*args, **kwargs)


_mock_http_retry.with_transient_retry = _passthrough
sys.modules.setdefault("shared.http_retry", _mock_http_retry)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Import modules under test AFTER sys.modules patching
# ---------------------------------------------------------------------------

import utils.autocomplete_state as state_mod
import utils.autocomplete_warm as warm_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    warm_mod._warm_semaphore = None
    yield
    state_mod._initialized = False
    state_mod._http_client = None
    state_mod._api_base = None
    state_mod.player_cache = None
    warm_mod._warm_semaphore = None


# ---------------------------------------------------------------------------
# Job registration tests
# ---------------------------------------------------------------------------


class TestNotificationRoleSyncJobRegistration:
    async def test_recurring_job_registered(self, reset_state):
        """register_warm_jobs registers notification-role-sync as an interval job."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()}, timezone="UTC")
        scheduler.start()
        try:
            bot = MagicMock()
            bot.guilds = []
            warm_mod.register_warm_jobs(scheduler, bot)
            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "notification-role-sync" in job_ids, f"notification-role-sync not in {job_ids}"
        finally:
            scheduler.shutdown(wait=False)

    async def test_startup_oneshot_job_registered(self, reset_state):
        """register_warm_jobs registers notification-role-sync-startup as a date job."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()}, timezone="UTC")
        scheduler.start()
        try:
            bot = MagicMock()
            bot.guilds = []
            warm_mod.register_warm_jobs(scheduler, bot)
            job_ids = [j.id for j in scheduler.get_jobs()]
            assert "notification-role-sync-startup" in job_ids, f"notification-role-sync-startup not in {job_ids}"
        finally:
            scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# sync_notification_roles_all_guilds unit tests
# ---------------------------------------------------------------------------


def _make_mock_member(discord_id: int, roles: list | None = None) -> MagicMock:
    member = MagicMock()
    member.id = discord_id
    member.roles = roles or []
    member.add_roles = AsyncMock()
    return member


def _make_mock_guild(guild_id: int, members: dict) -> MagicMock:
    """guild.get_member returns from members dict; fetch_member raises NotFound for unknowns."""
    guild = MagicMock()
    guild.id = guild_id
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(uid))

    async def _fetch_member(uid):
        if uid in members:
            return members[uid]
        raise Exception("Unknown member")

    guild.fetch_member = _fetch_member

    def _get_role(rid):
        r = MagicMock()
        r.id = rid
        r.name = f"role-{rid}"
        return r

    guild.get_role = MagicMock(side_effect=_get_role)
    return guild


class TestSyncNotificationRolesAllGuilds:
    async def test_adds_event_role_for_opted_in_member(self, reset_state):
        """A member who lacks the event role and has event_notifications_enabled=True gets it added."""
        event_role_id = 4001
        event_role = MagicMock()
        event_role.id = event_role_id

        member = _make_mock_member(discord_id=111, roles=[])
        guild = _make_mock_guild(guild_id=999, members={111: member})
        guild.get_role = MagicMock(return_value=event_role)

        cfg = {
            "event_announcements_role_id": event_role_id,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": None,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 111,
                "tier": "Bronze",
                "bounty_notifications_enabled": False,  # skip tier role
                "shop_notifications_enabled": False,  # skip shop role
                "event_notifications_enabled": True,  # wants event role
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            if "/config/" in url:
                r.json.return_value = cfg
            else:
                r.json.return_value = players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts_map = await warm_mod.sync_notification_roles_all_guilds(bot)

        counts = counts_map[999]
        assert counts["players_scanned"] == 1
        member.add_roles.assert_awaited_once()
        added_roles = member.add_roles.call_args[0]
        assert event_role in added_roles
        assert counts["roles_added"] >= 1

    async def test_skips_add_for_opted_out_member(self, reset_state):
        """A member who has event_notifications_enabled=False does not get the event role added."""
        event_role_id = 4001
        event_role = MagicMock()
        event_role.id = event_role_id

        member = _make_mock_member(discord_id=222, roles=[])
        guild = _make_mock_guild(guild_id=998, members={222: member})
        guild.get_role = MagicMock(return_value=event_role)

        cfg = {
            "event_announcements_role_id": event_role_id,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": None,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 222,
                "tier": "Bronze",
                "bounty_notifications_enabled": False,
                "shop_notifications_enabled": False,
                "event_notifications_enabled": False,  # opted out
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            if "/config/" in url:
                r.json.return_value = cfg
            else:
                r.json.return_value = players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts_map = await warm_mod.sync_notification_roles_all_guilds(bot)

        counts = counts_map[998]
        assert counts["players_scanned"] == 1
        member.add_roles.assert_not_awaited()
        assert counts["roles_added"] == 0

    async def test_counts_not_found_for_missing_member(self, reset_state):
        """A player whose discord_id is not in guild is counted as not_found."""
        event_role_id = 4001

        guild = _make_mock_guild(guild_id=997, members={})  # nobody in guild
        guild.get_role = MagicMock(return_value=MagicMock())

        cfg = {
            "event_announcements_role_id": event_role_id,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": None,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 333,  # not in guild
                "tier": "Bronze",
                "bounty_notifications_enabled": False,
                "shop_notifications_enabled": False,
                "event_notifications_enabled": True,
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            if "/config/" in url:
                r.json.return_value = cfg
            else:
                r.json.return_value = players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts_map = await warm_mod.sync_notification_roles_all_guilds(bot)

        counts = counts_map[997]
        assert counts["not_found"] == 1
        assert counts["roles_added"] == 0

    async def test_bounty_disabled_does_not_add_bronze_role(self, reset_state):
        """Member with bounty_notifications_enabled=False must not get the bronze tier role."""
        bronze_role_id = 5001
        bronze_role = MagicMock()
        bronze_role.id = bronze_role_id

        member = _make_mock_member(discord_id=444, roles=[])
        guild = _make_mock_guild(guild_id=996, members={444: member})
        guild.get_role = MagicMock(return_value=bronze_role)

        cfg = {
            "event_announcements_role_id": None,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 444,
                "tier": "Bronze",
                "bounty_notifications_enabled": False,
                "shop_notifications_enabled": False,
                "event_notifications_enabled": False,
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            r.json.return_value = cfg if "/config/" in url else players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts = await warm_mod.sync_guild_notification_roles(bot, guild)

        member.add_roles.assert_not_awaited()
        assert counts["roles_added"] == 0
        # Confirm bronze role was never passed to add_roles
        for call in member.add_roles.call_args_list:
            assert bronze_role not in call[0]

    async def test_bounty_enabled_missing_bronze_role_adds_it(self, reset_state):
        """Member with bounty_notifications_enabled=True lacking the bronze role gets it added."""
        bronze_role_id = 5001
        bronze_role = MagicMock()
        bronze_role.id = bronze_role_id

        member = _make_mock_member(discord_id=555, roles=[])
        guild = _make_mock_guild(guild_id=995, members={555: member})
        guild.get_role = MagicMock(return_value=bronze_role)

        cfg = {
            "event_announcements_role_id": None,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 555,
                "tier": "Bronze",
                "bounty_notifications_enabled": True,
                "shop_notifications_enabled": False,
                "event_notifications_enabled": False,
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            r.json.return_value = cfg if "/config/" in url else players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts = await warm_mod.sync_guild_notification_roles(bot, guild)

        member.add_roles.assert_awaited_once()
        added = member.add_roles.call_args[0]
        assert bronze_role in added
        assert counts["roles_added"] >= 1

    async def test_dry_run_counts_would_be_adds_without_calling_add_roles(self, reset_state):
        """dry_run=True reports roles_added count but never calls member.add_roles."""
        bronze_role_id = 5001
        bronze_role = MagicMock()
        bronze_role.id = bronze_role_id

        member = _make_mock_member(discord_id=666, roles=[])
        guild = _make_mock_guild(guild_id=994, members={666: member})
        guild.get_role = MagicMock(return_value=bronze_role)

        cfg = {
            "event_announcements_role_id": None,
            "shop_announcements_role_id": None,
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
        }
        players = [
            {
                "user_id": 666,
                "tier": "Bronze",
                "bounty_notifications_enabled": True,
                "shop_notifications_enabled": False,
                "event_notifications_enabled": False,
            }
        ]

        async def _mock_get(url, **kw):
            r = MagicMock()
            r.json.return_value = cfg if "/config/" in url else players
            r.raise_for_status = MagicMock()
            return r

        state_mod._initialized = True
        state_mod._http_client = MagicMock()
        state_mod._http_client.get = _mock_get
        state_mod._api_base = "http://bot-core:8000/api/v1"

        bot = MagicMock()
        bot.guilds = [guild]

        with patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)):
            counts = await warm_mod.sync_guild_notification_roles(bot, guild, dry_run=True)

        member.add_roles.assert_not_awaited()
        assert counts["roles_added"] >= 1, "dry_run should still count would-be adds"
        assert counts["players_scanned"] == 1
