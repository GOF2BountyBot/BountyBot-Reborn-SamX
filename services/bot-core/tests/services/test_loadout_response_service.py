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
        secondary_weapons=[],
        secondary_ammo={},
    )


def _ship():
    return SimpleNamespace(
        name="Wraith",
        armour=95,
        cargo=20,
        value=2000,
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
        value=2500,
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


def _compressor_module_rhoda():
    return SimpleNamespace(
        name="Rhoda Blackhole",
        emoji="<:rhoda:1>",
        type="CompressorModule",
        value=900,
        tech_level=3,
        extra_atts={"cargoMultiplier": 2.0},
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
        # Total value = 2000 (base hull) + 1000 (weapon) + 500 (D'iol)
        assert result.ship_stats.total_value == 3500
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

    async def test_player_secondaries_populated_with_rounds(self):
        """build_player_loadout surfaces secondary_weapons with ammo rounds (CI-28)."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=[],
            turrets=[],
            secondary_weapons=["Edo Torpedo", "S'koon Missile"],
            secondary_ammo={"Edo Torpedo": 5, "S'koon Missile": 3},
        )
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        # Return a stub item for secondaries
        svc.item_repo.get_by_name = AsyncMock(return_value=SimpleNamespace(emoji="<:edo:1>", dps=None, value=800))
        db = _make_db_session(player_ship=ps, ship=ship, module_factory=lambda n: None)

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        assert len(result.secondaries) == 2
        # First secondary: Edo Torpedo with 5 rounds
        assert result.secondaries[0].name == "Edo Torpedo"
        assert result.secondaries[0].rounds == 5
        # Second secondary: S'koon Missile with 3 rounds
        assert result.secondaries[1].name == "S'koon Missile"
        assert result.secondaries[1].rounds == 3
        # Total value = 2000 (base hull) + secondaries per round: 800×5 + 800×3
        assert result.ship_stats.total_value == 8400

    async def test_secondary_total_value_edge_rounds(self):
        """Secondary value edges: 0 rounds contributes nothing; no ammo entry counts once."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=[],
            turrets=[],
            secondary_weapons=["Edo Torpedo", "S'koon Missile"],
            secondary_ammo={"Edo Torpedo": 0},  # depleted stack; S'koon has no sidecar entry
        )
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=SimpleNamespace(emoji="<:edo:1>", dps=None, value=800))
        db = _make_db_session(player_ship=ps, ship=ship, module_factory=lambda n: None)

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        # 2000 (base hull) + Edo Torpedo 800×0 rounds = 0 + S'koon Missile (no ammo entry → once) 800
        assert result.ship_stats.total_value == 2800

    async def test_player_no_secondaries_returns_empty_list(self):
        """build_player_loadout returns secondaries=[] when ship has none equipped (CI-28)."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=[],
            turrets=[],
            secondary_weapons=[],
            secondary_ammo={},
        )
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)
        db = _make_db_session(player_ship=ps, ship=ship, module_factory=lambda n: None)

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        assert result.secondaries == []

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

    async def test_two_compressor_modules_stack_additively(self):
        """CompressorModule bonuses stack additively, not multiplicatively (issue #36):
        a +25% and a +100% compressor together give +125%, not +150% (1.25 × 2.0)."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=["AutoPacker 2", "Rhoda Blackhole"],
            turrets=[],
        )
        ship = _ship()  # base cargo=20

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)

        def _module_factory(n):
            if n == "AutoPacker 2":
                return _compressor_module()
            if n == "Rhoda Blackhole":
                return _compressor_module_rhoda()
            return None

        db = _make_db_session(player_ship=ps, ship=ship, module_factory=_module_factory)

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        # Additive: 20 × (1 + 0.25 + 1.00) = 20 × 2.25 = 45 (not 20 × 1.25 × 2.0 = 50)
        assert result.ship_stats.cargo == 45


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

    async def test_bounty_secondaries_populated_with_rounds(self):
        """build_bounty_loadout surfaces criminal secondaries with rounds count (CI-28)."""
        criminal_ship = {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [],
            "turrets": [],
            "modules": [],
            "secondaries": [
                {"name": "Edo Torpedo", "emoji": "<:edo:1>", "dps": 0.0, "value": 800, "rounds": 5},
                {"name": "S'koon Missile", "emoji": "<:skoon:2>", "dps": 0.0, "value": 600, "rounds": 3},
            ],
        }
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert len(result.secondaries) == 2
        # First secondary
        assert result.secondaries[0].name == "Edo Torpedo"
        assert result.secondaries[0].emoji == "<:edo:1>"
        assert result.secondaries[0].rounds == 5
        # Second secondary
        assert result.secondaries[1].name == "S'koon Missile"
        assert result.secondaries[1].rounds == 3

    async def test_bounty_empty_secondaries_returns_empty_list(self):
        """build_bounty_loadout returns secondaries=[] when criminal_ship has none (CI-28)."""
        criminal_ship = self._default_criminal_ship()  # contains no 'secondaries' key
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        assert result is not None
        assert result.secondaries == []

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

    async def test_criminal_two_compressor_modules_stack_additively(self):
        """Criminal-ship CompressorModules stack additively, not multiplicatively (issue #36)."""
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
                },
                {
                    "name": "Rhoda Blackhole",
                    "type": "CompressorModule",
                    "extra_atts": {"cargoMultiplier": 2.0},
                    "value": 900,
                },
            ],
        }
        bounty = self._make_bounty(criminal_ship=criminal_ship)
        ship = _interceptor_ship(cargo=20)

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=5)

        # Additive: 20 × (1 + 0.5 + 1.0) = 20 × 2.5 = 50 (not 20 × 1.5 × 2.0 = 60)
        assert result.ship_stats.cargo == 50


