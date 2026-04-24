"""Unit tests for LoadoutResponseService.

Covers spec §6.2 test file requirements:
  (a) build_player_loadout — happy path (player with full loadout)
  (b) "no active ship" path returns message
  (c) player not found returns None → router converts to 404
  (d) include_cargo=False omits cargo
  (e) build_bounty_loadout — happy path
  (f) missing criminal_ship returns message
  (g) criminal with no Ship in DB (partial stats)
  (h) criminal with no Criminal row (thumbnail_url=None)

Design notes:
- Max 2 mocks per test (per /proj/AGENTS.md).
- Prefer real SimpleNamespace objects with deterministic inputs over MagicMock.
- The `db` session is stubbed as a lightweight async dispatcher (same pattern used in
  TestLoadoutResponseServicePlayerPath in test_players_router.py).
- All tests are async (asyncio_mode = auto from pytest.ini).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Shared DB session factory
# ---------------------------------------------------------------------------


def _make_db_session(*, player_ship=None, ship=None, module_factory=None):
    """Build a lightweight fake AsyncSession that dispatches select() calls.

    Dispatch logic mirrors the approach in test_players_router.py:
    - PlayerShip query → player_ship
    - Ship query        → ship
    - Module query      → module_factory(name) if provided

    Returns a MagicMock whose `execute` coroutine returns deterministic results.
    """
    from persist.models.module import Module as ModuleModel
    from persist.models.player_ship import PlayerShip as PlayerShipModel
    from persist.models.ship import Ship as ShipModel

    async def _execute(stmt):
        result = MagicMock()
        model = stmt.column_descriptions[0]["entity"] if stmt.column_descriptions else None
        if model is PlayerShipModel:
            result.scalars.return_value.first.return_value = player_ship
        elif model is ShipModel:
            result.scalars.return_value.first.return_value = ship
        elif model is ModuleModel:
            try:
                name = stmt.whereclause.right.value
            except Exception:
                name = None
            result.scalars.return_value.first.return_value = module_factory(name) if module_factory else None
        else:
            result.scalars.return_value.first.return_value = None
        return result

    db = MagicMock()
    db.execute = _execute
    return db


_SENTINEL = object()  # sentinel to distinguish "not provided" from "explicit None"


def _make_svc(
    *,
    player=_SENTINEL,
    user=_SENTINEL,
    bounty=_SENTINEL,
    criminal=_SENTINEL,
    inventory=_SENTINEL,
):
    """Build a LoadoutResponseService with repository stubs injected.

    Pass the value you want each repo's lookup to return (including None for "not found").
    Use the default sentinel to leave a repo un-stubbed (it won't be called in that test).

    Design: at most 2 MagicMock objects are created per test call-site.
    """
    from services.loadout_response_service import LoadoutResponseService

    svc = LoadoutResponseService()

    if player is not _SENTINEL:
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=player)

    if user is not _SENTINEL:
        svc.user_repo = MagicMock()
        svc.user_repo.get_by_id = AsyncMock(return_value=user)

    if bounty is not _SENTINEL:
        svc.bounty_repo = MagicMock()
        svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)

    if criminal is not _SENTINEL:
        svc.criminal_repo = MagicMock()
        svc.criminal_repo.get_by_name = AsyncMock(return_value=criminal)

    if inventory is not _SENTINEL:
        svc.inventory_repo = MagicMock()
        svc.inventory_repo.get_player_items = AsyncMock(return_value=inventory)

    return svc


# ---------------------------------------------------------------------------
# Fixtures — game entities
# ---------------------------------------------------------------------------


def _player(*, active_ship_id=10):
    return SimpleNamespace(id=1, user_id=42, active_ship_id=active_ship_id)


def _user():
    return SimpleNamespace(discord_username="Alice")


def _player_ship():
    return SimpleNamespace(
        id=10,
        ship_name="Wraith",
        nickname="Betty",
        weapons=["Pulse Laser"],
        modules=["D'iol"],
        turrets=[],
    )


def _ship():
    return SimpleNamespace(
        name="Wraith",
        armour=95,
        cargo=20,
        emoji="<:wraith:1>",
        icon="https://cdn/wraith.png",
        handling=60,
        max_primaries=2,
        max_secondaries=0,
        max_turrets=0,
        max_modules=4,
    )


def _interceptor_ship(**overrides):
    base = dict(
        name="Interceptor",
        armour=95,
        cargo=45,
        handling=70,
        icon=None,
        max_primaries=1,
        max_secondaries=0,
        max_turrets=0,
        max_modules=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _diol_module():
    return SimpleNamespace(
        name="D'iol",
        emoji="<:diol:1>",
        type="ArmourModule",
        value=500,
        tech_level=1,
        extra_atts={"armour": 40},
    )


def _compressor_module():
    return SimpleNamespace(
        name="AutoPacker 2",
        emoji="<:pack:1>",
        type="CompressorModule",
        value=300,
        tech_level=2,
        extra_atts={"cargoMultiplier": 1.25},
    )


# ---------------------------------------------------------------------------
# Player path tests
# ---------------------------------------------------------------------------


class TestBuildPlayerLoadout:
    async def test_happy_path_full_loadout(self):
        """build_player_loadout returns a fully populated LoadoutResponse (spec §6.2.a)."""
        player = _player()
        user = _user()
        player_ship = _player_ship()
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        # item_repo.get_by_name is used by _build_weapon_items (max 2 mocks — reuse svc)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=SimpleNamespace(emoji="<:pulse:1>", dps=12.0, value=1000))

        db = _make_db_session(
            player_ship=player_ship,
            ship=ship,
            module_factory=lambda n: _diol_module() if n == "D'iol" else None,
        )

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        assert result.subject_kind == "player"
        assert result.subject_name == "Alice"
        assert result.ship_name == "Wraith"
        assert result.ship_nickname == "Betty"
        assert result.ship_icon == "https://cdn/wraith.png"
        assert result.thumbnail_url == "https://cdn/wraith.png"
        assert result.ship_stats.armour == 95
        # Cargo: base 20, no compressor → stays 20
        assert result.ship_stats.cargo == 20
        assert result.ship_stats.handling == 60
        # HP = base 95 + armour bonus 40 + shield 0 = 135
        assert result.ship_stats.hp == 135
        # DPS = 12.0 (Pulse Laser only)
        assert result.ship_stats.dps == 12.0
        # Total value = 1000 (weapon) + 500 (D'iol)
        assert result.ship_stats.total_value == 1500
        assert len(result.weapons) == 1
        assert result.weapons[0].name == "Pulse Laser"
        assert len(result.modules) == 1
        assert result.modules[0].name == "D'iol"
        assert result.modules[0].combat_tier == "combat"
        assert len(result.modules[0].effects) == 1
        assert result.modules[0].effects[0].label == "Armour"

    async def test_no_active_ship_returns_message(self):
        """build_player_loadout returns message='No active ship' (spec §6.2.b)."""
        player = _player(active_ship_id=None)
        user = _user()

        svc = _make_svc(player=player, user=user)
        db = MagicMock()  # db.execute never called in this path

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        assert result.message == "No active ship"
        assert result.subject_name == "Alice"
        assert result.subject_kind == "player"
        assert result.player_id == 1

    async def test_player_not_found_returns_none(self):
        """build_player_loadout returns None when player doesn't exist (spec §6.2.c).

        The router converts None → HTTP 404.
        """
        # player=None → _make_svc doesn't stub player_repo, so we do it directly here
        from services.loadout_response_service import LoadoutResponseService

        svc = LoadoutResponseService()
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=None)
        db = MagicMock()

        result = await svc.build_player_loadout(db, player_id=999, include_cargo=False)

        assert result is None

    async def test_include_cargo_false_omits_cargo(self):
        """include_cargo=False must return cargo=[] and cargo_total_count=0 (spec §6.2.d)."""
        player = _player()
        user = _user()
        player_ship = _player_ship()
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)
        db = _make_db_session(
            player_ship=player_ship,
            ship=ship,
            module_factory=lambda n: None,
        )

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        assert result.cargo == []
        assert result.cargo_total_count == 0

    async def test_compressor_module_multiplies_cargo(self):
        """CompressorModule cargoMultiplier applies to base ship.cargo (spec §2.6 step 9)."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=["AutoPacker 2"],
            turrets=[],
        )
        ship = _ship()  # base cargo=20

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)
        db = _make_db_session(
            player_ship=ps,
            ship=ship,
            module_factory=lambda n: _compressor_module() if n == "AutoPacker 2" else None,
        )

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        # 20 × 1.25 = 25
        assert result.ship_stats.cargo == 25
        assert result.modules[0].type == "CompressorModule"
        assert result.modules[0].combat_tier == "utility"


