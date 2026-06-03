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
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

from services.bounty_service import BountyService, CheckResult, RewardInfo

# ---------------------------------------------------------------------------
# Module-level autouse fixture: patch LoadoutBuilder.from_player
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Minimal BountyAnnouncementRequest-shaped dict returned by the real helper.
# Used when a test only needs the call to complete without asserting on the
# return value (e.g. tests verifying HTTP PUT behaviour or non-fatal paths).
# ---------------------------------------------------------------------------


def _make_request_payload(bounty=None, *, captured=False, route_map_url=None, bounty_hunter_role_id=None):
    """Return a minimal BountyAnnouncementRequest-shaped dict (wire shape from A.48)."""
    name = getattr(bounty, "criminal_name", None) or "Unknown"
    faction = getattr(bounty, "criminal_faction", None)
    title = f"✅ {name} — CAPTURED" if captured else name
    return {
        "text_content": (f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id else None),
        "loadout_response": {
            "subject_kind": "criminal",
            "subject_name": name,
        },
        "metadata": {
            "title": title,
            "color": 0,
            "footer_text": faction,
            "image_url": route_map_url,
            "prefix_fields": [],
            "suffix_fields": [],
        },
    }


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
) -> SimpleNamespace:
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol", "Omega"]
    if checked is None:
        checked = {s: -1 for s in route}
    return SimpleNamespace(
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
    )


def _make_discord_message(
    message_id: int = 99999,
    channel_id: int = 7777,
    guild_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="some-uuid",
        message_id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        message_type="bounty_announcement",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> BountyService:
    """Return a BountyService with all repositories replaced by MagicMocks.

    B.49: config_repo is also replaced so that check_bounty can call
    get_by_guild_id without hitting the real DB.  Returns None by default
    so resolve_constant falls back to global GameConstants values.
    """
    svc = BountyService()
    svc.bounty_repo = MagicMock()
    svc.criminal_repo = MagicMock()
    svc.item_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)
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
# Tests: _edit_bounty_announcement internals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_announcement_looks_up_discord_message(service, mock_db):
    """_edit_bounty_announcement calls get_by_guild_type_and_reference with correct params."""
    bounty = _make_active_bounty(bounty_id=42, guild_id=99)
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_request_payload(bounty)),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty)

    mock_msg_repo.get_by_guild_type_and_reference.assert_called_once_with(mock_db, 99, "bounty_announcement", 42)


@pytest.mark.asyncio
async def test_edit_announcement_skips_when_no_message_found(service, mock_db):
    """When no DiscordMessage exists, no HTTP call is made."""
    bounty = _make_active_bounty(bounty_id=10)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=None)

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        await service._edit_bounty_announcement(mock_db, bounty)

    # httpx.AsyncClient should NOT have been instantiated when no message is found
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_edit_announcement_rebuilds_embed_with_checked_systems(service, mock_db):
    """The builder is called with checked systems translated to the correct format."""
    # Alpha is 3 stops before Sol (distance=3), so it's "checked" not "recently_spotted"
    # Gamma is 1 stop before Sol, so it would be "recently_spotted"
    bounty = _make_active_bounty(
        bounty_id=10,
        route=["Alpha", "Beta", "Gamma", "Delta", "Sol"],
        answer="Sol",
        # Alpha checked by player 7 (3 stops before Sol → plain "checked"),
        # Sol (answer) found by player 7,
        # Beta unchecked
        checked={"Alpha": 7, "Beta": -1, "Gamma": -1, "Delta": -1, "Sol": 7},
        guild_id=1,
        division="bronze",
        tech_level=3,
        reward=5000,
    )
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    mock_helper = AsyncMock(return_value=_make_request_payload(bounty))

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty)

    # A.48: the helper is invoked with the bounty object (which carries the raw checked
    # dict).  Translation (player IDs → status strings) is the helper's responsibility,
    # exercised by tests/test_bounty_announcement_payload.py.
    # Here we assert that build_bounty_announcement_request was called and received the
    # bounty whose .checked dict matches what the test set up.
    mock_helper.assert_awaited_once()
    called_bounty = mock_helper.call_args.args[1]
    assert called_bounty.checked == bounty.checked