# ---------------------------------------------------------------------------
# A.48 — criminal-only module dedup tests
# ---------------------------------------------------------------------------


class TestCriminalModuleDedup:
    """A.48: dedup CabinModule + CompressorModule (criminal only).

    The dedup is a pure presentation transform applied inside
    `build_bounty_loadout`. It MUST NOT touch `build_player_loadout` (HARD
    invariant per A.48). All other module subtypes pass through unchanged.
    """

    def _make_bounty(self, criminal_ship):
        return SimpleNamespace(
            id=99,
            criminal_name="Pal Tyyrt",
            criminal_faction="Terran",
            tech_level=10,
            criminal_ship=criminal_ship,
        )

    def _criminal_ship_with_modules(self, modules):
        return {
            "ship_name": "Darkzov",
            "ship_armour": 200,
            "total_hp": 500,
            "weapons": [],
            "turrets": [],
            "modules": modules,
        }

    def _module_dict(self, name, mtype):
        return {"name": name, "type": mtype, "value": 100}

    async def test_compressor_module_x9_collapses_to_xN_marker(self):
        """9× Rhoda Blackhole CompressorModule → single 'Rhoda Blackhole x9' entry (A.48 root cause)."""
        modules = [self._module_dict("Rhoda Blackhole", "CompressorModule") for _ in range(9)]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert len(result.modules) == 1
        assert result.modules[0].name == "Rhoda Blackhole x9"
        assert result.modules[0].type == "CompressorModule"

    async def test_cabin_module_x3_collapses_to_xN_marker(self):
        """3× CabinModule of the same name → 'Name x3'."""
        modules = [self._module_dict("Comfy Cabin", "CabinModule") for _ in range(3)]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert len(result.modules) == 1
        assert result.modules[0].name == "Comfy Cabin x3"
        assert result.modules[0].type == "CabinModule"

    async def test_compressor_x1_left_alone(self):
        """A single CompressorModule (N=1) is rendered with its bare name (no x1 suffix)."""
        modules = [self._module_dict("Rhoda Blackhole", "CompressorModule")]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert len(result.modules) == 1
        assert result.modules[0].name == "Rhoda Blackhole"

    async def test_shield_module_not_deduped(self):
        """Three different ShieldModule rows render individually (no dedup)."""
        modules = [
            self._module_dict("Targe Shield", "ShieldModule"),
            self._module_dict("Targe Shield", "ShieldModule"),
            self._module_dict("Targe Shield", "ShieldModule"),
        ]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert len(result.modules) == 3
        # All three retain bare name
        assert all(m.name == "Targe Shield" for m in result.modules)

    async def test_armour_module_not_deduped(self):
        """ArmourModule is gameplay-meaningful and not deduped, even when stacked."""
        modules = [self._module_dict("D'iol", "ArmourModule") for _ in range(4)]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert len(result.modules) == 4

    async def test_mixed_dedup_and_passthrough_preserves_order(self):
        """Mixed loadout: dedup the eligible runs, passthrough the rest, preserve original order."""
        modules = [
            self._module_dict("Targe Shield", "ShieldModule"),
            self._module_dict("Rhoda Blackhole", "CompressorModule"),
            self._module_dict("Rhoda Blackhole", "CompressorModule"),
            self._module_dict("D'iol", "ArmourModule"),
            self._module_dict("Rhoda Blackhole", "CompressorModule"),  # still dedups even if non-adjacent
            self._module_dict("Comfy Cabin", "CabinModule"),
            self._module_dict("Comfy Cabin", "CabinModule"),
        ]
        bounty = self._make_bounty(self._criminal_ship_with_modules(modules))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        names = [m.name for m in result.modules]
        # ShieldModule + first CompressorModule run (3 total) + ArmourModule + first CabinModule run (2 total)
        assert names == [
            "Targe Shield",
            "Rhoda Blackhole x3",
            "D'iol",
            "Comfy Cabin x2",
        ]

    async def test_pal_tyyrt_loadout_module_count_after_dedup(self):
        """A.48 GAP-002 (spec §6): Pal Tyyrt's exact criminal loadout deduplicates the
        9× Rhoda Blackhole CompressorModule entries into a single 'Rhoda Blackhole x9'
        entry.  The remaining 5 distinct module types pass through unchanged.

        Input: 14 modules (Targe Shield, Phoenix SIS, Spectral Filter Omega, Rhoda Vortex,
        Rhoda Blackhole × 9).
        Expected: 5 visible module entries after dedup (4 distinct + 1 grouped).
        """
        pal_tyyrt_ship = {
            "ship_name": "Darkzov",
            "ship_armour": 200,
            "total_hp": 740,
            "weapons": [
                {"name": "Mimung Blaster", "dps": 30.0, "value": 1500},
                {"name": "MaxHeat o20", "dps": 25.5, "value": 1200},
                {"name": "MaxHeat o20", "dps": 25.5, "value": 1200},
                {"name": "Mass Driver MD 12", "dps": 29.5, "value": 1800},
            ],
            "turrets": [
                {"name": "PE Ambipolar-5", "dps": 18.0, "value": 1100},
            ],
            "modules": [
                {"name": "Targe Shield", "type": "ShieldModule", "value": 500},
                {"name": "Phoenix SIS", "type": "ShieldInjectorModule", "value": 700},
                {"name": "Spectral Filter Omega", "type": "SpectralFilterModule", "value": 600},
                {"name": "Rhoda Vortex", "type": "TimeExtenderModule", "value": 800},
                # 9× Rhoda Blackhole (CompressorModule) — the A.48 root cause
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
                {"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300},
            ],
        }
        bounty = self._make_bounty(pal_tyyrt_ship)
        ship = _interceptor_ship(name="Darkzov", cargo=80, handling=50, armour=200)

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=99)

        assert result is not None
        # After dedup: 4 non-deduped modules + 1 collapsed Rhoda Blackhole x9 = 5 total.
        assert len(result.modules) == 5, (
            f"Expected 5 modules after dedup (4 distinct + 1 Rhoda Blackhole x9), "
            f"got {len(result.modules)}: {[m.name for m in result.modules]}"
        )
        # The deduped entry must carry the x9 suffix.
        rhoda_entry = next((m for m in result.modules if "Rhoda Blackhole" in m.name), None)
        assert rhoda_entry is not None, "Rhoda Blackhole entry missing from deduped modules"
        assert rhoda_entry.name == "Rhoda Blackhole x9", f"Expected 'Rhoda Blackhole x9', got {rhoda_entry.name!r}"
        # No individual Rhoda Blackhole entries should remain (sanity: no 'x10' or bare name).
        for m in result.modules:
            assert "x10" not in m.name, f"Unexpected xN suffix ≥ 10: {m.name!r}"

    async def test_player_loadout_never_deduped(self):
        """HARD INVARIANT (A.48): build_player_loadout MUST NOT dedup any modules."""
        # Player path: 9 identical CompressorModule entries via player_ship.modules
        player = _player()
        user = _user()

        ps = SimpleNamespace(
            id=10,
            ship_name="Darkzov",
            nickname=None,
            weapons=[],
            modules=["Rhoda Blackhole"] * 9,
            turrets=[],
        )
        ship = _ship()

        # ItemRepository / Module lookup return the same compressor record each time.
        compressor_module = SimpleNamespace(
            name="Rhoda Blackhole",
            emoji=None,
            type="CompressorModule",
            value=100,
            tech_level=8,
            extra_atts={"cargoMultiplier": 1.1},
        )

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)
        db = _make_db_session(
            player_ship=ps,
            ship=ship,
            module_factory=lambda n: compressor_module if n == "Rhoda Blackhole" else None,
        )

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        # All 9 entries returned as separate items — INVARIANT verified.
        assert len(result.modules) == 9
        assert all(m.name == "Rhoda Blackhole" for m in result.modules)


