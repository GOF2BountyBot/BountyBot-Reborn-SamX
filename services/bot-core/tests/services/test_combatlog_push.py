"""Phase 2 — combat-log gateway-push call-site tests (bot-core side).

Verifies the cross-service push contract:
  - push_combatlog_invalidate_both pushes for BOTH human combatants.
  - PvC criminal side (NULL user_id) is SKIPPED (never pushed).
  - A push failure is swallowed (never propagates to the fight finalizer).
  - CombatLogService._schedule_combatlog_invalidate fires the both-combatant push
    on a running loop and degrades silently with no running loop.

Max 2 mocks per test.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# Guard: mock shared.bblogger before any src imports
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# push_combatlog_invalidate_both — both combatants, NULL skip, failure-tolerant
# ---------------------------------------------------------------------------


def test_push_both_combatants_pushes_twice():
    from utils import gateway_push

    seen: list[tuple[int, int]] = []

    async def _fake_single(guild_id, user_id):
        seen.append((guild_id, user_id))

    with patch.object(gateway_push, "push_combatlog_invalidate", _fake_single):
        asyncio.run(gateway_push.push_combatlog_invalidate_both(99, 1001, 1002))

    assert seen == [(99, 1001), (99, 1002)]


def test_push_pvc_null_criminal_side_skipped():
    from utils import gateway_push

    seen: list[tuple[int, int]] = []

    async def _fake_single(guild_id, user_id):
        seen.append((guild_id, user_id))

    with patch.object(gateway_push, "push_combatlog_invalidate", _fake_single):
        # PvC: combatant2 is the NPC criminal (NULL discord id) — must be skipped.
        asyncio.run(gateway_push.push_combatlog_invalidate_both(99, 1001, None))

    assert seen == [(99, 1001)]  # only the human combatant pushed


def test_push_failure_is_swallowed():
    from utils import gateway_push

    # A raising HTTP client must NOT propagate out of the push helper.
    failing_client = MagicMock()
    failing_client.__aenter__ = AsyncMock(return_value=failing_client)
    failing_client.__aexit__ = AsyncMock(return_value=False)
    failing_client.post = AsyncMock(side_effect=RuntimeError("gateway down"))

    with (
        patch("httpx.AsyncClient", return_value=failing_client),
        patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": "tok"}),
    ):
        # Should not raise.
        asyncio.run(gateway_push.push_combatlog_invalidate(99, 1001))


# ---------------------------------------------------------------------------
# CombatLogService._schedule_combatlog_invalidate — fire-after-commit hook
# ---------------------------------------------------------------------------


def test_schedule_fires_both_push_on_running_loop():
    from services.combat_log_service import CombatLogService

    captured: dict = {}

    async def _fake_both(guild_id, c1, c2):
        captured["args"] = (guild_id, c1, c2)

    async def _run():
        with patch("utils.gateway_push.push_combatlog_invalidate_both", _fake_both):
            CombatLogService._schedule_combatlog_invalidate(77, 5, 6)
            # let the scheduled task run
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    asyncio.run(_run())
    assert captured.get("args") == (77, 5, 6)


def test_schedule_no_running_loop_is_silent():
    from services.combat_log_service import CombatLogService

    # No running loop → must NOT raise (logs debug + skips; TTL self-heals).
    CombatLogService._schedule_combatlog_invalidate(77, 5, None)