@pytest.mark.asyncio
async def test_edit_announcement_rebuilds_embed_with_recently_spotted(service, mock_db):
    """A.48: the helper receives the bounty (raw checked dict); translation is downstream."""
    # Gamma is 1 stop before Sol → recently_spotted
    # Delta is 2 stops before Sol → recently_spotted
    # Alpha is 4 stops before Sol → checked (not recently_spotted)
    bounty = _make_active_bounty(
        bounty_id=11,
        route=["Alpha", "Beta", "Gamma", "Delta", "Sol"],
        answer="Sol",
        # Alpha: 4 stops before → plain "checked"
        # Gamma: 2 stops before → "recently_spotted"
        # Delta: 1 stop before → "recently_spotted"
        # Sol: found → "found"
        checked={"Alpha": 7, "Beta": -1, "Gamma": 7, "Delta": 7, "Sol": 7},
        guild_id=1,
        division="silver",
        tech_level=5,
        reward=10000,
    )
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    mock_helper = AsyncMock(return_value=_make_request_payload(bounty))

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty)

    # A.48: translation now lives in `utils.bounty_announcement_payload._project_checked`
    # and is tested in `tests/test_bounty_announcement_payload.py`. Here we verify
    # the helper was invoked with the bounty carrying the raw checked dict.
    mock_helper.assert_awaited_once()
    called_bounty = mock_helper.call_args.args[1]
    assert called_bounty.checked == bounty.checked


@pytest.mark.asyncio
async def test_edit_announcement_non_fatal(service, mock_db):
    """If the HTTP edit fails, the exception is caught and does not propagate."""
    bounty = _make_active_bounty(bounty_id=10)
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_request_payload(bounty)),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        # Make the HTTP client raise an exception
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Must NOT raise — non-fatal
        await service._edit_bounty_announcement(mock_db, bounty)


@pytest.mark.asyncio
async def test_edit_announcement_sends_put_to_gateway(service, mock_db):
    """A.48: Verifies PUT goes to the unified bounty-announcement endpoint with structured payload."""
    import os

    bounty = _make_active_bounty(bounty_id=77, guild_id=5)
    discord_msg = _make_discord_message(message_id=12345)

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    captured_calls = {}

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=AsyncMock(return_value=_make_request_payload(bounty)),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
        patch.dict(os.environ, {"DISCORD_GATEWAY_HOST": "test-gateway", "GATEWAY_PORT": "8888"}),
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()

        async def capture_put(url, json=None, timeout=None):
            captured_calls["url"] = url
            captured_calls["json"] = json
            return mock_response

        mock_client.put = capture_put
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty)

    # A.48 unified endpoint: http://{host}:{port}/api/v1/announcements/bounty/channel/{channel_id}/message/{message_id}
    assert captured_calls["url"] == "http://test-gateway:8888/api/v1/announcements/bounty/channel/7777/message/12345"

    # Structured payload: text_content + loadout_response + metadata.
    payload = captured_calls["json"]
    assert "loadout_response" in payload
    assert "metadata" in payload
    assert "text_content" in payload


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
# Tests: _edit_bounty_announcement passes captured flag to builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_announcement_passes_captured_true_to_builder(service, mock_db):
    """When captured=True is passed, the builder receives captured=True in the data dict."""
    bounty = _make_active_bounty(bounty_id=10, guild_id=1)
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    mock_helper = AsyncMock(return_value=_make_request_payload(bounty, captured=True))

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty, captured=True)

    # A.48 wire shape: build_bounty_announcement_request must be called with captured=True kwarg.
    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("captured") is True


@pytest.mark.asyncio
async def test_edit_announcement_passes_captured_false_by_default(service, mock_db):
    """When captured is not passed, the builder receives captured=False."""
    bounty = _make_active_bounty(bounty_id=11, guild_id=1)
    discord_msg = _make_discord_message()

    mock_msg_repo = AsyncMock()
    mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=discord_msg)

    mock_helper = AsyncMock(return_value=_make_request_payload(bounty))

    with (
        patch(
            "persist.repositories.discord_message_repository.DiscordMessageRepository",
            return_value=mock_msg_repo,
        ),
        patch(
            "utils.bounty_announcement_payload.build_bounty_announcement_request",
            new=mock_helper,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await service._edit_bounty_announcement(mock_db, bounty)

    # A.48 wire shape: captured kwarg should default to False when not passed.
    mock_helper.assert_awaited_once()
    assert mock_helper.call_args.kwargs.get("captured") is False
