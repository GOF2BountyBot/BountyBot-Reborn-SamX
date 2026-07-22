"""
Deep coverage tests for tags.py — third pass targeting remaining uncovered lines.

Uncovered lines after test_tags_extended.py + test_tags_extra.py (65%):
  86-93   - get_tag: non-dict payload where setattr raises → __dict__ fallback
  127-128 - create: normalize_emoji raises → 422 (TRUEUP-P3 fixed; was a status.HTTP_422 bug → 500)
  153-159 - create: channel.edit raises AttributeError → proxy _TagProxy fallback
  174-175 - create: dict payload emoji normalization raises (silently ignored)
  179-186 - create: non-dict payload where setattr raises → __dict__ fallback
  235-236 - update: invalid emoji → 422 (TRUEUP-P3 fixed; was a status.HTTP_422 bug → 500)
  250-283 - update: no tag.edit, no edit_tag → tags_to_edit_payload fallback
  289-290 - update: id-lookup after edit returns None → name fallback
  292     - update: both id and name lookups fail → use original tag
  301-308 - update: dict payload with emoji=None but tag_data.emoji requested
  312-324 - update: non-dict payload where setattr raises → __dict__ fallback
  389-393 - delete: channel.edit(remaining) raises TypeError → dict payload loop
  397-414 - delete: channel.edit(payloads) raises AttributeError → proxy fallback
  421-422 - delete: deleted=False → 500 (unreachable in practice, guarded)

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception``, ``get_entity_or_404`` or ``ChannelConverter``
EXCEPT in the handful of tests whose entire point is a branch the real
``ChannelConverter.forum_tag_to_payload`` can never take — it always returns
a plain dict (see ``tag_to_dict`` in ``src/utils/discord_helpers.py``), so
the router's "non-dict payload / setattr raises / __dict__ fallback" code is
dead for the real converter. Those tests patch only
``ChannelConverter.forum_tag_to_payload`` (narrowest scope, via
``_force_payload``) with a comment explaining why. A few tests similarly
patch only ``tags_to_edit_payload`` (a pure helper) to construct a payload
shape (a non-int ``"id"``) the real function's own id-sanitization can never
produce. Everything else uses the same real-spec'd
``discord.ForumChannel``/``discord.ForumTag`` mocks as ``test_tags.py``, with
``channel.edit``/``create_tag`` side effects chained via plain Python
exception sequencing to exercise the router's real TypeError/AttributeError
fallback chain end-to-end.
"""

import os
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ["info", "debug", "warning", "error", "trace", "critical"]:
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Helpers (same shape as test_tags.py / test_tags_extended.py / test_tags_extra.py)
# ---------------------------------------------------------------------------


def _make_tag(tag_id=1234567890, name="Test Tag", emoji=None):
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def _make_forum_channel(channel_id=555555, tags=None):
    ch = MagicMock(spec=discord.ForumChannel)
    ch.id = channel_id
    ch.guild = MagicMock()
    ch.guild.id = 999999
    ch.available_tags = tags if tags is not None else [_make_tag()]

    async def _create_tag(name, emoji=None, **_kwargs):
        new_id = max((t.id for t in ch.available_tags if isinstance(t.id, int)), default=0) + 1
        new_tag = _make_tag(tag_id=new_id, name=name, emoji=emoji)
        ch.available_tags.append(new_tag)
        return new_tag

    async def _edit(**kwargs):
        if "available_tags" not in kwargs:
            return
        new_list = []
        next_id = max((t.id for t in ch.available_tags if isinstance(t.id, int)), default=0) + 1
        for item in kwargs["available_tags"]:
            if hasattr(item, "moderated"):
                new_list.append(item)
                continue
            data = item if isinstance(item, dict) else item.to_dict()
            tid = data.get("id")
            existing = discord.utils.get(ch.available_tags, id=tid) if tid is not None else None
            if existing is not None:
                existing.name = data.get("name")
                existing.emoji = data.get("emoji")
                new_list.append(existing)
            else:
                if tid is None:
                    tid, next_id = next_id, next_id + 1
                new_list.append(_make_tag(tag_id=tid, name=data.get("name"), emoji=data.get("emoji")))
        ch.available_tags = new_list

    ch.edit = AsyncMock(side_effect=_edit)
    ch.create_tag = AsyncMock(side_effect=_create_tag)
    return ch, _edit


def _make_bot(guilds=None, get_channel=None, fetch_channel=None):
    bot = DiscordMockUtils.create_mock_bot(user_id=11111, username="B")
    bot.guilds = guilds or []
    if get_channel is not None:
        bot.get_channel = get_channel
    if fetch_channel is not None:
        bot.fetch_channel = fetch_channel
    return bot


