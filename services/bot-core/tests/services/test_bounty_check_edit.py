"""
Tests for BountyService._edit_bounty_announcement and its integration
with check_bounty().

These tests verify:
1. check_bounty calls _edit_bounty_announcement after INCORRECT results
2. check_bounty calls _edit_bounty_announcement after CORRECT results
3. _edit_bounty_announcement looks up the DiscordMessage via the repo
4. _edit_bounty_announcement skips the HTTP call when no message is found
5. _edit_bounty_announcement rebuilds the embed with checked-system state
6. _edit_bounty_announcement is non-fatal (exceptions are caught+logged)
7. _edit_bounty_announcement sends the correct PUT to the gateway

The `_edit_bounty_announcement` internal tests exercise the REAL wire-payload
builder (`utils.bounty_announcement_payload.build_bounty_announcement_request`)
and intercept the gateway PUT at the httpx transport layer with respx, asserting
the route, method and JSON body shape.  This is the regression guard for the
class of prod defect where a blanket `patch("httpx.AsyncClient")` "accept
anything, return 200" responder let a malformed payload ship.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger is mocked before importing service code.
# conftest.py already does this at collection time, but we defend against
# running this file in isolation.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from api.schemas.loadout_schema import LoadoutResponse
from persist.models.bounty import Bounty
from persist.models.discord_message import DiscordMessage
from services.bounty_service import BountyService, CheckResult, RewardInfo

# ---------------------------------------------------------------------------
# Gateway env used by the respx-backed edit tests. The real
# _edit_bounty_announcement reads DISCORD_GATEWAY_HOST / GATEWAY_PORT to build
# the PUT URL, so we pin them to a known value the assertions can reconstruct.
# ---------------------------------------------------------------------------
GATEWAY_HOST = "test-gateway"
GATEWAY_PORT = "8888"


def _gateway_put_url(channel_id: int, message_id: int) -> str:
    return f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/announcements/bounty/channel/{channel_id}/message/{message_id}"


# ---------------------------------------------------------------------------
# Module-level autouse fixture: patch LoadoutBuilder.from_player
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_loadout_builder_from_player():
    """Auto-mock LoadoutBuilder.from_player so check_bounty tests don't need real DB calls."""
    from services.combat_models import ShipLoadout

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Unarmed", base_armour=100)),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers / factory functions
# ---------------------------------------------------------------------------


def _make_player(
    player_id: int = 1,
    tier: str = "Bronze",
    classic_mode: bool = False,
    bounty_cooldown_end=None,
    active_ship=None,
) -> SimpleNamespace:
    # Player is left as a SimpleNamespace: the check_bounty trigger tests attach an
    # `active_ship` duck-typed ship (with ad-hoc `armour`), which the real Player
    # ORM relationship would reject. The edit-path tests below do not touch Player.
    return SimpleNamespace(
        id=player_id,
        user_id=player_id * 1000,  # T10: Discord user_id for fight_ships
        guild_id=9999,  # T10: guild_id for fight_ships
        tier=tier,
        classic_mode=classic_mode,
        bounty_cooldown_end=bounty_cooldown_end,
        active_ship=active_ship,
    )


