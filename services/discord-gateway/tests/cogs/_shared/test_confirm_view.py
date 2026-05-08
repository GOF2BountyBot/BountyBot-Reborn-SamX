"""Tests for cogs/_shared/confirm_view.py — covers all UI button/timeout paths.

ConfirmView is a discord.ui.View subclass with two buttons (Confirm/Cancel).
The `confirm` and `cancel` attributes are `discord.ui.Button` instances whose
`.callback(interaction)` coroutine is what actually runs the button logic.

Key behaviour:
  - `confirm.callback(interaction)` → result=True, stop(), interaction.response.defer()
  - `cancel.callback(interaction)` → result=False, stop(), interaction.response.defer()
  - `on_timeout()` from parent → no-op (view relies on parent wait() machinery to stop)
  - Initial state: result is None, is_finished() is False

Note on discord.ui.Button callbacks:
    After the @discord.ui.button decorator is applied, `view.confirm` is a
    `discord.ui.Button` instance. The actual async function body lives at
    `view.confirm.callback` which is an `_ItemCallback` — callable as
    `await view.confirm.callback(interaction)` (no extra positional args).
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Clear discord modules so we import real discord
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_interaction():
    """Create a mock discord.Interaction with async response.defer."""
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    return interaction


@pytest.fixture
def view():
    """Return a default ConfirmView with action='this action' and default timeout."""
    from cogs._shared.confirm_view import ConfirmView

    return ConfirmView()


@pytest.fixture
def custom_view():
    """Return a ConfirmView with custom action and timeout."""
    from cogs._shared.confirm_view import ConfirmView

    return ConfirmView(action="delete all bounties", timeout=30)


# ---------------------------------------------------------------------------
# TestConfirmViewInitialState
# ---------------------------------------------------------------------------


class TestConfirmViewInitialState:
    """Tests for the initial state of ConfirmView after instantiation."""

    def test_result_is_none_on_creation(self, view):
        """result must be None immediately after creation (no interaction yet)."""
        assert view.result is None

    def test_action_default_value(self, view):
        """Default action attribute should be 'this action'."""
        assert view.action == "this action"

    def test_action_custom_value(self, custom_view):
        """Custom action label is stored on the view."""
        assert custom_view.action == "delete all bounties"

    def test_view_is_not_stopped_on_creation(self, view):
        """The view must NOT be in a stopped state immediately after creation.

        is_finished() returns False when __stopped future has not been created
        (before wait() is called). This is the expected initial state.
        """
        import discord

        assert isinstance(view, discord.ui.View)
        # is_finished() == False because __stopped is None before wait() starts
        assert not view.is_finished()

    def test_timeout_default(self, view):
        """Default timeout should be 60 seconds (as per __init__ default)."""
        assert view.timeout == 60

    def test_timeout_custom(self, custom_view):
        """Custom timeout is forwarded to the parent View."""
        assert custom_view.timeout == 30

    def test_confirm_button_exists(self, view):
        """ConfirmView has a 'confirm' attribute (the discord.ui.Button)."""
        import discord

        assert hasattr(view, "confirm")
        assert isinstance(view.confirm, discord.ui.Button)

    def test_cancel_button_exists(self, view):
        """ConfirmView has a 'cancel' attribute (the discord.ui.Button)."""
        import discord

        assert hasattr(view, "cancel")
        assert isinstance(view.cancel, discord.ui.Button)


# ---------------------------------------------------------------------------
# TestConfirmCallback
# ---------------------------------------------------------------------------


class TestConfirmCallback:
    """Tests for the confirm button callback.

    discord.ui.Button callbacks are invoked via button.callback(interaction).
    The button instance is the view attribute set by @discord.ui.button.
    """

    def test_confirm_sets_result_true(self, view):
        """Pressing Confirm must set result to True."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        assert view.result is True

    def test_confirm_stops_view(self, view):
        """Pressing Confirm must call stop() on the view.

        We verify this by monkeypatching stop() to track that it was called.
        """
        stop_called = []

        original_stop = view.stop

        def _patched_stop():
            stop_called.append(True)
            original_stop()

        view.stop = _patched_stop
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        assert stop_called, "view.stop() must be called when Confirm is pressed"

    def test_confirm_calls_defer(self, view):
        """Pressing Confirm must call interaction.response.defer()."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        interaction.response.defer.assert_awaited_once()

    def test_confirm_defer_called_without_args(self, view):
        """interaction.response.defer must be called with no positional args."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        call_args = interaction.response.defer.call_args
        assert call_args == ((), {})

    def test_confirm_result_true_not_false(self, view):
        """result must be exactly True, not just truthy."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        assert view.result is True
        assert view.result is not False
        assert view.result is not None

    def test_confirm_twice_still_result_true(self, view):
        """Calling confirm twice leaves result True (idempotent)."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        asyncio.run(view.confirm.callback(interaction))
        assert view.result is True


# ---------------------------------------------------------------------------
# TestCancelCallback
# ---------------------------------------------------------------------------