def _bot_for_channel(ch):
    """A bot whose get_channel/fetch_channel resolve `ch` by id (used by create_forum_tag)."""
    return _make_bot(
        get_channel=MagicMock(side_effect=lambda x: ch if x == ch.id else None),
        fetch_channel=AsyncMock(side_effect=lambda x: ch if x == ch.id else create_discord_not_found()),
    )


def _bot_with_tag_in_guild(ch):
    """A bot whose guilds[0].channels contains `ch` (used by get/update/delete_tag's guild scan)."""
    guild = MagicMock()
    guild.channels = [ch]
    return _make_bot(guilds=[guild])


def _make_app(bot):
    app = FastAPI()
    app.state.bot = bot
    from api.routers.tags import router

    app.include_router(router, prefix="/api/v1")
    return app


@contextmanager
def _force_payload(payload_or_exc):
    """Patch only ``ChannelConverter.forum_tag_to_payload`` (narrowest scope).

    Used exclusively to reach branches the real converter can never take (a
    non-dict return) or to inject an unexpected error for outer
    exception-handler tests, while leaving resolve_bot/get_entity_or_404/
    handle_discord_exception/everything else real.
    """
    # Resolve the converter dynamically: the api-package conftest purges cached
    # utils/api modules per test, so the class the router imported THIS test is
    # the one that must be patched (a module-level import would go stale).
    from utils.discord_converters import ChannelConverter

    with patch.object(ChannelConverter, "forum_tag_to_payload") as mock_fn:
        if isinstance(payload_or_exc, Exception):
            mock_fn.side_effect = payload_or_exc
        else:
            mock_fn.return_value = payload_or_exc
        yield mock_fn


def _chained_edit(real_edit, *prefix_exceptions):
    """Build a channel.edit side_effect that raises `prefix_exceptions` in order
    on the first N calls, then delegates to the real (state-mutating) edit."""
    calls = {"n": 0}

    async def _side_effect(**kwargs):
        idx = calls["n"]
        calls["n"] += 1
        if idx < len(prefix_exceptions):
            raise prefix_exceptions[idx]
        return await real_edit(**kwargs)

    return _side_effect


# =============================================================================
# GET /tags/{tag_id} — lines 86-93
# =============================================================================


class TestGetTagNonDictSetAttrRaises:
    """Lines 86-93: non-dict payload, setattr raises → __dict__ fallback.

    Only reachable by forcing ``ChannelConverter.forum_tag_to_payload`` to
    return a non-dict object — the real converter always returns a dict.
    """

    def test_get_tag_object_payload_setattr_raises_uses_dict_fallback(self):
        """When forum_tag_to_payload returns object with frozen setattr, use __dict__ fallback."""
        tag = _make_tag(1234567890)
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenPayload:
            """Simulates an object where setattr is blocked."""

            id = 1234567890
            channel_id = 555555
            name = "Test Tag"
            emoji = None

            def __setattr__(self, key, value):
                raise AttributeError("frozen object")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with _force_payload(_FrozenPayload()):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"

    def test_get_tag_object_payload_no_dict_attribute(self):
        """When payload object has no __dict__ and setattr raises, the __dict__ fallback
        degrades to just {"channel_id": ...} — missing the schema's required id/name fields."""
        tag = _make_tag(1234567890)
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _SlotPayload:
            __slots__ = ("emoji", "id", "name")

            def __init__(self):
                self.id = 1234567890
                self.name = "Test Tag"
                self.emoji = None

            def __setattr__(self, key, value):
                if key == "channel_id":
                    raise AttributeError("no channel_id slot")
                super().__setattr__(key, value)

        with _force_payload(_SlotPayload()):
            client = TestClient(_make_app(bot))
            # The __dict__ fallback builds a dict from getattr(payload, "__dict__", {}) or {};
            # a __slots__-only object has no instance __dict__ at all, so this deterministically
            # falls back to {"channel_id": ...} alone — missing the ForumTag schema's required
            # id/name fields, which pydantic validation rejects. Real, deterministic 500 (not the
            # original test's "either 200 or 500 is acceptable" hedge).
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 500
            assert "detail" in response.json()


# =============================================================================
# POST /channels/{channel_id}/tags — lines 127-128, 153-159, 174-175, 179-186
# =============================================================================