def _make_active_bounty(
    bounty_id: int = 10,
    route: list | None = None,
    answer: str = "Sol",
    criminal_name: str = "Zara",
    criminal_faction: str = "terran",
    checked: dict | None = None,
    guild_id: int = 1,
    division: str = "bronze",
    tech_level: int = 3,
    reward: int = 5000,
    reward_per_sys: int = 500,
    end_time=None,
    criminal_ship: dict | None = None,
) -> Bounty:
    """Return a REAL Bounty ORM instance (constructible without a session)."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol", "Omega"]
    if checked is None:
        checked = {s: -1 for s in route}
    return Bounty(
        id=bounty_id,
        route=route,
        answer=answer,
        criminal_name=criminal_name,
        criminal_faction=criminal_faction,
        checked=checked,
        guild_id=guild_id,
        division=division,
        tech_level=tech_level,
        reward=reward,
        reward_per_sys=reward_per_sys,
        end_time=end_time,
        criminal_ship=criminal_ship,
        status="active",  # X3-bounty: _process_single_bounty_check now checks status under lock
    )


def _make_discord_message(
    message_id: int = 99999,
    channel_id: int = 7777,
    guild_id: int = 1,
) -> DiscordMessage:
    """Return a REAL DiscordMessage ORM instance (constructible without a session)."""
    return DiscordMessage(
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        message_type="bounty_announcement",
        embed_payload="{}",
        reference_id=10,
    )


@contextlib.contextmanager
def _edit_payload_env(discord_msg, *, loadout_subject_name="Zara", raise_on_put=False):
    """Set up the real-wire-payload edit environment for _edit_bounty_announcement.

    Runs the REAL `build_bounty_announcement_request` (payload assembly) and
    intercepts the gateway PUT with respx at the httpx transport layer.

    Three collaborators are patched — all genuine DB/service boundaries that
    `_edit_bounty_announcement` fans out to (justifies >2 mocks per R3):
      - DiscordMessageRepository -> yields `discord_msg` (or None) from the repo
      - CriminalRepository.get_by_name -> None (criminal-icon lookup is a DB read;
        returning None keeps the icon out of the payload so it stays JSON-serialisable)
      - LoadoutResponseService.build_bounty_loadout -> a REAL LoadoutResponse (the
        method itself issues ORM queries; the returned schema flows through
        model_dump() into the real wire payload)

    Yields the respx route object (or None when discord_msg is None) so callers
    can assert `.called`, the request URL/method, and the JSON body.
    """
    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)
    loadout = LoadoutResponse(subject_kind="criminal", subject_name=loadout_subject_name)

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "persist.repositories.criminal_repository.CriminalRepository.get_by_name",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.loadout_response_service.LoadoutResponseService.build_bounty_loadout",
            new=AsyncMock(return_value=loadout),
        ),
        patch.dict(os.environ, {"DISCORD_GATEWAY_HOST": GATEWAY_HOST, "GATEWAY_PORT": GATEWAY_PORT}),
        respx.mock(assert_all_called=False) as router,
    ):
        route = None
        if discord_msg is not None:
            route = router.put(_gateway_put_url(discord_msg.channel_id, discord_msg.message_id))
            if raise_on_put:
                route.mock(side_effect=httpx.ConnectError("Connection refused"))
            else:
                route.mock(return_value=httpx.Response(200))
        yield router, route


def _put_payload(route) -> dict:
    """Decode the JSON body of the single PUT captured by a respx route."""
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> BountyService:
    """Return a BountyService with all repositories replaced by MagicMocks.

    B.49: config_repo is also replaced so that check_bounty can call
    get_by_guild_id without hitting the real DB.  Returns None by default
    so resolve_constant falls back to global GameConstants values.

    X3-bounty: bounty_repo.get_by_id_for_update is configured as an AsyncMock
    with a side_effect that auto-routes by bounty ID from the active bounties
    list set via get_active_by_guild_and_division.return_value.
    """
    svc = BountyService()
    svc.bounty_repo = MagicMock()
    svc.criminal_repo = MagicMock()
    svc.item_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)

    async def _for_update_side_effect(_db, bounty_id):
        rv = svc.bounty_repo.get_active_by_guild_and_division.return_value
        active = rv if isinstance(rv, list) else []
        for b in active:
            if getattr(b, "id", None) == bounty_id:
                return b
        return None

    svc.bounty_repo.get_by_id_for_update = AsyncMock(side_effect=_for_update_side_effect)
    # P6-T1: _build_payout_breakdown now calls player_repo.get_by_ids (batched).
    # Default to empty list; individual tests that need payout content can override.
    svc.player_repo.get_by_ids = AsyncMock(return_value=[])
    # T7 (over-cap lockout): check_bounty's first step reads cargo load via
    # inventory_repo. Default to an empty cargo (zero load → under cap → gate is a
    # no-op). Over-cap behaviour is covered by tests/integration/test_t7_over_cap_lockout.py.
    svc.inventory_repo = MagicMock()
    svc.inventory_repo.get_player_items = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests: check_bounty triggers _edit_bounty_announcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_incorrect_triggers_announcement_edit(service, mock_db):
    """After an INCORRECT check, _edit_bounty_announcement is called with the bounty."""
    player = _make_player()
    bounty = _make_active_bounty(route=["Alpha", "Beta", "Gamma", "Sol", "Omega"], answer="Sol")
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    # Patch _edit_bounty_announcement to track calls
    service._edit_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    # B.12: incorrect outcomes pass captured=False explicitly (was positional-only).
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=False)


@pytest.mark.asyncio
async def test_check_correct_triggers_announcement_edit_win(service, mock_db):
    """After a CORRECT check with combat win, _edit_bounty_announcement is called with captured=True."""
    player = _make_player(classic_mode=True)  # auto-win
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    service._edit_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=True)


@pytest.mark.asyncio
async def test_check_correct_triggers_announcement_edit_loss(service, mock_db):
    """After a CORRECT check with combat loss (Silver player reset), _edit_bounty_announcement is called."""
    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    # Use Silver tier so mandatory combat gate applies (loss triggers _reset_bounty_checks)
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Silver")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Dreadnought", raw_hp=500, raw_dps=99.0, varied_hp=499, varied_dps=99.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Dreadnought",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service = MagicMock()
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service._reset_bounty_checks = AsyncMock()

    service._edit_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is False
    # B.12: combat-loss outcomes pass captured=False explicitly (was positional-only).
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=False)


# ---------------------------------------------------------------------------
# Tests: capture path — edit with captured=True, no delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_correct_win_edits_with_captured_flag(service, mock_db):
    """After CORRECT + combat win (bronze auto-capture), _edit_bounty_announcement is called with captured=True.

    New lifecycle: capture → EDIT to 'Captured!' state (no DELETE).
    The DELETE happens only when the expiry timer fires.
    """
    player = _make_player(classic_mode=True)  # auto-win (bronze)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    service._edit_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Must call edit with captured=True
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=True)


@pytest.mark.asyncio
async def test_check_correct_win_does_not_delete_announcement(service, mock_db):
    """After CORRECT + combat win, _delete_bounty_announcement is NOT called.

    The announcement is kept (showing 'CAPTURED') until the expiry timer fires.
    """
    player = _make_player(classic_mode=True)  # auto-win (bronze)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    service._edit_bounty_announcement = AsyncMock()
    service._delete_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    # _delete_bounty_announcement must NOT be called on capture
    service._delete_bounty_announcement.assert_not_called()


@pytest.mark.asyncio
async def test_check_correct_silver_win_edits_with_captured_flag(service, mock_db):
    """After CORRECT + Silver combat win, _edit_bounty_announcement is called with captured=True.

    The autouse fixture mocks LoadoutBuilder.from_player to return ShipLoadout(ship_name="Unarmed").
    So the mock fight must use winner_name="Unarmed" to simulate a player win.
    """
    active_ship = SimpleNamespace(ship_name="Unarmed", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Silver")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Betty",
        "ship_armour": 50,
        "weapons": [],
        "turrets": [],
    }

    _fs1 = SimpleNamespace(ship_name="Unarmed", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Unarmed",  # player loadout ship_name (see autouse mock)
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service = MagicMock()
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=5000, xp_earned=250, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    service._edit_bounty_announcement = AsyncMock()
    service._delete_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Must call edit with captured=True — no delete
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=True)
    service._delete_bounty_announcement.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _edit_bounty_announcement internals (REAL payload builder + respx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_announcement_looks_up_discord_message(service, mock_db):
    """_edit_bounty_announcement calls get_by_guild_type_and_reference with correct params."""
    bounty = _make_active_bounty(bounty_id=42, guild_id=99)
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty)

    # DiscordMessageRepository.get_by_guild_type_and_reference is looked up by
    # (db, guild_id, "bounty_announcement", bounty.id); grab the patched repo via
    # the route's single PUT having fired (message was found).
    assert route.called
    # The lookup must have used the bounty's guild_id + id as the reference.
    put_url = str(route.calls.last.request.url)
    assert put_url == _gateway_put_url(discord_msg.channel_id, discord_msg.message_id)


@pytest.mark.asyncio
async def test_edit_announcement_skips_when_no_message_found(service, mock_db):
    """When no DiscordMessage exists, no HTTP call is made."""
    bounty = _make_active_bounty(bounty_id=10)

    with _edit_payload_env(None) as (router, _route):
        await service._edit_bounty_announcement(mock_db, bounty)

    # No gateway PUT should have been attempted when no message is found.
    assert len(router.calls) == 0


@pytest.mark.asyncio
async def test_edit_announcement_rebuilds_embed_with_checked_systems(service, mock_db):
    """The REAL payload reflects checked-system markdown (strikethrough + found bold)."""
    # Alpha is 4 stops before Sol → plain "checked"; Sol (answer) → "found".
    bounty = _make_active_bounty(
        bounty_id=10,
        route=["Alpha", "Beta", "Gamma", "Delta", "Sol"],
        answer="Sol",
        checked={"Alpha": 7, "Beta": -1, "Gamma": -1, "Delta": -1, "Sol": 7},
        guild_id=1,
        division="bronze",
        tech_level=3,
        reward=5000,
    )
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty)

    assert route.called
    payload = _put_payload(route)
    assert set(payload) >= {"text_content", "loadout_response", "metadata"}
    # A.48: translation (player IDs → status markdown) now runs for real in
    # utils.bounty_announcement_payload. Assert the Route field in the payload.
    fields = {f["name"]: f["value"] for f in payload["metadata"]["prefix_fields"]}
    assert "~~Alpha~~" in fields["Route"]  # checked → strikethrough
    assert "**Sol**" in fields["Route"]  # answer found → bold
    assert "~~Alpha~~" in fields["Checked Systems"]


@pytest.mark.asyncio
async def test_edit_announcement_rebuilds_embed_with_recently_spotted(service, mock_db):
    """The REAL payload marks systems 1..window stops before the answer as recently-spotted."""
    # window falls back to LEGACY_SPOTTED_WINDOW (2): Gamma (dist 2) and Delta (dist 1)
    # are recently_spotted; Alpha (dist 4) is plain checked; Sol is found.
    bounty = _make_active_bounty(
        bounty_id=11,
        route=["Alpha", "Beta", "Gamma", "Delta", "Sol"],
        answer="Sol",
        checked={"Alpha": 7, "Beta": -1, "Gamma": 7, "Delta": 7, "Sol": 7},
        guild_id=1,
        division="silver",
        tech_level=5,
        reward=10000,
    )
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty)

    assert route.called
    payload = _put_payload(route)
    fields = {f["name"]: f["value"] for f in payload["metadata"]["prefix_fields"]}
    # recently_spotted → **~~system~~**
    assert "**~~Gamma~~**" in fields["Route"]
    assert "**~~Delta~~**" in fields["Route"]
    assert "~~Alpha~~" in fields["Route"]  # plain checked
    assert "**Sol**" in fields["Route"]  # found


@pytest.mark.asyncio
async def test_edit_announcement_non_fatal(service, mock_db):
    """If the HTTP edit fails, the exception is caught and does not propagate."""
    bounty = _make_active_bounty(bounty_id=10)
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg, raise_on_put=True) as (_router, route):
        # Must NOT raise — non-fatal
        await service._edit_bounty_announcement(mock_db, bounty)

    # The PUT was attempted (and raised, which the service swallowed).
    assert route.called


@pytest.mark.asyncio
async def test_edit_announcement_sends_put_to_gateway(service, mock_db):
    """A.48: Verifies PUT goes to the unified bounty-announcement endpoint with structured payload."""
    bounty = _make_active_bounty(bounty_id=77, guild_id=5, criminal_faction="terran")
    discord_msg = _make_discord_message(message_id=12345)

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty)

    assert route.called
    # Method + URL: unified endpoint at the configured gateway host/port.
    assert route.calls.last.request.method == "PUT"
    assert str(route.calls.last.request.url) == _gateway_put_url(discord_msg.channel_id, discord_msg.message_id)

    # Structured payload: text_content + loadout_response + metadata, all present
    # and correctly shaped (this is the wire contract the gateway re-renders).
    payload = _put_payload(route)
    assert set(payload) >= {"text_content", "loadout_response", "metadata"}
    assert payload["loadout_response"]["subject_kind"] == "criminal"
    metadata = payload["metadata"]
    assert metadata["title"] == "Zara"  # non-captured title == criminal name
    assert metadata["captured"] is False
    assert metadata["footer_text"] == "terran"
    assert metadata["reward"] == 5000
    assert isinstance(metadata["prefix_fields"], list)
    # Edits suppress the role mention (no <@&...> text_content).
    assert payload["text_content"] is None


# ---------------------------------------------------------------------------
# Tests: combat loss path still only edits (no delete, no captured flag)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_correct_loss_edits_announcement_no_captured_flag(service, mock_db):
    """After CORRECT + combat loss (Silver player reset), _edit_bounty_announcement is called
    WITHOUT captured=True — the bounty escapes and stays active.
    """
    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    # Silver tier: mandatory combat gate
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Silver")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Dreadnought", raw_hp=500, raw_dps=99.0, varied_hp=499, varied_dps=99.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Dreadnought",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service = MagicMock()
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.update = AsyncMock(return_value=bounty)
    service._reset_bounty_checks = AsyncMock()

    service._edit_bounty_announcement = AsyncMock()
    service._delete_bounty_announcement = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is False
    # B.12: On Silver+ loss the announcement is still edited (bounty stays active).
    # The "no captured flag" semantics now means captured=False is passed explicitly.
    service._edit_bounty_announcement.assert_called_once_with(mock_db, bounty, captured=False)
    service._delete_bounty_announcement.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _edit_bounty_announcement captured-state wire payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_announcement_passes_captured_true_to_builder(service, mock_db):
    """When captured=True, the REAL payload shows the CAPTURED title/color/state."""
    bounty = _make_active_bounty(bounty_id=10, guild_id=1, criminal_name="Zara")
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty, captured=True)

    assert route.called
    metadata = _put_payload(route)["metadata"]
    # A.48 captured wire shape: green color, CAPTURED title, captured flag, and the
    # "Bounty Ends" header value reads "**Captured**".
    assert metadata["captured"] is True
    assert metadata["title"] == "✅ Zara — CAPTURED"
    assert metadata["color"] == 3066993  # _CAPTURED_COLOR (green)
    fields = {f["name"]: f["value"] for f in metadata["prefix_fields"]}
    assert fields["Bounty Ends"] == "**Captured**"


@pytest.mark.asyncio
async def test_edit_announcement_passes_captured_false_by_default(service, mock_db):
    """When captured is not passed, the REAL payload is in the normal (non-captured) state."""
    bounty = _make_active_bounty(bounty_id=11, guild_id=1, criminal_name="Zara", criminal_faction="terran")
    discord_msg = _make_discord_message()

    with _edit_payload_env(discord_msg) as (_router, route):
        await service._edit_bounty_announcement(mock_db, bounty)

    assert route.called
    metadata = _put_payload(route)["metadata"]
    # Non-captured: plain criminal-name title, faction color, captured flag False.
    assert metadata["captured"] is False
    assert metadata["title"] == "Zara"
    assert metadata["color"] == 15844367  # FACTION_COLORS["terran"]
