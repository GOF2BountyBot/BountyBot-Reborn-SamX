"""Phase 3 self-heal tests — Bucket D static-catalog D-010 regression coverage.

The headline D-010 test: preload a static cache, clear() it (as /reload_autocomplete
does), then call the autocomplete handler — it must return the data again because the
refresh_fn (or size-guard) lazily self-heals. A revert to the old no-refresh_fn cache
would leave the dropdown permanently empty.

Covers: bountyCog._systems_cache, adminCog._item_catalog/_ship_catalog,
aboutCog._categories_cache/_objects_cache, skinsCog._ship_skins (size-guard).
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_a, **_k):
    lg = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(lg, m, MagicMock())
    return lg


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    for _mod in list(sys.modules):
        if _mod == "discord" or _mod.startswith("discord."):
            sys.modules.pop(_mod, None)


def _mock_interaction(namespace=None):
    it = MagicMock()
    it.guild_id = 987654321
    it.user = MagicMock()
    it.user.id = 111
    it.namespace = namespace if namespace is not None else MagicMock()
    return it


def _resp(json_data):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_data)
    r.status_code = 200
    return r


@pytest.fixture(scope="module")
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# bountyCog._systems_cache (#14)
# ---------------------------------------------------------------------------


@pytest.fixture
def bounty_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.bountyCog import BountyCog

    cog = BountyCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


class TestSystemsSelfHeal:
    def test_d010_clear_then_autocomplete_self_heals(self, bounty_cog):
        # Preload, then clear() (as /reload_autocomplete does).
        bounty_cog._systems_cache.set("all", ["Sol", "Vega"])
        bounty_cog._systems_cache.clear()
        assert bounty_cog._systems_cache.peek("all") is None

        # refresh_fn re-fetches on the next keystroke → dropdown NOT empty.
        bounty_cog.http_client.get = AsyncMock(return_value=_resp([{"name": "Sol"}, {"name": "Vega"}]))
        res = asyncio.run(bounty_cog.system_autocomplete(_mock_interaction(), ""))
        names = [c.value for c in res]
        assert "Sol" in names and "Vega" in names


# ---------------------------------------------------------------------------
# adminCog catalogs (#19, #21)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


class TestAdminCatalogSelfHeal:
    def test_item_catalog_self_heals_after_clear(self, admin_cog):
        admin_cog._item_catalog.set("primary_weapon", ["Laser"])
        admin_cog._item_catalog.clear()
        assert admin_cog._item_catalog.peek("primary_weapon") is None
        # get() triggers _fetch_item_catalog refresh_fn.
        admin_cog.http_client.get = AsyncMock(return_value=_resp([{"name": "Laser"}, {"name": "Plasma"}]))
        names = asyncio.run(admin_cog._item_catalog.get("primary_weapon"))
        assert names == ["Laser", "Plasma"]

    def test_ship_catalog_self_heals_after_clear(self, admin_cog):
        admin_cog._ship_catalog.set("all", ["Betty"])
        admin_cog._ship_catalog.clear()
        assert admin_cog._ship_catalog.peek("all") is None
        admin_cog.http_client.get = AsyncMock(return_value=_resp([{"name": "Betty"}, {"name": "Avenger"}]))
        names = asyncio.run(admin_cog._ship_catalog.get("all"))
        assert names == ["Betty", "Avenger"]


# ---------------------------------------------------------------------------
# aboutCog (#30, #31, #32)
# ---------------------------------------------------------------------------


@pytest.fixture
def about_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.aboutCog import AboutCog

    cog = AboutCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


class TestAboutSelfHeal:
    def test_categories_self_heal_after_clear(self, about_cog):
        about_cog._categories_cache.set("all", ["module"])
        about_cog._categories_cache.clear()
        about_cog.http_client.get = AsyncMock(return_value=_resp(["module", "ship"]))
        res = asyncio.run(about_cog.category_autocomplete(_mock_interaction(), ""))
        values = [c.value for c in res]
        assert "module" in values and "ship" in values

    def test_objects_self_heal_after_clear(self, about_cog):
        ns = MagicMock()
        ns.category = "module"
        about_cog._objects_cache.set("module", [{"name": "Shield"}])
        about_cog._objects_cache.clear()
        about_cog.http_client.get = AsyncMock(return_value=_resp([{"name": "Shield"}, {"name": "Armour"}]))
        res = asyncio.run(about_cog.object_autocomplete(_mock_interaction(namespace=ns), ""))
        names = [c.value for c in res]
        assert "Shield" in names and "Armour" in names


# ---------------------------------------------------------------------------
# skinsCog size-guard (#27, #28, #29)
# ---------------------------------------------------------------------------


@pytest.fixture
def skins_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.skinsCog import SkinsCog

    cog = SkinsCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    cog.blender_client = MagicMock()
    cog.blender_client.aclose = AsyncMock()
    return cog


class TestSkinsSelfHeal:
    def test_skin_autocomplete_keyed_self_heals(self, skins_cog):
        ns = MagicMock()
        ns.ship = "Betty"
        skins_cog._ship_skins.set("Betty", ["Red"])
        skins_cog._ship_skins.clear()
        # Per-ship refresh_fn re-fetches THIS ship's skins.
        skins_cog.http_client.get = AsyncMock(
            return_value=_resp({"compatible_skins": {"Red": {}, "Blue": {}}})
        )
        res = asyncio.run(skins_cog.skin_autocomplete(_mock_interaction(namespace=ns), ""))
        values = [c.value for c in res]
        assert "Red" in values and "Blue" in values

    @pytest.mark.asyncio
    async def test_enumeration_handler_invokes_background_selfheal_when_empty(self, skins_cog):
        # Empty cache → size-guard invokes the bulk preload in the background
        # (degrade-then-warm; not inline, to respect the 3s autocomplete deadline).
        # Uses a native async test (pytest-asyncio running loop) so the background
        # create_task is scheduled on the same loop the handler runs on.
        skins_cog._ship_skins.clear()
        assert skins_cog._ship_skins.size == 0

        called = {"n": 0}

        async def _spy_preload():
            called["n"] += 1

        skins_cog._preload_ship_skins = _spy_preload

        # Some sibling test modules patch asyncio.create_task on the shared process;
        # ensure the REAL create_task is in place so the size-guard can actually
        # schedule its background task here (production always has the real one).
        import unittest.mock as _um

        real_create_task = asyncio.tasks.create_task
        with _um.patch("asyncio.create_task", real_create_task):
            out = await skins_cog.ship_autocomplete(_mock_interaction(), "")
            task = getattr(skins_cog, "_skins_preload_task", None)
            assert task is not None, "size-guard must schedule a background self-heal task"
            await task  # drain so no pending-task warning

        # THIS keystroke is empty (cache still cold) ...
        assert out == []
        # ... and the background self-heal preload was invoked exactly once.
        assert called["n"] == 1

    def test_enumeration_handler_warm_no_selfheal_scheduled(self, skins_cog):
        # Non-empty cache → no self-heal task scheduled, keys enumerated normally.
        skins_cog._ship_skins.clear()
        skins_cog._skins_preload_task = None
        skins_cog._ship_skins.set("Betty", ["Red"])
        skins_cog._ship_skins.set("Avenger", ["Blue"])
        res = asyncio.run(skins_cog.ship_autocomplete(_mock_interaction(), ""))
        names = [c.value for c in res]
        assert "Betty" in names and "Avenger" in names
        assert getattr(skins_cog, "_skins_preload_task", None) is None