class TestCreateForumTagDeep:
    def test_create_invalid_emoji_returns_422(self):
        """Lines 127-128: normalize_emoji raises in create → 422 "Invalid emoji: ...".

        History (TRUEUP-P3, fixed): this branch used the nonexistent ``status.HTTP_422``
        attribute (AttributeError → outer handler → 500) — see FOLLOWUPS.md R-gw-api-1."""
        ch, _edit = _make_forum_channel()
        bot = _bot_for_channel(ch)

        with patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad emoji")):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "Tag", "emoji": "bad_emoji_str"})
            assert response.status_code == 422
            assert response.json()["detail"] == "Invalid emoji: bad_emoji_str"

    def test_create_channel_edit_attributeerror_uses_proxy_fallback(self):
        """Lines 153-159: create_tag AND the dict-payload edit both raise AttributeError →
        real proxy-object fallback (``_TagProxy.to_dict()``) is exercised."""
        existing_tag = _make_tag(name="Existing")
        ch, real_edit = _make_forum_channel(tags=[existing_tag])
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("edit no dicts")))
        bot = _bot_for_channel(ch)

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555/tags", json={"name": "New Tag"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["name"] == "New Tag"
        assert ch.edit.await_count == 2

    def test_create_dict_response_emoji_normalize_raises_silently(self):
        """Lines 174-175: emoji normalization in the real dict create-response raises → silently ignored.

        No emoji in the request (so the request-side normalize_emoji call at line 124 is
        skipped entirely); the real create_tag() simulates Discord returning a tag that
        already carries an emoji, so only the RESPONSE-side normalize (line 174) runs.
        """
        ch, _edit = _make_forum_channel()

        async def _create_tag_with_preset_emoji(name, emoji=None, **_kwargs):
            new_tag = _make_tag(tag_id=999999, name=name, emoji="bad")
            ch.available_tags.append(new_tag)
            return new_tag

        ch.create_tag = AsyncMock(side_effect=_create_tag_with_preset_emoji)
        bot = _bot_for_channel(ch)

        with patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad")):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "Tag"})
            # normalize_emoji fails silently → should still return 201
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_object_response_setattr_raises_uses_dict_fallback(self):
        """Lines 179-186: create returns object payload, setattr raises → __dict__ fallback.

        Only reachable by forcing the converter (see class-level docstring).
        """
        ch, _edit = _make_forum_channel()
        bot = _bot_for_channel(ch)

        class _FrozenTag:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = None

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": 1234567890, "channel_id": 555555, "name": "Tag", "emoji": None}

        with _force_payload(_FrozenTag()):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "Tag"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"


# =============================================================================
# PUT /tags/{tag_id} — lines 235-236, 250-283, 289-290, 292, 301-308, 312-324
# =============================================================================