class TestCancelCallback:
    """Tests for the cancel button callback."""

    def test_cancel_sets_result_false(self, view):
        """Pressing Cancel must set result to False."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        assert view.result is False

    def test_cancel_stops_view(self, view):
        """Pressing Cancel must call stop() on the view.

        We verify this by monkeypatching stop() to track that it was called.
        """
        stop_called = []

        original_stop = view.stop

        def _patched_stop():
            stop_called.append(True)
            original_stop()

        view.stop = _patched_stop
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        assert stop_called, "view.stop() must be called when Cancel is pressed"

    def test_cancel_calls_defer(self, view):
        """Pressing Cancel must call interaction.response.defer()."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        interaction.response.defer.assert_awaited_once()

    def test_cancel_defer_called_without_args(self, view):
        """interaction.response.defer must be called with no arguments."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        call_args = interaction.response.defer.call_args
        assert call_args == ((), {})

    def test_cancel_result_false_not_none(self, view):
        """result must be exactly False (not None) after cancel."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        assert view.result is False
        assert view.result is not None

    def test_cancel_twice_still_result_false(self, view):
        """Calling cancel twice leaves result False (idempotent)."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        asyncio.run(view.cancel.callback(interaction))
        assert view.result is False


# ---------------------------------------------------------------------------
# TestOnTimeout
# ---------------------------------------------------------------------------


class TestOnTimeout:
    """Tests for the on_timeout hook.

    ConfirmView does NOT override on_timeout; it inherits the discord.py
    parent's no-op implementation. The actual timeout signalling happens in
    the internal wait() machinery (which calls stop()). The contract the
    class docstring documents is:
        "if view.result is None: # timed out"
    meaning that after timeout, result remains None because no button was pressed.

    We test on_timeout() directly to confirm it does not change result and is
    safe to call.
    """

    def test_on_timeout_is_callable(self, view):
        """on_timeout must be a callable coroutine method."""
        assert callable(view.on_timeout)

    def test_result_is_none_after_on_timeout_called(self, view):
        """After on_timeout() fires, result remains None."""
        asyncio.run(view.on_timeout())
        assert view.result is None

    def test_on_timeout_does_not_crash(self, view):
        """on_timeout() must not raise any exceptions."""
        asyncio.run(view.on_timeout())  # Should not raise

    def test_timeout_result_distinguishable_from_confirm(self, view):
        """After timeout: result is None (not True), distinguishable from confirm."""
        asyncio.run(view.on_timeout())
        assert view.result is not True

    def test_timeout_result_distinguishable_from_cancel(self, view):
        """After timeout: result is None (not False), distinguishable from cancel."""
        asyncio.run(view.on_timeout())
        assert view.result is not False

    def test_view_stop_then_on_timeout_leaves_result_none(self, view):
        """If no button was pressed and timeout fires, result remains None."""
        # Simulate: view times out without any button press
        asyncio.run(view.on_timeout())
        assert view.result is None


# ---------------------------------------------------------------------------
# TestConfirmCancelOrdering
# ---------------------------------------------------------------------------


class TestConfirmCancelOrdering:
    """Tests verifying that confirm and cancel produce distinguishable outcomes."""

    def test_confirm_and_cancel_produce_distinct_results(self):
        """Two separate view instances: confirm→True, cancel→False."""
        from cogs._shared.confirm_view import ConfirmView

        confirm_view = ConfirmView()
        cancel_view = ConfirmView()
        interaction = _make_interaction()

        asyncio.run(confirm_view.confirm.callback(interaction))
        asyncio.run(cancel_view.cancel.callback(interaction))

        assert confirm_view.result is True
        assert cancel_view.result is False

    def test_confirm_result_is_not_cancel_result(self):
        """result=True from confirm must not equal result=False from cancel."""
        from cogs._shared.confirm_view import ConfirmView

        cv = ConfirmView()
        xv = ConfirmView()
        interaction = _make_interaction()

        asyncio.run(cv.confirm.callback(interaction))
        asyncio.run(xv.cancel.callback(interaction))

        assert cv.result != xv.result

    def test_timeout_distinguished_from_cancel(self):
        """Timeout result (None) is distinguishable from cancel result (False)."""
        from cogs._shared.confirm_view import ConfirmView

        timeout_view = ConfirmView()
        cancel_view = ConfirmView()
        interaction = _make_interaction()

        asyncio.run(timeout_view.on_timeout())
        asyncio.run(cancel_view.cancel.callback(interaction))

        assert timeout_view.result is None
        assert cancel_view.result is False
        assert timeout_view.result != cancel_view.result

    def test_confirm_does_not_affect_separate_view(self):
        """Confirming on view A does not change view B's result."""
        from cogs._shared.confirm_view import ConfirmView

        view_a = ConfirmView()
        view_b = ConfirmView()
        interaction = _make_interaction()

        asyncio.run(view_a.confirm.callback(interaction))

        assert view_a.result is True
        assert view_b.result is None  # untouched


# ---------------------------------------------------------------------------
# TestConfirmViewInstantiationVariants
# ---------------------------------------------------------------------------