# ---------------------------------------------------------------------------
# modules_total_count field tests
# ---------------------------------------------------------------------------


class TestModulesTotalCount:
    """modules_total_count must be the PRE-dedup count for criminal path and
    equal to len(modules) for player path (never deduped).
    """

    def _make_bounty(self, criminal_ship):
        return SimpleNamespace(
            id=77,
            criminal_name="Doni Trillyx",
            criminal_faction="Terran",
            tech_level=5,
            criminal_ship=criminal_ship,
        )

    async def test_criminal_modules_total_count_is_pre_dedup_count(self):
        """Criminal with 3× CompressorModule + 2 others → modules_total_count==5, len(modules)==3 after dedup."""
        criminal_ship = {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [],
            "turrets": [],
            "modules": [
                {"name": "Static Thrust", "type": "ThrusterModule", "value": 400},
                {"name": "Targe Shield", "type": "ShieldModule", "value": 500},
                {"name": "ZMI Optistore", "type": "CompressorModule", "value": 300},
                {"name": "ZMI Optistore", "type": "CompressorModule", "value": 300},
                {"name": "ZMI Optistore", "type": "CompressorModule", "value": 300},
            ],
        }
        bounty = self._make_bounty(criminal_ship)
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=77)

        assert result is not None
        # Dedup collapses 3× ZMI Optistore into 1 entry → list shrinks 5→3
        assert len(result.modules) == 3, f"Expected 3 deduped module entries, got {len(result.modules)}"
        # But modules_total_count must reflect the true pre-dedup count
        assert result.modules_total_count == 5, (
            f"Expected modules_total_count==5 (pre-dedup), got {result.modules_total_count}"
        )

    async def test_criminal_modules_total_count_no_dedup_needed(self):
        """Criminal with no dedup-eligible duplicates: modules_total_count == len(modules)."""
        criminal_ship = {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [],
            "turrets": [],
            "modules": [
                {"name": "Static Thrust", "type": "ThrusterModule", "value": 400},
                {"name": "Targe Shield", "type": "ShieldModule", "value": 500},
            ],
        }
        bounty = self._make_bounty(criminal_ship)
        ship = _interceptor_ship()

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=77)

        assert result is not None
        assert len(result.modules) == 2
        assert result.modules_total_count == 2

    async def test_player_modules_total_count_equals_len_modules(self):
        """Player path: modules_total_count == len(modules) (players are never deduped)."""
        player = _player()
        user = _user()
        ps = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=["D'iol", "D'iol", "AutoPacker 2"],
            turrets=[],
            secondary_weapons=[],
            secondary_ammo={},
        )
        ship = _ship()

        svc = _make_svc(player=player, user=user)
        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)

        def _module_factory(name):
            if name == "D'iol":
                return _diol_module()
            if name == "AutoPacker 2":
                return _compressor_module()
            return None

        db = _make_db_session(player_ship=ps, ship=ship, module_factory=_module_factory)

        result = await svc.build_player_loadout(db, player_id=1, include_cargo=False)

        assert result is not None
        # Player path: 3 modules, all returned individually (no dedup)
        assert len(result.modules) == 3
        assert result.modules_total_count == 3

    async def test_criminal_compressor_x9_modules_total_count_is_13(self):
        """Pal Tyyrt-style: 9× CompressorModule + 4 other modules → modules_total_count==13."""
        modules = (
            [{"name": "Targe Shield", "type": "ShieldModule", "value": 500}]
            + [{"name": "Phoenix SIS", "type": "ShieldInjectorModule", "value": 700}]
            + [{"name": "Spectral Filter Omega", "type": "SpectralFilterModule", "value": 600}]
            + [{"name": "Rhoda Vortex", "type": "TimeExtenderModule", "value": 800}]
            + [{"name": "Rhoda Blackhole", "type": "CompressorModule", "value": 300} for _ in range(9)]
        )
        criminal_ship = {
            "ship_name": "Darkzov",
            "ship_armour": 200,
            "total_hp": 740,
            "weapons": [],
            "turrets": [],
            "modules": modules,
        }
        bounty = self._make_bounty(criminal_ship)
        ship = _interceptor_ship(name="Darkzov", cargo=80, handling=50, armour=200)

        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=ship)

        result = await svc.build_bounty_loadout(db, bounty_id=77)

        assert result is not None
        # After dedup: 4 passthrough + 1 grouped = 5 visible entries
        assert len(result.modules) == 5
        # Pre-dedup total: 4 + 9 = 13
        assert result.modules_total_count == 13