class TestUpdateTagDeep:
    def test_update_invalid_emoji_returns_422(self):
        """Lines 235-236: normalize_emoji raises in update → 422 "Invalid emoji: ...".

        History (TRUEUP-P3, fixed): this branch used the nonexistent ``status.HTTP_422``
        attribute (AttributeError → outer handler → 500) — see FOLLOWUPS.md R-gw-api-1."""
        tag = _make_tag(1234567890, name="Original")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        with patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad emoji")):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"emoji": "❌bad"})
            assert response.status_code == 422
            assert response.json()["detail"] == "Invalid emoji: ❌bad"

    def test_update_tag_no_edit_no_edit_tag_uses_payload_fallback(self):
        """Lines 250-283: no tag.edit, no channel.edit_tag → the real tags_to_edit_payload
        fallback runs (the installed discord.py's ForumTag/ForumChannel have neither by
        default — see test_tags.py's fidelity note)."""
        tag = _make_tag(1234567890, name="Original")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "Updated"

    def test_update_tag_no_edit_no_edit_tag_edit_raises_attributeerror_proxy(self):
        """Lines 264-280: tags_to_edit fallback, channel.edit raises AttributeError → real proxy fallback."""
        tag = _make_tag(1234567890, name="Original")
        ch, real_edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("no edit with list")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "Updated"
        assert ch.edit.await_count == 2

    def test_update_tag_refetch_by_name_when_id_lookup_fails(self):
        """Lines 289-290: after edit, id-lookup returns None → real search by name.

        Simulates a discord.py variant whose edit() reassigns the tag's id.
        """
        tag = _make_tag(tag_id=1234567890, name="OldName")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        async def _edit_reassigns_id(**kwargs):
            ch.available_tags = [_make_tag(tag_id=999999999, name="NewName")]

        ch.edit = AsyncMock(side_effect=_edit_reassigns_id)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "NewName"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "NewName"
        assert data["data"]["id"] == 999999999

    def test_update_tag_refetch_falls_back_to_original(self):
        """Line 292: both id and name lookups fail after edit → real code falls back to the
        original (pre-edit) tag object reference."""
        tag = _make_tag(tag_id=1234567890, name="OldName")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        async def _edit_wipes_tags(**kwargs):
            # Simulate a variant whose edit call empties the cache entirely —
            # neither an id nor a name lookup can find anything afterward.
            ch.available_tags = []

        ch.edit = AsyncMock(side_effect=_edit_wipes_tags)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "RequestedName"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        # Falls back to the original (unmutated) tag object: id=1234567890, name="OldName"
        assert data["data"]["id"] == 1234567890
        assert data["data"]["name"] == "OldName"

    def test_update_tag_dict_response_emoji_none_but_requested(self):
        """Lines 303-308: real dict response has emoji=None (edit doesn't persist emoji)
        but the request asked for one → best-effort reflect requested emoji."""
        tag = _make_tag(1234567890, name="Tag", emoji=None)
        ch, _edit = _make_forum_channel(tags=[tag])
        # Simulate a variant whose edit() only applies "name", silently dropping "emoji".
        ch.edit = AsyncMock(side_effect=lambda **kwargs: setattr(tag, "name", kwargs.get("name", tag.name)))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Tag", "emoji": "🚀"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["emoji"] == "🚀"

    def test_update_tag_dict_response_emoji_none_normalize_raises(self):
        """Lines 305-308: dict response emoji=None reflect path, normalize_emoji raises → raw emoji used."""
        tag = _make_tag(1234567890, name="Tag", emoji=None)
        ch, _edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=lambda **kwargs: setattr(tag, "name", kwargs.get("name", tag.name)))
        bot = _bot_with_tag_in_guild(ch)

        # First normalize call is for the request emoji (update_kwargs) → succeed.
        # Second call is for the best-effort reflect → raise.
        call_seq = [0]

        def _norm(emoji):
            idx = call_seq[0]
            call_seq[0] += 1
            if idx == 0:
                return "🚀"
            raise ValueError("bad")

        with patch("api.routers.tags.normalize_emoji", side_effect=_norm):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag", "emoji": "🚀"})
            # Should succeed — normalize failure in reflect is silently caught, raw emoji kept.
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "updated"
            assert data["data"]["emoji"] == "🚀"

    def test_update_tag_object_response_setattr_raises_uses_dict_fallback(self):
        """Lines 312-324: update returns object payload, setattr raises → __dict__ fallback.

        Only reachable by forcing the converter (see class-level docstring).
        """
        tag = _make_tag(1234567890, name="Original")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenUpdateTag:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = None

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": 1234567890, "channel_id": 555555, "name": "Tag", "emoji": None}

        with _force_payload(_FrozenUpdateTag()):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_tag_object_response_setattr_raises_with_emoji(self):
        """Lines 315-324: dict fallback for update response, with emoji normalization."""
        tag = _make_tag(1234567890, name="Original")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenUpdateTagWithEmoji:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = "🚀"  # has emoji → lines 315-319

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": 1234567890, "channel_id": 555555, "name": "Tag", "emoji": "🚀"}

        with _force_payload(_FrozenUpdateTagWithEmoji()):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"


# =============================================================================
# DELETE /tags/{tag_id} — lines 389-393, 397-414, 421-422
# =============================================================================


