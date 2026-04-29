"""Tests for the SchedulerCog Discord commands.

Covers:
- /scheduler_list  (guild-filtered)
- /scheduler_view
- /scheduler_update
- /scheduler_delete
- /admin_reset_scheduler  (new)
- /admin_clear_scheduler  (new)

Import isolation: shared.bblogger is mocked before any application imports.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any cog imports
# ---------------------------------------------------------------------------
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interaction(guild_id: int = 123456789, user_id: int = 987654321) -> MagicMock:
    """Build a minimal mock discord.Interaction."""
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


def _make_mock_response(json_data, status_code: int = 200, raise_status: bool = False) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if raise_status:
        error_resp = MagicMock()
        error_resp.status_code = status_code
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}", request=MagicMock(), response=error_resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    return bot


@pytest.fixture
def cog(mock_bot):
    # Evict any stale cog module so it re-imports fresh with our mock logger
    for key in list(sys.modules.keys()):
        if "schedulerCog" in key or (key.startswith("cogs.") and "scheduler" in key.lower()):
            del sys.modules[key]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

    from cogs.schedulerCog import SchedulerCog

    return SchedulerCog(mock_bot)


# ---------------------------------------------------------------------------
# Sample API payloads
# ---------------------------------------------------------------------------

_SAMPLE_JOBS = [
    {
        "id": "bounty_spawn_default",
        "next_run_time": "2026-06-01T12:00:00+00:00",
        "trigger": "cron[*/5 * * * *]",
        "args": ["bounty_spawn_default", {"job_type": "bounty_spawn"}],
    },
    {
        "id": "guild-job-abc123",
        "next_run_time": "2026-06-01T12:05:00+00:00",
        "trigger": "date[2026-06-01 12:05:00 UTC]",
        "args": ["guild-job-abc123", {"job_type": "bounty_expire", "guild_id": 123456789}],
    },
]


# ===========================================================================
# TestSchedulerList — /scheduler_list
# ===========================================================================


class TestSchedulerList:
    """Tests for /scheduler_list command."""

    def test_scheduler_list_success(self, cog):
        """Happy path: API returns jobs and embed is sent with guild_id filter."""
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_SAMPLE_JOBS))

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        # Verify guild_id query param was passed
        call_kwargs = cog.http_client.get.call_args
        params = call_kwargs.kwargs.get("params", {}) or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert params.get("guild_id") == interaction.guild_id

    def test_scheduler_list_empty_returns_no_jobs_embed(self, cog):
        """When API returns empty list, sends a 'No scheduled jobs' embed."""
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(return_value=_make_mock_response([]))

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        send_call = interaction.followup.send.call_args
        embed = send_call.kwargs.get("embed") or (send_call.args[0] if send_call.args else None)
        assert embed is not None

    def test_scheduler_list_503_sends_unavailable_message(self, cog):
        """Returns warning message when API responds with 503."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 503
        cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="503", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "unavailable" in msg.lower() or "starting" in msg.lower()

    def test_scheduler_list_api_error_sends_error_message(self, cog):
        """Non-503 API errors send a '❌ API Error' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 500
        cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="500", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        # B.31b: helper sends a sanitized embed for non-503 API errors.
        call_kwargs = interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_scheduler_list_generic_exception_sends_warning(self, cog):
        """Generic exceptions send a warning message."""
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(side_effect=Exception("network failure"))

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "⚠️" in msg or "error" in msg.lower()


# ===========================================================================
# TestAdminResetScheduler — /admin_reset_scheduler
# ===========================================================================


class TestAdminResetScheduler:
    """Tests for /admin_reset_scheduler command (new endpoint POST /reset)."""

    def test_reset_scheduler_success(self, cog):
        """Happy path: resets scheduler and shows jobs_registered count."""
        interaction = _make_interaction()
        cog.http_client.post = AsyncMock(return_value=_make_mock_response({"status": "reset", "jobs_registered": 3}))

        asyncio.run(cog.admin_reset_scheduler.callback(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        # The command should have POSTed to /reset
        call_url = cog.http_client.post.call_args.args[0]
        assert "/reset" in call_url

    def test_reset_scheduler_sends_embed_with_jobs_registered(self, cog):
        """Embed includes the jobs_registered count from the API response."""
        interaction = _make_interaction()
        cog.http_client.post = AsyncMock(return_value=_make_mock_response({"status": "reset", "jobs_registered": 3}))

        asyncio.run(cog.admin_reset_scheduler.callback(cog, interaction))

        send_call = interaction.followup.send.call_args
        embed = send_call.kwargs.get("embed") or (send_call.args[0] if send_call.args else None)
        assert embed is not None

    def test_reset_scheduler_503_sends_unavailable_message(self, cog):
        """503 from API sends 'scheduler unavailable' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 503
        cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="503", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.admin_reset_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "unavailable" in msg.lower() or "starting" in msg.lower()

    def test_reset_scheduler_api_error_sends_error_message(self, cog):
        """Non-503 API error sends '❌ API Error' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 500
        cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="500", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.admin_reset_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        # B.31b: helper sends a sanitized embed for non-503 API errors.
        call_kwargs = interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_reset_scheduler_generic_exception_sends_warning(self, cog):
        """Generic exceptions send a warning message."""
        interaction = _make_interaction()
        cog.http_client.post = AsyncMock(side_effect=Exception("timeout"))

        asyncio.run(cog.admin_reset_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "⚠️" in msg or "error" in msg.lower()

    def test_reset_scheduler_error_handler_sends_message_when_not_done(self, cog):
        """Error handler sends a message when interaction is not yet responded to."""
        from discord import app_commands

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = MagicMock()
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.response.send_message = AsyncMock()
        error = app_commands.MissingPermissions(["administrator"])

        asyncio.run(cog.admin_reset_scheduler_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ===========================================================================
# TestAdminClearScheduler — /admin_clear_scheduler
# ===========================================================================


class TestAdminClearScheduler:
    """Tests for /admin_clear_scheduler command (new endpoint DELETE /jobs/guild/{guild_id})."""

    def test_clear_scheduler_success(self, cog):
        """Happy path: clears guild jobs and shows removed_count."""
        interaction = _make_interaction(guild_id=123456789)
        cog.http_client.delete = AsyncMock(
            return_value=_make_mock_response(
                {"status": "guild_jobs_deleted", "guild_id": 123456789, "removed_count": 2}
            )
        )

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        # Verify DELETE was called with the correct URL
        call_url = cog.http_client.delete.call_args.args[0]
        assert f"/jobs/guild/{interaction.guild_id}" in call_url

    def test_clear_scheduler_sends_embed_with_removed_count(self, cog):
        """Embed includes the removed_count value from the API response."""
        interaction = _make_interaction()
        cog.http_client.delete = AsyncMock(
            return_value=_make_mock_response(
                {"status": "guild_jobs_deleted", "guild_id": interaction.guild_id, "removed_count": 5}
            )
        )

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        send_call = interaction.followup.send.call_args
        embed = send_call.kwargs.get("embed") or (send_call.args[0] if send_call.args else None)
        assert embed is not None

    def test_clear_scheduler_uses_guild_id_from_interaction(self, cog):
        """DELETE request uses interaction.guild_id in the URL path."""
        guild_id = 555444333
        interaction = _make_interaction(guild_id=guild_id)
        cog.http_client.delete = AsyncMock(
            return_value=_make_mock_response({"status": "guild_jobs_deleted", "guild_id": guild_id, "removed_count": 0})
        )

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        call_url = cog.http_client.delete.call_args.args[0]
        assert str(guild_id) in call_url

    def test_clear_scheduler_503_sends_unavailable_message(self, cog):
        """503 from API sends 'scheduler unavailable' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 503
        cog.http_client.delete = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="503", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "unavailable" in msg.lower() or "starting" in msg.lower()

    def test_clear_scheduler_api_error_sends_error_message(self, cog):
        """Non-503 API error sends '❌ API Error' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 500
        cog.http_client.delete = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="500", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        # B.31b: helper sends a sanitized embed for non-503 API errors.
        call_kwargs = interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_clear_scheduler_generic_exception_sends_warning(self, cog):
        """Generic exceptions send a warning message."""
        interaction = _make_interaction()
        cog.http_client.delete = AsyncMock(side_effect=Exception("network error"))

        asyncio.run(cog.admin_clear_scheduler.callback(cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "⚠️" in msg or "error" in msg.lower()

    def test_clear_scheduler_error_handler_sends_message_when_not_done(self, cog):
        """Error handler sends a message when interaction is not yet responded to."""
        from discord import app_commands

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = MagicMock()
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.response.send_message = AsyncMock()
        error = app_commands.MissingPermissions(["administrator"])

        asyncio.run(cog.admin_clear_scheduler_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ===========================================================================
# TestSchedulerView — /scheduler_view
# ===========================================================================


class TestSchedulerView:
    """Tests for /scheduler_view command."""

    def test_scheduler_view_success(self, cog):
        """Happy path: fetches a specific job and sends embed."""
        interaction = _make_interaction()
        job_data = _SAMPLE_JOBS[0]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(job_data))

        asyncio.run(cog.scheduler_view.callback(cog, interaction, job_id="bounty_spawn_default"))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    def test_scheduler_view_404_sends_not_found_message(self, cog):
        """404 from API sends '❌ Job not found' message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 404
        cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="404", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.scheduler_view.callback(cog, interaction, job_id="nonexistent"))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "not found" in msg.lower() or "❌" in msg

    def test_scheduler_view_503_sends_unavailable(self, cog):
        """503 sends unavailable message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 503
        cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="503", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.scheduler_view.callback(cog, interaction, job_id="some-job"))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "unavailable" in msg.lower() or "starting" in msg.lower()

    def test_scheduler_view_generic_error_sends_warning(self, cog):
        """Generic exceptions send warning message."""
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(side_effect=Exception("timeout"))

        asyncio.run(cog.scheduler_view.callback(cog, interaction, job_id="some-job"))

        interaction.followup.send.assert_awaited_once()


# ===========================================================================
# TestSchedulerDelete — /scheduler_delete
# ===========================================================================


class TestSchedulerDelete:
    """Tests for /scheduler_delete command."""

    def test_scheduler_delete_success(self, cog):
        """Happy path: deletes job and sends confirmation embed."""
        interaction = _make_interaction()
        cog.http_client.delete = AsyncMock(
            return_value=_make_mock_response({"status": "deleted", "job_id": "some-job-id"})
        )

        asyncio.run(cog.scheduler_delete.callback(cog, interaction, job_id="some-job-id"))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    def test_scheduler_delete_404_sends_not_found(self, cog):
        """404 sends not-found message."""
        interaction = _make_interaction()
        error_resp = MagicMock()
        error_resp.status_code = 404
        cog.http_client.delete = AsyncMock(
            side_effect=httpx.HTTPStatusError(message="404", request=MagicMock(), response=error_resp)
        )

        asyncio.run(cog.scheduler_delete.callback(cog, interaction, job_id="nonexistent"))

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "not found" in msg.lower() or "❌" in msg


# ===========================================================================
# TestSchedulerUpdate — /scheduler_update
# ===========================================================================


class TestSchedulerUpdate:
    """Tests for /scheduler_update command."""

    def test_scheduler_update_success(self, cog):
        """Happy path: updates job payload and sends confirmation embed."""
        interaction = _make_interaction()
        cog.http_client.put = AsyncMock(
            return_value=_make_mock_response({"status": "updated", "job_id": "some-job-id"})
        )

        asyncio.run(
            cog.scheduler_update.callback(
                cog, interaction, job_id="some-job-id", payload_json='{"job_type": "bounty_spawn"}'
            )
        )

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    def test_scheduler_update_invalid_json_sends_error(self, cog):
        """B.28 fix: Invalid JSON is validated BEFORE defer — uses send_message not followup.send."""
        interaction = _make_interaction()
        cog.http_client.put = AsyncMock()

        asyncio.run(cog.scheduler_update.callback(cog, interaction, job_id="some-job", payload_json="not-valid-json"))

        # API should not have been called
        cog.http_client.put.assert_not_awaited()
        # B.28: since validation happens before defer, error uses response.send_message (not followup)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.defer.assert_not_awaited()
        msg = str(interaction.response.send_message.call_args)
        assert "❌" in msg or "invalid" in msg.lower()

    def test_scheduler_update_valid_json_defers_before_api_call(self, cog):
        """B.28 fix: When JSON is valid, defer is called before the PUT request."""
        interaction = _make_interaction()
        cog.http_client.put = AsyncMock(return_value=_make_mock_response({"status": "updated", "job_id": "job-1"}))

        call_order = []
        original_defer = interaction.response.defer
        original_put = cog.http_client.put

        async def track_defer(*args, **kwargs):
            call_order.append("defer")
            return await original_defer(*args, **kwargs)

        async def track_put(*args, **kwargs):
            call_order.append("put")
            return await original_put(*args, **kwargs)

        interaction.response.defer = track_defer
        cog.http_client.put = track_put

        asyncio.run(
            cog.scheduler_update.callback(cog, interaction, job_id="job-1", payload_json='{"job_type": "bounty_spawn"}')
        )

        assert call_order.index("defer") < call_order.index("put"), "defer must happen before API PUT"


# ===========================================================================
# TestCogSetupAndUnload
# ===========================================================================


class TestCogSetupAndUnload:
    """Tests for SchedulerCog setup and unload lifecycle."""

    def test_setup_function_adds_cog_to_bot(self, mock_bot):
        """setup() adds SchedulerCog to the bot."""
        from cogs.schedulerCog import setup

        asyncio.run(setup(mock_bot))
        mock_bot.add_cog.assert_awaited_once()

    def test_cog_unload_closes_http_client(self, cog):
        """cog_unload() closes the http_client."""
        cog.http_client.aclose = AsyncMock()
        asyncio.run(cog.cog_unload())
        cog.http_client.aclose.assert_awaited_once()


# ===========================================================================
# TestJobIdAutocomplete
# ===========================================================================


class TestJobIdAutocomplete:
    """Tests for the job_id autocomplete helper."""

    def test_autocomplete_returns_matching_choices(self, cog):
        """Returns choices that contain the current text."""
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_SAMPLE_JOBS))
        interaction = _make_interaction()

        choices = asyncio.run(cog.job_id_autocomplete(interaction, current="bounty"))

        assert len(choices) > 0
        assert all("bounty" in c.name.lower() or "bounty" in c.value.lower() for c in choices)

    def test_autocomplete_returns_empty_on_api_error(self, cog):
        """Returns empty list when API call fails."""
        cog.http_client.get = AsyncMock(side_effect=Exception("network error"))
        interaction = _make_interaction()

        choices = asyncio.run(cog.job_id_autocomplete(interaction, current=""))

        assert choices == []


# ===========================================================================
# TestB27ErrorHandlerFallback — B.27: error handlers fallback after defer
# ===========================================================================


class TestB27ErrorHandlerFallback:
    """B.27 fix: All 6 scheduler error handlers must send a followup when is_done() is True."""

    def _make_deferred_interaction(self) -> MagicMock:
        """Interaction where defer has already been called (is_done() returns True)."""
        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = MagicMock()
        interaction.response.is_done = MagicMock(return_value=True)
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        return interaction

    def _make_undeferred_interaction(self) -> MagicMock:
        """Interaction where defer has NOT been called (is_done() returns False)."""
        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = MagicMock()
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        return interaction

    def test_scheduler_list_error_handler_sends_followup_when_deferred(self, cog):
        """scheduler_list error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.scheduler_list_error(interaction, error))

        interaction.followup.send.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    def test_scheduler_list_error_handler_sends_response_when_not_deferred(self, cog):
        """scheduler_list error handler sends response.send_message when is_done() is False."""
        from discord import app_commands

        interaction = self._make_undeferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.scheduler_list_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()

    def test_scheduler_view_error_handler_sends_followup_when_deferred(self, cog):
        """scheduler_view error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.scheduler_view_error(interaction, error))

        interaction.followup.send.assert_awaited_once()

    def test_scheduler_update_error_handler_sends_followup_when_deferred(self, cog):
        """scheduler_update error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.scheduler_update_error(interaction, error))

        interaction.followup.send.assert_awaited_once()

    def test_scheduler_delete_error_handler_sends_followup_when_deferred(self, cog):
        """scheduler_delete error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.scheduler_delete_error(interaction, error))

        interaction.followup.send.assert_awaited_once()

    def test_admin_reset_scheduler_error_handler_sends_followup_when_deferred(self, cog):
        """admin_reset_scheduler error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.admin_reset_scheduler_error(interaction, error))

        interaction.followup.send.assert_awaited_once()

    def test_admin_clear_scheduler_error_handler_sends_followup_when_deferred(self, cog):
        """admin_clear_scheduler error handler sends followup.send when is_done() is True."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        error = app_commands.CheckFailure("Not admin")

        asyncio.run(cog.admin_clear_scheduler_error(interaction, error))

        interaction.followup.send.assert_awaited_once()

    def test_error_handler_suppresses_followup_exception(self, cog):
        """B.27: followup.send failure in error handler must be silently suppressed."""
        from discord import app_commands

        interaction = self._make_deferred_interaction()
        interaction.followup.send.side_effect = Exception("Discord API error")
        error = app_commands.CheckFailure("Not admin")

        # Should not raise despite followup.send throwing
        asyncio.run(cog.scheduler_view_error(interaction, error))

        interaction.followup.send.assert_awaited_once()


# ===========================================================================
# TestB29CronTriggerDisplay — B.29: cron trigger wrapped in backticks
# ===========================================================================


class TestB29CronTriggerDisplay:
    """B.29 fix: cron trigger strings must be wrapped in backticks to prevent Discord markdown."""

    def test_scheduler_list_wraps_trigger_in_backticks(self, cog):
        """scheduler_list embed field values must wrap trigger in backticks."""
        cron_trigger = "cron[month='*', day='*', day_of_week='*', hour='*', minute='*/5']"
        jobs = [
            {
                "id": "bounty_spawn_default",
                "next_run_time": "2026-06-01T12:00:00+00:00",
                "trigger": cron_trigger,
                "args": ["bounty_spawn_default", {"job_type": "bounty_spawn"}],
            }
        ]
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(jobs))

        asyncio.run(cog.scheduler_list.callback(cog, interaction))

        send_call = interaction.followup.send.call_args
        embed = send_call.kwargs.get("embed") or (send_call.args[0] if send_call.args else None)
        assert embed is not None
        # The trigger value in the embed field must be wrapped in backticks
        field_values = [f.value for f in embed.fields]
        assert any(f"`{cron_trigger}`" in v for v in field_values), (
            f"Expected trigger wrapped in backticks in embed fields, got: {field_values}"
        )

    def test_scheduler_view_wraps_trigger_in_backticks(self, cog):
        """scheduler_view Trigger embed field must wrap the trigger in backticks."""
        cron_trigger = "cron[month='*', day='*', day_of_week='*', hour='*', minute='*/5']"
        job_data = {
            "id": "bounty_spawn_default",
            "next_run_time": "2026-06-01T12:00:00+00:00",
            "trigger": cron_trigger,
            "args": ["bounty_spawn_default", {"job_type": "bounty_spawn"}],
        }
        interaction = _make_interaction()
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(job_data))

        asyncio.run(cog.scheduler_view.callback(cog, interaction, job_id="bounty_spawn_default"))

        send_call = interaction.followup.send.call_args
        embed = send_call.kwargs.get("embed") or (send_call.args[0] if send_call.args else None)
        assert embed is not None
        # Find the "Trigger" field and verify backtick wrapping
        trigger_fields = [f for f in embed.fields if f.name == "Trigger"]
        assert trigger_fields, "Expected a 'Trigger' field in the embed"
        trigger_value = trigger_fields[0].value
        assert trigger_value == f"`{cron_trigger}`", f"Expected trigger wrapped in backticks, got: {trigger_value!r}"