class TestConfirmViewInstantiationVariants:
    """Tests for various constructor argument combinations."""

    def test_default_instantiation(self):
        """ConfirmView() with no args should work and set correct defaults."""
        from cogs._shared.confirm_view import ConfirmView

        v = ConfirmView()
        assert v.result is None
        assert v.action == "this action"
        assert v.timeout == 60

    def test_action_only(self):
        """ConfirmView(action='...') should set custom action with default timeout."""
        from cogs._shared.confirm_view import ConfirmView

        v = ConfirmView(action="remove the player")
        assert v.action == "remove the player"
        assert v.timeout == 60

    def test_timeout_only(self):
        """ConfirmView(timeout=120) should set custom timeout with default action."""
        from cogs._shared.confirm_view import ConfirmView

        v = ConfirmView(timeout=120)
        assert v.timeout == 120
        assert v.action == "this action"

    def test_both_action_and_timeout(self):
        """ConfirmView(action='...', timeout=30) should set both attributes."""
        from cogs._shared.confirm_view import ConfirmView

        v = ConfirmView(action="reset the guild", timeout=30)
        assert v.action == "reset the guild"
        assert v.timeout == 30

    def test_result_starts_none_all_instances(self):
        """Every new ConfirmView instance starts with result=None."""
        from cogs._shared.confirm_view import ConfirmView

        for _ in range(3):
            v = ConfirmView()
            assert v.result is None


# ---------------------------------------------------------------------------
# TestDiscordViewSubclassContract
# ---------------------------------------------------------------------------


class TestDiscordViewSubclassContract:
    """Tests verifying ConfirmView satisfies the discord.ui.View contract."""

    def test_is_discord_ui_view_subclass(self):
        """ConfirmView must be a subclass of discord.ui.View."""
        import discord
        from cogs._shared.confirm_view import ConfirmView

        assert issubclass(ConfirmView, discord.ui.View)

    def test_instance_is_discord_ui_view(self, view):
        """ConfirmView instance must be an instance of discord.ui.View."""
        import discord

        assert isinstance(view, discord.ui.View)

    def test_confirm_is_discord_ui_button(self, view):
        """confirm attribute must be a discord.ui.Button instance."""
        import discord

        assert isinstance(view.confirm, discord.ui.Button)

    def test_cancel_is_discord_ui_button(self, view):
        """cancel attribute must be a discord.ui.Button instance."""
        import discord

        assert isinstance(view.cancel, discord.ui.Button)

    def test_confirm_has_callback(self, view):
        """confirm button must have a callable callback attribute."""
        assert hasattr(view.confirm, "callback")
        assert callable(view.confirm.callback)

    def test_cancel_has_callback(self, view):
        """cancel button must have a callable callback attribute."""
        assert hasattr(view.cancel, "callback")
        assert callable(view.cancel.callback)

    def test_view_has_two_children(self, view):
        """ConfirmView must have exactly 2 children (confirm + cancel buttons)."""
        assert len(view.children) == 2

    def test_confirm_label(self, view):
        """Confirm button label must be 'Confirm'."""
        assert view.confirm.label == "Confirm"

    def test_cancel_label(self, view):
        """Cancel button label must be 'Cancel'."""
        assert view.cancel.label == "Cancel"

    def test_confirm_style_is_danger(self, view):
        """Confirm button uses danger (red) style."""
        import discord

        assert view.confirm.style == discord.ButtonStyle.danger

    def test_cancel_style_is_secondary(self, view):
        """Cancel button uses secondary (grey) style."""
        import discord

        assert view.cancel.style == discord.ButtonStyle.secondary


# ---------------------------------------------------------------------------
# TestViewStopBehavior
# ---------------------------------------------------------------------------


class TestViewStopBehavior:
    """Tests for view.stop() behavior via monkeypatching.

    discord.ui.View.is_finished() only returns True after wait() has been started
    (lazy future creation). In this test environment, asyncio.create_task is
    patched by the autouse _block_background_tasks fixture, which interferes with
    wait(). We verify stop() is called by tracking calls via monkeypatching.
    """

    def test_confirm_calls_stop(self, view):
        """confirm.callback() must call stop() on the view."""
        stop_calls = []
        original = view.stop

        def _track():
            stop_calls.append(True)
            original()

        view.stop = _track
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        assert stop_calls, "stop() must be called on Confirm"

    def test_cancel_calls_stop(self, view):
        """cancel.callback() must call stop() on the view."""
        stop_calls = []
        original = view.stop

        def _track():
            stop_calls.append(True)
            original()

        view.stop = _track
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        assert stop_calls, "stop() must be called on Cancel"

    def test_confirm_result_is_true_after_callback(self, view):
        """result must be True after confirm.callback()."""
        interaction = _make_interaction()
        asyncio.run(view.confirm.callback(interaction))
        assert view.result is True

    def test_cancel_result_is_false_after_callback(self, view):
        """result must be False after cancel.callback()."""
        interaction = _make_interaction()
        asyncio.run(view.cancel.callback(interaction))
        assert view.result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