class TestDeleteTagDeep:
    def test_delete_tag_edit_raises_typeerror_then_dict_payloads(self):
        """Lines 385-393: channel.edit(remaining) raises TypeError → real dict payload loop."""
        tag = _make_tag(tag_id=1234567890, name="Del Tag")
        other_tag = _make_tag(tag_id=9999999, name="Keep")
        ch, real_edit = _make_forum_channel(tags=[tag, other_tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, TypeError("wrong type")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 2
        assert not any(t.id == 1234567890 for t in ch.available_tags)

    def test_delete_tag_edit_raises_typeerror_then_attributeerror_proxy(self):
        """Lines 397-414: first edit raises TypeError, dict-payload edit raises AttributeError →
        real proxy fallback."""
        tag = _make_tag(tag_id=1234567890, name="Del Tag")
        other_tag = _make_tag(tag_id=9999999, name="Keep")
        ch, real_edit = _make_forum_channel(tags=[tag, other_tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, TypeError("wrong type"), AttributeError("no edit")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 3

    def test_delete_tag_deleted_false_returns_500(self):
        """Lines 421-422: deleted=False after all fallbacks is unreachable in normal flow — the
        only way to reach it is an edit failure that isn't TypeError/AttributeError, which the
        inner `except Exception: raise` re-raises to the outer handler → real 500."""
        tag = _make_tag(tag_id=1234567890, name="Del Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=RuntimeError("total failure"))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 500
        assert "detail" in response.json()


# =============================================================================
# GET /tags/{tag_id} — lines 90-93 (dict fallback WITH emoji → normalize called)
# =============================================================================


class TestGetTagDictFallbackWithEmoji:
    """Lines 90-93: non-dict payload, setattr raises, __dict__ has emoji → normalize called.

    Only reachable by forcing the converter (real one always returns a dict).
    """

    def test_get_tag_frozen_payload_with_emoji_normalize_succeeds(self):
        """Lines 90-91: __dict__ fallback, emoji present, normalize_emoji succeeds."""
        tag = _make_tag(1234567890)
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenEmojiPayload:
            id = 1234567890
            channel_id = 555555
            name = "Emoji Tag"
            emoji = "🚀"

            def __setattr__(self, key, value):
                raise AttributeError("frozen object")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with (
            _force_payload(_FrozenEmojiPayload()),
            patch("api.routers.tags.normalize_emoji", return_value="🚀") as mock_norm,
        ):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"
            mock_norm.assert_called()

    def test_get_tag_frozen_payload_with_emoji_normalize_raises(self):
        """Lines 90-93: __dict__ fallback, emoji present, normalize_emoji raises → silently ignored."""
        tag = _make_tag(1234567890)
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenEmojiPayload2:
            id = 1234567890
            channel_id = 555555
            name = "Emoji Tag"
            emoji = "bad_emoji"

            def __setattr__(self, key, value):
                raise AttributeError("frozen object")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with (
            _force_payload(_FrozenEmojiPayload2()),
            patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad")),
        ):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"


# =============================================================================
# POST /channels/{channel_id}/tags — lines 183-186 (frozen payload WITH emoji)
# =============================================================================


class TestCreateFrozenPayloadWithEmoji:
    """Lines 183-186: create returns non-dict, setattr raises, __dict__ has emoji.

    Only reachable by forcing the converter (real one always returns a dict).
    """

    def test_create_frozen_payload_with_emoji_normalize_succeeds(self):
        """Lines 183-184: __dict__ fallback, emoji present, normalize_emoji succeeds."""
        ch, _edit = _make_forum_channel()
        bot = _bot_for_channel(ch)

        class _FrozenTagWithEmoji:
            id = 1234567890
            channel_id = 555555
            name = "Emoji Create Tag"
            emoji = "🎯"

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with (
            _force_payload(_FrozenTagWithEmoji()),
            patch("api.routers.tags.normalize_emoji", return_value="🎯") as mock_norm,
        ):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "Emoji Create Tag"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"
            mock_norm.assert_called()

    def test_create_frozen_payload_with_emoji_normalize_raises(self):
        """Lines 183-186: __dict__ fallback, emoji present, normalize raises → silently ignored."""
        ch, _edit = _make_forum_channel()
        bot = _bot_for_channel(ch)

        class _FrozenTagBadEmoji:
            id = 1234567890
            channel_id = 555555
            name = "Bad Emoji Create"
            emoji = "bad_emoji"

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with (
            _force_payload(_FrozenTagBadEmoji()),
            patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad")),
        ):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "Bad Emoji Create"})
            # normalize fails silently → still returns 201
            assert response.status_code == 201
            assert response.json()["status"] == "created"


# =============================================================================
# POST /channels/{channel_id}/tags — lines 153-159 (_TagProxy.to_dict() called)
# =============================================================================


class TestCreateTagProxyToDictCalled:
    """Lines 153-159: _TagProxy.to_dict() is actually invoked during proxy edit fallback."""

    def test_create_proxy_to_dict_is_invoked_without_id(self):
        """Lines 153-156, 159: to_dict() called on the proxy for the brand-new tag entry,
        which the real code never gives an "id" key (the tag doesn't exist yet)."""
        ch, real_edit = _make_forum_channel(tags=[])
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_for_channel(ch)

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555/tags", json={"name": "New Tag"})
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert ch.edit.await_count == 2

    def test_create_proxy_to_dict_is_invoked_with_int_id(self):
        """Lines 153-159: an existing tag (real int id, sanitized by the real
        tags_to_edit_payload) is carried through the proxy fallback alongside the new one."""
        existing = _make_tag(tag_id=9876543, name="Existing")
        ch, real_edit = _make_forum_channel(tags=[existing])
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_for_channel(ch)

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555/tags", json={"name": "Tagged"})
        assert response.status_code == 201
        assert response.json()["status"] == "created"
        assert any(t.id == 9876543 and t.name == "Existing" for t in ch.available_tags)

    def test_create_proxy_to_dict_with_non_int_id(self):
        """Lines 157-158: to_dict()'s own int()-conversion except branch.

        The real ``tags_to_edit_payload`` already sanitizes ids to int-or-absent before a
        payload dict is built, so a non-int "id" key can only be constructed by patching
        ``tags_to_edit_payload`` directly — the narrowest possible mock for this branch.
        """
        ch, real_edit = _make_forum_channel(tags=[])
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_for_channel(ch)

        with patch(
            "api.routers.tags.tags_to_edit_payload",
            # Distinct name from the request's, so the router's final `discord.utils.get`
            # by-name lookup (which selects the response tag) resolves to the genuinely
            # newly-created tag (real int id) rather than this fabricated one.
            return_value=[{"name": "SomeOtherExistingTag", "id": "not-an-int", "emoji": None}],
        ):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555/tags", json={"name": "NonIntId"})
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "created"
            assert data["data"]["name"] == "NonIntId"
            # The fabricated non-int-id tag is still present, with to_dict()'s except-branch
            # having assigned it the raw (non-int) id value verbatim.
            assert any(t.id == "not-an-int" for t in ch.available_tags)


# =============================================================================
# PUT /tags/{tag_id} — line 251 (elif hasattr(parent_channel, "edit_tag") path)
# =============================================================================


class TestUpdateTagEditTagPath:
    """Line 251: tag has no edit, channel HAS edit_tag → uses edit_tag path.

    The installed discord.py's ForumTag/ForumChannel have neither by default;
    ``edit_tag`` is attached explicitly here to model a hypothetical richer variant.
    """

    def test_update_uses_edit_tag_when_no_tag_edit(self):
        """Line 251-252: tag has no edit attr, channel.edit_tag exists → called."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        ch.edit_tag = AsyncMock()
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        ch.edit_tag.assert_called_once()

    def test_update_edit_tag_called_with_emoji(self):
        """Line 251: channel.edit_tag path also works when emoji is provided."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        ch.edit_tag = AsyncMock()
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated", "emoji": "🚀"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        ch.edit_tag.assert_called_once()


# =============================================================================
# PUT /tags/{tag_id} — lines 257-259 (int(tag_id) raises in fallback path)
# =============================================================================


class TestUpdateTagIntConversionFails:
    """Lines 253-259: tags_to_edit_payload fallback path when tag has no edit/edit_tag."""

    def test_update_tag_int_tag_id_raises_uses_raw_key(self):
        """Lines 253-259: tag_id is int via HTTP; int(tag_id) branch succeeds normally.

        NOTE: The except branch at lines 257-259 (raw tag_id fallback when int() raises)
        is unreachable via the HTTP endpoint because FastAPI enforces `tag_id: int` in the
        path parameter declaration — any non-integer path value is rejected with HTTP 422
        before the handler runs. The reachable path exercised here is the try-branch
        (lines 253-256): int(tag_id) succeeds, upd_map is keyed by int, and the real
        tags_to_edit_payload fallback runs end-to-end.
        """
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        ch.edit.assert_called_once()


# =============================================================================
# PUT /tags/{tag_id} — lines 271-277 (update _TagProxy.to_dict() called)
# =============================================================================


class TestUpdateTagProxyToDictCalled:
    """Lines 271-277: _TagProxy.to_dict() is actually invoked in update proxy fallback."""

    def test_update_proxy_to_dict_invoked_no_id(self):
        """Lines 271-274, 277: to_dict() called via the real tags_to_edit_payload fallback
        after channel.edit(dict payloads) raises AttributeError."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, real_edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        assert ch.edit.await_count == 2

    def test_update_proxy_to_dict_invoked_with_int_id(self):
        """Lines 271-274, 277: to_dict() called; the real tags_to_edit_payload output carries
        the tag's genuine int id through the proxy."""
        tag = _make_tag(tag_id=9876543, name="Tag")
        ch, real_edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/9876543", json={"name": "Updated"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["id"] == 9876543

    def test_update_proxy_to_dict_invoked_with_non_int_id(self):
        """Lines 275-276: to_dict()'s int()-conversion except branch — only reachable by
        patching ``tags_to_edit_payload`` directly (see TestCreateTagProxyToDictCalled note)."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, real_edit = _make_forum_channel(tags=[tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, AttributeError("first edit raises")))
        bot = _bot_with_tag_in_guild(ch)

        with patch(
            "api.routers.tags.tags_to_edit_payload",
            # Distinct name from the request's, so neither the post-edit id-lookup (the
            # fabricated tag's id is a string, not the real int tag_id) nor the name-lookup
            # fallback resolves to this fabricated tag — the router falls back to the
            # original (pre-edit, real int id) tag object, per line 292.
            return_value=[{"name": "SomeOtherName", "id": "not-an-int", "emoji": None}],
        ):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "updated"
            assert any(t.id == "not-an-int" for t in ch.available_tags)


# =============================================================================
# PUT /tags/{tag_id} — lines 281-283 (outer except Exception block in update)
# =============================================================================


class TestUpdateTagOuterExceptBlock:
    """Lines 281-283: outer `except Exception as exc: raise exc from exc` in update."""

    def test_update_tag_edit_raises_runtime_error(self):
        """Lines 281-283: tag.edit() raises RuntimeError → real handle_discord_exception → 500."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        tag.edit = AsyncMock(side_effect=RuntimeError("edit failed"))
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 500
        assert "detail" in response.json()

    def test_update_tag_edit_tag_raises_runtime_error(self):
        """Lines 281-283: channel.edit_tag() raises RuntimeError → real handle_discord_exception → 500."""
        tag = _make_tag(tag_id=1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        ch.edit_tag = AsyncMock(side_effect=RuntimeError("edit_tag failed"))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
        assert response.status_code == 500
        assert "detail" in response.json()


# =============================================================================
# PUT /tags/{tag_id} — lines 301-302 (dict response emoji normalization raises)
# =============================================================================


class TestUpdateTagDictResponseEmojiNormalizeRaises:
    """Lines 301-302: real dict response with emoji, normalize_emoji raises → silently caught."""

    def test_update_dict_response_emoji_normalize_raises_silent(self):
        """Lines 299-302: tag already carries an emoji; a name-only update still re-normalizes
        it for the response, raises → except: pass (301-302)."""
        tag = _make_tag(tag_id=1234567890, name="Tag", emoji="bad_emoji")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        with patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad emoji")):
            client = TestClient(_make_app(bot))
            # No emoji in request → normalize not called for update_kwargs.
            # Real dict response has the tag's existing emoji → normalize called → raises → caught.
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_dict_response_emoji_normalize_raises_with_emoji_in_request(self):
        """Lines 299-302: dict response emoji, normalize raises after update_kwargs normalize."""
        tag = _make_tag(tag_id=1234567890, name="Tag", emoji="old")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        call_count = [0]

        def norm_side_effect(emoji):
            call_count[0] += 1
            if call_count[0] == 1:
                return "🚀"  # first call (update_kwargs) succeeds
            raise ValueError("second normalize fails")  # second call (response) fails → 301-302

        with patch("api.routers.tags.normalize_emoji", side_effect=norm_side_effect):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag", "emoji": "🚀"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"


# =============================================================================
# PUT /tags/{tag_id} — lines 318-319, 321-324 (non-dict response emoji paths)
# =============================================================================


class TestUpdateTagNonDictResponseEmojiPaths:
    """Lines 318-319, 321-324: update non-dict response, setattr raises, emoji handling.

    Only reachable by forcing the converter (real one always returns a dict).
    """

    def test_update_non_dict_response_emoji_normalize_raises(self):
        """Lines 318-319: non-dict response, setattr raises, dict has emoji → normalize raises."""
        tag = _make_tag(1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenUpdatedTagWithEmoji:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = "🔥"

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with (
            _force_payload(_FrozenUpdatedTagWithEmoji()),
            patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad")),
        ):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_non_dict_response_emoji_none_with_requested_emoji(self):
        """Lines 321-322: non-dict response, setattr raises, dict emoji=None, request emoji not None."""
        tag = _make_tag(1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenNoEmoji:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = None

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        with _force_payload(_FrozenNoEmoji()), patch("api.routers.tags.normalize_emoji", return_value="🚀"):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag", "emoji": "🚀"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_non_dict_response_emoji_none_normalize_raises(self):
        """Lines 323-324: non-dict response, setattr raises, dict emoji=None, normalize raises → raw emoji."""
        tag = _make_tag(1234567890, name="Tag")
        ch, _edit = _make_forum_channel(tags=[tag])
        bot = _bot_with_tag_in_guild(ch)

        class _FrozenNoEmoji2:
            id = 1234567890
            channel_id = 555555
            name = "Tag"
            emoji = None

            def __setattr__(self, key, value):
                raise AttributeError("frozen")

            @property
            def __dict__(self):
                return {"id": self.id, "channel_id": self.channel_id, "name": self.name, "emoji": self.emoji}

        call_count = [0]

        def norm_side_effect(emoji):
            call_count[0] += 1
            if call_count[0] == 1:
                return "🚀"
            raise ValueError("response normalize fails")

        with _force_payload(_FrozenNoEmoji2()), patch("api.routers.tags.normalize_emoji", side_effect=norm_side_effect):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Tag", "emoji": "🚀"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"


# =============================================================================
# DELETE /tags/{tag_id} — lines 391-393 (malformed tag in remaining list)
# =============================================================================


class TestDeleteTagMalformedRemaining:
    """Lines 391-393: tag in remaining list where t.name raises → except: continue."""

    def test_delete_malformed_tag_in_remaining_skipped(self):
        """Lines 391-393: malformed tag in remaining (t.name raises) → skipped via continue."""
        tag_to_delete = _make_tag(tag_id=1234567890, name="Del Tag")

        bad_remaining_tag = MagicMock()
        bad_remaining_tag.id = 9999999
        type(bad_remaining_tag).name = property(lambda self: (_ for _ in ()).throw(AttributeError("broken name")))

        good_remaining_tag = _make_tag(tag_id=7777777, name="Keep Me")

        ch, real_edit = _make_forum_channel(tags=[tag_to_delete, bad_remaining_tag, good_remaining_tag])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, TypeError("wrong type")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        # The malformed tag was dropped by the real `with suppress(Exception)`; the good one
        # survives by name (delete_tag's own dict-payload loop — unlike tags_to_edit_payload —
        # never carries the original id forward, so the rebuilt tag gets a freshly synthesized
        # id; this is itself a minor real-code fidelity gap, not something this test asserts on).
        assert any(t.name == "Keep Me" for t in ch.available_tags)
        assert not any(t.id == 9999999 for t in ch.available_tags)
        assert not any(t.id == 1234567890 for t in ch.available_tags)

    def test_delete_all_remaining_malformed_empty_payloads(self):
        """Lines 391-393: ALL remaining tags are malformed → payloads=[] → real edit called with []."""
        tag_to_delete = _make_tag(tag_id=1234567890, name="Del Tag")

        bad_tag1 = MagicMock()
        bad_tag1.id = 8888888
        type(bad_tag1).name = property(lambda self: (_ for _ in ()).throw(RuntimeError("name error")))

        bad_tag2 = MagicMock()
        bad_tag2.id = 7777777
        type(bad_tag2).name = property(lambda self: (_ for _ in ()).throw(ValueError("name val error")))

        ch, real_edit = _make_forum_channel(tags=[tag_to_delete, bad_tag1, bad_tag2])
        ch.edit = AsyncMock(side_effect=_chained_edit(real_edit, TypeError("wrong type")))
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.available_tags == []


# =============================================================================
# DELETE /tags/{tag_id} — lines 404-410 (delete _TagProxy.to_dict() called)
# =============================================================================


class TestDeleteTagProxyToDictCalled:
    """Lines 404-410: _TagProxy.to_dict() is actually invoked in delete proxy fallback.

    Note: delete_tag's own dict-payload loop (``{"name": t.name, "emoji": ...}``) never
    includes an "id" key for ANY remaining tag — unlike ``tags_to_edit_payload`` used by
    create/update, there is no id-sanitization step here at all. So (unlike the
    create/update proxy classes) there is no real "with int id" variant to construct; all
    three tests below exercise the same real id-less proxy shape, varying only the
    remaining tags' other real attributes.
    """

    def test_delete_proxy_to_dict_invoked_no_emoji(self):
        """Lines 404-407, 410: to_dict() called on proxy; remaining tag has no emoji."""
        tag_to_delete = _make_tag(tag_id=1234567890, name="Del Tag")
        keep_tag = _make_tag(tag_id=9999999, name="Keep")
        ch, real_edit = _make_forum_channel(tags=[tag_to_delete, keep_tag])
        ch.edit = AsyncMock(
            side_effect=_chained_edit(real_edit, TypeError("first: wrong type"), AttributeError("second: no dicts"))
        )
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 3

    def test_delete_proxy_to_dict_invoked_with_emoji(self):
        """Lines 404-407, 410: to_dict() called on proxy; remaining tag carries a real emoji."""
        tag_to_delete = _make_tag(tag_id=1234567890, name="Del Tag")
        keep_tag = _make_tag(tag_id=9999999, name="Keep", emoji="🎯")
        ch, real_edit = _make_forum_channel(tags=[tag_to_delete, keep_tag])
        ch.edit = AsyncMock(
            side_effect=_chained_edit(real_edit, TypeError("first: wrong type"), AttributeError("second: no dicts"))
        )
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 3

    def test_delete_proxy_to_dict_invoked_with_multiple_remaining(self):
        """Lines 404-407, 410: to_dict() called on the proxy for each of several remaining tags."""
        tag_to_delete = _make_tag(tag_id=1234567890, name="Del Tag")
        keep_1 = _make_tag(tag_id=9999999, name="Keep1")
        keep_2 = _make_tag(tag_id=8888888, name="Keep2", emoji="🚀")
        ch, real_edit = _make_forum_channel(tags=[tag_to_delete, keep_1, keep_2])
        ch.edit = AsyncMock(
            side_effect=_chained_edit(real_edit, TypeError("first: wrong type"), AttributeError("second: no dicts"))
        )
        bot = _bot_with_tag_in_guild(ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 3