# ---------------------------------------------------------------------------
# Weapon combat-field extraction — damage_per_shot / loading_speed_ms
# ---------------------------------------------------------------------------


class TestWeaponCombatFields:
    """_weapon_combat_fields pulls per-shot damage + reload from a weapon's extra_atts."""

    @staticmethod
    def _fn():
        from services.loadout_response_service import LoadoutResponseService

        return LoadoutResponseService._weapon_combat_fields

    def test_nested_inner_extra_atts(self):
        """Real DB shape: combat fields nested under inner extra_atts (Dark Matter Laser)."""
        item = SimpleNamespace(
            extra_atts={
                "builtIn": False,
                "techLevel": 9,
                "extra_atts": {"damage_per_shot": 60, "loading_speed_ms": 680, "range_m": 3300},
            }
        )
        assert self._fn()(item) == (60, 680)

    def test_secondary_damage_key_fallback(self):
        """Secondaries store per-shot damage under `damage`, not `damage_per_shot`."""
        item = SimpleNamespace(extra_atts={"extra_atts": {"damage": 800, "loading_speed_ms": 3000}})
        assert self._fn()(item) == (800, 3000)

    def test_flat_extra_atts_without_nesting(self):
        """Falls back to the outer dict when there's no inner extra_atts."""
        item = SimpleNamespace(extra_atts={"damage_per_shot": 12, "loading_speed_ms": 500})
        assert self._fn()(item) == (12, 500)

    def test_missing_fields_yield_none(self):
        item = SimpleNamespace(extra_atts={"extra_atts": {"range_m": 5500}})
        assert self._fn()(item) == (None, None)

    def test_non_dict_extra_atts(self):
        assert self._fn()(SimpleNamespace(extra_atts=None)) == (None, None)
        assert self._fn()(None) == (None, None)

    def test_non_numeric_value_coerced_to_none(self):
        item = SimpleNamespace(extra_atts={"extra_atts": {"damage_per_shot": "n/a", "loading_speed_ms": 680}})
        assert self._fn()(item) == (None, 680)