# ---------------------------------------------------------------------------
# Bounty path tests
# ---------------------------------------------------------------------------


class TestBuildBountyLoadout:
    def _make_bounty(self, *, criminal_ship=None):
        return SimpleNamespace(
            id=5,
            criminal_name="Dark Mage",
            criminal_faction="Void Syndicate",
            tech_level=3,
            criminal_ship=criminal_ship,
        )

    def _default_criminal_ship(self):
        return {
            "ship_name": "Interceptor",
            "ship_emoji": "<:interceptor:1>",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [{"name": "Blaster", "emoji": "<:b:1>", "dps": 5.2, "value": 500}],
            "turrets": [],
            "modules": [
                {
                    "name": "D'iol",
                    "emoji": "<:diol:1>",
                    "type": "ArmourModule",
                    "value": 500,
                    "extra_atts": {"armour": 40},
                }
            ],
        }

    async def test_happy_path_bounty_loadout(self):
        """build_bounty_loadout returns full LoadoutResponse for a bounty (spec §6.2.e)."""
        bounty = self._make_bounty(criminal_ship=self._default_criminal_ship())
        criminal = SimpleNamespace(name="Dark Mage", icon="https://cdn/darkmage.png")
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=criminal)
        # db only used for ship lookup
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert result.subject_kind == "criminal"
        assert result.subject_name == "Dark Mage"
        assert result.subject_description == "Void Syndicate"
        assert result.bounty_id == 5
        assert result.tech_level == 3
        assert result.ship_name == "Interceptor"
        assert result.thumbnail_url == "https://cdn/darkmage.png"
        assert result.ship_stats.hp == 200
        assert result.ship_stats.cargo == 45  # no compressor; base ship cargo
        assert result.ship_stats.handling == 70
        assert len(result.weapons) == 1
        assert result.weapons[0].name == "Blaster"
        assert len(result.modules) == 1
        assert result.modules[0].name == "D'iol"
        assert result.modules[0].type == "ArmourModule"
        assert result.modules[0].combat_tier == "combat"
        # cargo always empty for criminals
        assert result.cargo == []
        assert result.cargo_total_count == 0

    async def test_missing_criminal_ship_returns_message(self):
        """build_bounty_loadout returns message when criminal_ship is None (spec §6.2.f)."""
        bounty = self._make_bounty(criminal_ship=None)

        svc = _make_svc(bounty=bounty)
        db = MagicMock()

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert result.message == "Criminal ship data unavailable"
        assert result.subject_kind == "criminal"
        assert result.subject_name == "Dark Mage"

    async def test_criminal_with_no_ship_in_db_partial_stats(self):
        """build_bounty_loadout handles missing Ship row gracefully (spec §6.2.g).

        ship_stats should have None for DB-sourced fields; cargo-based fields from JSON.
        """
        criminal_ship = self._default_criminal_ship()
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        criminal = SimpleNamespace(name="Dark Mage", icon="https://cdn/darkmage.png")

        svc = _make_svc(bounty=bounty, criminal=criminal)
        # db.execute returns None for ship (ship not in DB)
        db = _make_db_session(ship=None)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert result.subject_kind == "criminal"
        # handling should be None (no ship record for handling lookup)
        assert result.ship_stats.handling is None
        # cargo = 0 (no ship record; base_cargo defaults to 0)
        assert result.ship_stats.cargo == 0
        # weapons and modules still populated from criminal_ship JSON
        assert len(result.weapons) == 1
        assert len(result.modules) == 1

    async def test_criminal_with_no_criminal_row_thumbnail_none(self):
        """build_bounty_loadout returns thumbnail_url=None when Criminal lookup misses (spec §6.2.h)."""
        criminal_ship = self._default_criminal_ship()
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        ship = _interceptor_ship()

        # No criminal row in DB
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert result.thumbnail_url is None
        # Other fields still populated correctly
        assert result.subject_name == "Dark Mage"
        assert result.ship_name == "Interceptor"

    async def test_bounty_not_found_returns_none(self):
        """build_bounty_loadout returns None when bounty doesn't exist → router raises 404."""
        # bounty=None stubs bounty_repo.get_by_id to return None (repo already mocked)
        svc = _make_svc(bounty=None)
        db = MagicMock()  # db is never called when bounty_repo.get_by_id is stubbed

        result = await svc.build_bounty_loadout(db, bounty_id=999)

        assert result is None

    async def test_criminal_compressor_module_multiplies_cargo(self):
        """CompressorModule in criminal_ship multiplies base ship cargo (spec §2.6 step 9)."""
        criminal_ship = {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [],
            "turrets": [],
            "modules": [
                {
                    "name": "AutoPacker 2",
                    "type": "CompressorModule",
                    "extra_atts": {"cargoMultiplier": 1.5},
                    "value": 300,
                }
            ],
        }
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        ship = _interceptor_ship(cargo=20)

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        # 20 × 1.5 = 30
        assert result.ship_stats.cargo == 30