class TestNormalizeWeaponDict:
    """_normalize_weapon_dict projects criminal_ship JSON fields including the new combat fields."""

    @staticmethod
    def _fn():
        from services.loadout_response_service import LoadoutResponseService

        return LoadoutResponseService._normalize_weapon_dict

    def test_primary_weapon_fields(self):
        raw = {
            "name": "Rail Gun",
            "emoji": "<:rg:1>",
            "dps": 88.2,
            "value": 1000,
            "damage_per_shot": 60,
            "loading_speed_ms": 680,
        }
        out = self._fn()(raw)
        assert out["damage_per_shot"] == 60
        assert out["loading_speed_ms"] == 680
        assert "rounds" not in out

    def test_secondary_damage_and_rounds(self):
        raw = {"name": "S'koonn", "dps": 0.0, "value": 5000, "damage": 800, "loading_speed_ms": 3000, "rounds": 1}
        out = self._fn()(raw, include_rounds=True)
        assert out["damage_per_shot"] == 800  # mapped from `damage`
        assert out["loading_speed_ms"] == 3000
        assert out["rounds"] == 1

    def test_float_values_coerced_to_int(self):
        raw = {"name": "W", "damage_per_shot": 8.0, "loading_speed_ms": 600.0}
        out = self._fn()(raw)
        assert out["damage_per_shot"] == 8
        assert out["loading_speed_ms"] == 600

    def test_missing_combat_fields_none(self):
        out = self._fn()({"name": "W"})
        assert out["damage_per_shot"] is None
        assert out["loading_speed_ms"] is None


# ---------------------------------------------------------------------------
# T4b — criminal loot rendered as Cargo Hold contents on the bounty LoadoutResponse
# ---------------------------------------------------------------------------


class TestBuildBountyLootCargo:
    """build_bounty_loadout renders the criminal's rolled loot as its Cargo Hold
    contents — the loot IS the criminal's cargo.

    The loot is persisted at spawn (T4) under criminal_ship["cargo"] = {item_type,
    item_name, quantity} and surfaced in the LoadoutResponse ``cargo`` list with
    ``cargo_total_count`` (rendered as 'Cargo Hold <N/M>'), NOT a separate
    'Loot aboard' field — so ``loot_cargo`` is now always None. The key may be
    ABSENT for legacy / no-roll bounties, which MUST render as empty cargo (never
    an error).
    """

    def _make_bounty(self, *, criminal_ship):
        return SimpleNamespace(
            id=7,
            criminal_name="Dark Mage",
            criminal_faction="Void Syndicate",
            tech_level=3,
            criminal_ship=criminal_ship,
        )

    def _ship_with_cargo(self, cargo):
        return {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [{"name": "Blaster", "dps": 5.2, "value": 500}],
            "modules": [],
            "cargo": cargo,
        }

    async def test_loot_renders_as_cargo_commodity_stack(self):
        """A rolled commodity stack renders as a Cargo Hold item with name/type/quantity."""
        cargo = {"item_type": "commodity", "item_name": "Booze", "quantity": 16}
        bounty = self._make_bounty(criminal_ship=self._ship_with_cargo(cargo))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=7)

        assert result is not None
        assert result.loot_cargo is None  # no separate "Loot aboard" field anymore
        assert len(result.cargo) == 1
        assert result.cargo[0].item_name == "Booze"
        assert result.cargo[0].item_type == "commodity"
        assert result.cargo[0].quantity == 16
        assert result.cargo_total_count == 16

    async def test_loot_renders_as_cargo_qty_one_weapon(self):
        """A qty-1 equippable still renders (quantity always carried, even 1)."""
        cargo = {"item_type": "primary_weapon", "item_name": "AB-1 Retractor", "quantity": 1}
        bounty = self._make_bounty(criminal_ship=self._ship_with_cargo(cargo))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=7)

        assert result.loot_cargo is None
        assert len(result.cargo) == 1
        assert result.cargo[0].item_name == "AB-1 Retractor"
        assert result.cargo[0].quantity == 1
        assert result.cargo_total_count == 1

    async def test_legacy_no_cargo_key_yields_empty_cargo(self):
        """A bounty whose criminal_ship has NO 'cargo' key → empty cargo (graceful)."""
        ship = {
            "ship_name": "Interceptor",
            "ship_armour": 95,
            "total_hp": 200,
            "weapons": [{"name": "Blaster", "dps": 5.2, "value": 500}],
            "modules": [],
        }
        bounty = self._make_bounty(criminal_ship=ship)
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=7)

        assert result is not None
        assert result.loot_cargo is None
        assert result.cargo == []
        assert result.cargo_total_count == 0

    async def test_missing_criminal_ship_has_empty_cargo(self):
        """The 'no criminal_ship' message branch leaves cargo empty."""
        bounty = self._make_bounty(criminal_ship=None)
        svc = _make_svc(bounty=bounty)
        db = MagicMock()

        result = await svc.build_bounty_loadout(db, bounty_id=7)

        assert result is not None
        assert result.loot_cargo is None
        assert result.cargo == []
        assert result.cargo_total_count == 0

    async def test_malformed_cargo_blobs_yield_empty_cargo(self):
        """Malformed cargo shapes (bad type, blank name, non-positive qty) → empty cargo."""
        bad_blobs = [
            {"item_type": "commodity", "item_name": "", "quantity": 5},  # blank name
            {"item_type": "commodity", "item_name": "Booze", "quantity": 0},  # zero qty
            {"item_type": "commodity", "item_name": "Booze", "quantity": -3},  # negative qty
            {"item_type": "commodity", "item_name": "Booze"},  # missing qty
            {"item_type": "commodity", "quantity": 5},  # missing name
            "not-a-dict",  # wrong shape entirely
        ]
        for blob in bad_blobs:
            bounty = self._make_bounty(criminal_ship=self._ship_with_cargo(blob))
            svc = _make_svc(bounty=bounty, criminal=None)
            db = _make_db_session(ship=_interceptor_ship())
            result = await svc.build_bounty_loadout(db, bounty_id=7)
            assert result is not None
            assert result.loot_cargo is None, f"expected None for blob={blob!r}"
            assert result.cargo == [], f"expected empty cargo for blob={blob!r}"
            assert result.cargo_total_count == 0

    async def test_item_type_defaults_empty_when_absent(self):
        """A cargo blob missing item_type still renders with item_type=''."""
        cargo = {"item_name": "Ore Core", "quantity": 8}
        bounty = self._make_bounty(criminal_ship=self._ship_with_cargo(cargo))
        svc = _make_svc(bounty=bounty, criminal=None)
        db = _make_db_session(ship=_interceptor_ship())

        result = await svc.build_bounty_loadout(db, bounty_id=7)

        assert result.loot_cargo is None
        assert len(result.cargo) == 1
        assert result.cargo[0].item_name == "Ore Core"
        assert result.cargo[0].item_type == ""
        assert result.cargo[0].quantity == 8
        assert result.cargo_total_count == 8
