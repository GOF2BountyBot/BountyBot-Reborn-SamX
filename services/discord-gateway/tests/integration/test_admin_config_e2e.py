"""
E2E gateway-boundary test harness for /admin_config (issue #70 admin-config overhaul).

Exercises the REAL AdminCog code end-to-end at the interaction boundary — no live
Discord account, no browser, no real Discord token.

URL approach
------------
BOT_API_BASE_URL is set to ``http://bountydev-bot-core:8000/api/v1`` (container-
accessible URL for the dev bot-core on ``bountydev-net``).  Tests run inside the
``bountydev-discord-gateway`` container (or any container on the same network).
The env var is written before any cog module is imported so the module-level
``api_base`` in ``adminCog.py`` picks it up.  All cog HTTP calls (view, set,
validate, help, reset) therefore hit the live dev bot-core directly — no respx
interception on the hot path.  Fall back to ``http://localhost:18000/api/v1`` when
running on the Docker host (if all packages are locally available).

Teardown safety
---------------
Any test that MUTATEs dev config resets the field in a finally block via a direct
``httpx`` call to the bot-core reset endpoint, preserving:
  ``check_cooldown=15``  and  ``tier_change_cooldown=300``.

Discord limits checked (all-page sweep for view)
-------------------------------------------------
  • embed description  ≤ 4096 chars
  • embed fields       ≤ 25 per embed
  • embed total        ≤ 6000 chars  (title + description + all field name/value text)
"""

import asyncio
import os
import sys
import types
import urllib.request
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# 1. Set BOT_API_BASE_URL BEFORE any cog import so module-level api_base picks it up.
#    The dev bot-core listens on 18000 both inside the container network and on
#    the host (compose maps 18000->18000). Run this harness with:
#      docker run --rm --network bountydev-net \
#        -e BOT_API_BASE_URL=http://bountydev-bot-core:18000/api/v1 ...
#    The resolver below is the fallback when the env var is unset.
# ---------------------------------------------------------------------------
def _resolve_bot_core_url() -> str:
    """Return the first reachable bot-core URL, preferring the container-network address."""
    for url in (
        "http://bountydev-bot-core:18000/api/v1",  # container network (compose service:port)
        "http://localhost:18000/api/v1",  # docker host
    ):
        try:
            urllib.request.urlopen(f"{url}/health", timeout=2)
            return url
        except Exception:
            continue
    # If neither is reachable fall back to the container URL; tests will fail with a
    # clear connection error rather than an import-time crash.
    return "http://bountydev-bot-core:18000/api/v1"


_LIVE_BOT_CORE_URL = os.environ.get("BOT_API_BASE_URL") or _resolve_bot_core_url()

# ---------------------------------------------------------------------------
# This is a LIVE-integration harness: it drives the real AdminCog against a
# running bot-core. If none is reachable (e.g. CI's gateway unit-test job, which
# has no bot-core service), skip the whole module cleanly rather than failing —
# it is meant to be run against the dev stack, not in unit CI.
#
# CRITICAL: probe and skip BEFORE mutating the global os.environ below. Pytest
# imports every collected test module in the worker process; setting
# BOT_API_BASE_URL at import time and THEN skipping would still leak the value
# to every other cog test in that worker (their api_base is read at import),
# which silently broke the entire gateway suite. Skipping first keeps this
# module's env changes from ever escaping when there is no live bot-core.
# ---------------------------------------------------------------------------
try:
    urllib.request.urlopen(f"{_LIVE_BOT_CORE_URL}/config/metadata", timeout=3).read(1)
except OSError as _exc:  # URLError is an OSError subclass — covers DNS/connect/timeout
    pytest.skip(
        f"live bot-core not reachable at {_LIVE_BOT_CORE_URL} ({type(_exc).__name__}); "
        "this integration harness runs against the dev stack, not unit CI",
        allow_module_level=True,
    )

# Only reached when bot-core is live (dev stack): point the cog imports below at it.
os.environ["BOT_API_BASE_URL"] = _LIVE_BOT_CORE_URL

# ---------------------------------------------------------------------------
# 2. Mock shared.bblogger so the cog can be imported without the real shared package.
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
    return logger


def _close_coro(coro):
    coro.close()
    return MagicMock()


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Evict stale discord/cog modules to force a clean import of the cog.
for _mod in list(sys.modules):
    if _mod == "discord" or _mod.startswith("discord.") or _mod.startswith("cogs."):
        sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# 3. Import AdminCog (after env var is set so api_base is correct).
# ---------------------------------------------------------------------------
from cogs.adminCog import AdminCog

from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GUILD_ID = 711548456019296289
"""Dev guild used for all E2E assertions; has check_cooldown=15, tier_change_cooldown=300."""

_DISCORD_EMBED_DESC_LIMIT = 4096
_DISCORD_EMBED_FIELDS_LIMIT = 25
_DISCORD_EMBED_TOTAL_LIMIT = 6000

# A safe game-constant field to mutate in set tests.
# Currently null in the dev guild → safe to toggle and reset.
_SAFE_FLOAT_FIELD = "shop_combat_module_prob"
_SAFE_FLOAT_VALUE = 0.5

# ---------------------------------------------------------------------------
# 4. Helpers
# ---------------------------------------------------------------------------


def _api_metadata_to_cog_format(api_response: dict) -> list[dict]:
    """Convert GET /config/metadata response to the list format AdminCog._config_metadata expects.

    The live API returns ``{"fields": {"field_name": {type, min, max, default, description, deprecated}}}``.
    AdminCog expects a list of ``{field, type, ge, le, default, description, category, deprecated, replaced_by}``.
    This helper is the *correct* conversion the cog's ``_fetch_config_metadata`` should apply but currently
    does not — the mismatch is the metadata-bootstrap finding documented in the test below.
    """
    result = []
    for field_name, meta in api_response.get("fields", {}).items():
        field_type = meta.get("type", "int")
        if field_type == "dict":
            # dict fields are not slash-settable; skip them for cog metadata list
            continue
        result.append(
            {
                "field": field_name,
                "type": field_type,
                "ge": meta.get("min"),
                "le": meta.get("max"),
                "default": meta.get("default"),
                "description": meta.get("description", ""),
                "category": "General",  # API response has no category field (rev 0033 overhaul)
                "deprecated": meta.get("deprecated", False),
                "replaced_by": None,
            }
        )
    return result


def _fetch_live_metadata() -> dict:
    """Fetch the raw /config/metadata response from the live dev bot-core."""
    resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/metadata", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _embed_total_chars(embed) -> int:
    """Count total characters across title, description, and all embed field names+values."""
    total = 0
    if embed.title:
        total += len(embed.title)
    if embed.description:
        total += len(embed.description)
    for field in embed.fields:
        total += len(field.name or "") + len(field.value or "")
    return total


def _check_discord_limits(embed, label: str = "") -> None:
    """Assert that a discord.Embed does not exceed any Discord limits."""
    prefix = f"[{label}] " if label else ""
    desc_len = len(embed.description or "")
    fields_count = len(embed.fields)
    total = _embed_total_chars(embed)

    assert desc_len <= _DISCORD_EMBED_DESC_LIMIT, (
        f"{prefix}embed description {desc_len} chars exceeds Discord limit {_DISCORD_EMBED_DESC_LIMIT}"
    )
    assert fields_count <= _DISCORD_EMBED_FIELDS_LIMIT, (
        f"{prefix}embed has {fields_count} fields, Discord limit is {_DISCORD_EMBED_FIELDS_LIMIT}"
    )
    assert total <= _DISCORD_EMBED_TOTAL_LIMIT, (
        f"{prefix}embed total {total} chars exceeds Discord limit {_DISCORD_EMBED_TOTAL_LIMIT}"
    )


def _create_admin_interaction(guild_id: int = GUILD_ID) -> MagicMock:
    """Create a fake Discord Interaction with admin permissions and capture buffers."""
    interaction = DiscordMockUtils.create_mock_interaction(guild_id=guild_id)
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "DevGuild"
    # Built-in Discord Administrator → _check_is_admin returns True immediately.
    interaction.user.guild_permissions.administrator = True
    return interaction


def _run_admin_config(
    cog: AdminCog,
    interaction,
    *,
    action: str,
    setting: str | None = None,
    int_value: int | None = None,
    float_value: float | None = None,
    bool_value: bool | None = None,
    text_value: str | None = None,
    only_overridden: bool = True,
) -> None:
    """Synchronously invoke admin_config.callback via asyncio.run()."""
    asyncio.run(
        cog.admin_config.callback(
            cog,
            interaction,
            action=action,
            setting=setting,
            int_value=int_value,
            float_value=float_value,
            bool_value=bool_value,
            text_value=text_value,
            only_overridden=only_overridden,
        )
    )


def _get_followup_embed(interaction) -> object | None:
    """Extract the embed kwarg from the last followup.send call, or None.

    Returns None (not a plain string) if the cog sent an error message string instead
    of an embed — callers can distinguish embed-responses from plain-text responses.
    """
    import discord as _discord

    call = interaction.followup.send.call_args
    if call is None:
        return None
    embed = call.kwargs.get("embed")
    if isinstance(embed, _discord.Embed):
        return embed
    # Fallback: first positional arg if it's an Embed (rare but possible)
    if call.args and isinstance(call.args[0], _discord.Embed):
        return call.args[0]
    return None


def _get_followup_content(interaction) -> str:
    """Extract the string content from the last followup.send call."""
    call = interaction.followup.send.call_args
    if call is None:
        return ""
    return call.args[0] if call.args else call.kwargs.get("content", "")


def _reset_field_via_api(field: str) -> None:
    """Direct bot-core call to reset a single game-constant field; used in teardown."""
    resp = httpx.post(
        f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants/reset",
        json={"fields": [field]},
        timeout=10,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# 5. Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="E2ETestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture(scope="module")
def cog(mock_bot):
    """AdminCog pointed at the live dev bot-core (no respx interception).

    Module-scoped so the cog's internal state (catalogs, metadata cache) persists
    across tests within the module.  The http_client is refreshed per-test by the
    ``fresh_cog_client`` autouse fixture to avoid stale event-loop connections
    across consecutive ``asyncio.run()`` calls.
    """
    c = AdminCog(mock_bot)
    # Keep metadata empty — individual tests load it as needed
    c._config_metadata = []
    c._config_metadata_by_field = {}
    yield c
    # Do NOT call asyncio.run(c.http_client.aclose()) here — pytest-asyncio has
    # already closed the event loop at module teardown, so asyncio.run() would raise
    # "RuntimeError: Event loop is closed".  The GC will collect the client.


@pytest.fixture(autouse=True)
def fresh_cog_client(cog):
    """Give the cog a brand-new httpx.AsyncClient before every test.

    ``asyncio.run()`` creates and immediately destroys its event loop.  An
    ``httpx.AsyncClient`` that made connections on a now-closed loop has stale
    transport state that causes the FIRST request on the *next* ``asyncio.run()``
    loop to fail.  Replacing the client before each test avoids this entirely.
    The stale client is not explicitly closed (same asyncio.run teardown problem).
    """
    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    yield


class _AlwaysFailTransport(httpx.AsyncBaseTransport):
    """Custom httpx transport that always raises ConnectError — simulates a dead endpoint."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Simulated dead endpoint", request=request)


@pytest.fixture()
def cog_dead_client(cog):
    """Replace cog.http_client with a transport that always fails, restore on teardown.

    Avoids the asyncio.run()-in-finally pattern which fails when pytest-asyncio has
    already torn down the event loop.  The fake transport never establishes a real
    connection so no async cleanup is needed.
    """
    original_client = cog.http_client
    dead_client = httpx.AsyncClient(transport=_AlwaysFailTransport(), timeout=httpx.Timeout(1.0))
    cog.http_client = dead_client
    yield cog
    # Restore synchronously — no asyncio needed since no real socket was opened
    cog.http_client = original_client


@pytest.fixture()
def cog_with_real_metadata(cog):
    """Load live metadata (converted to cog format) for this test, restore empty after."""
    raw = _fetch_live_metadata()
    metadata_list = _api_metadata_to_cog_format(raw)
    cog._config_metadata = metadata_list
    cog._config_metadata_by_field = {m["field"]: m for m in metadata_list}
    yield cog
    cog._config_metadata = []
    cog._config_metadata_by_field = {}


# ===========================================================================
# 6. Test classes
# ===========================================================================


class TestMetadataBootstrap:
    """Cog fetches GET /config/metadata on preload; verify real endpoint behaviour."""

    def test_metadata_endpoint_is_reachable(self):
        """Live bot-core must respond at /config/metadata."""
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/metadata", timeout=10)
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert "fields" in data, "Response must have a 'fields' key"
        assert len(data["fields"]) >= 110, f"Expected ≥110 fields, got {len(data['fields'])}"

    def test_metadata_normalised_to_consumer_shape(self, cog):
        """REGRESSION (was a real bug): _fetch_config_metadata must normalise the
        endpoint's ``{"fields": {name: {type, min, max, ...}}}`` into the list-of-
        descriptor shape every consumer expects — keyed ``field``, bounds under
        ``ge``/``le`` — so type-checking, the range pre-check, per-field help, and
        metadata-driven autocomplete actually function in production.

        The original code returned ``resp.json()`` raw, so the preload's
        ``{m["field"]: m for m in metadata}`` iterated dict KEYS and left
        ``_config_metadata_by_field`` permanently empty.
        """
        meta = asyncio.run(cog._fetch_config_metadata())
        assert isinstance(meta, list), f"Expected a list of descriptors; got {type(meta).__name__}"
        assert len(meta) >= 112, f"Expected ≥112 settable fields (110 + 2 core), got {len(meta)}"
        by_field = {m["field"]: m for m in meta}
        # The two core scalars the static fallback list omits must be present.
        assert "starting_credits" in by_field
        assert "sale_price_factor" in by_field
        # A representative bounded int field carries ge/le under the consumer keys.
        cc = by_field["check_cooldown"]
        assert cc["type"] == "int"
        assert cc["ge"] == 0 and cc["le"] == 86400, f"bounds not normalised: {cc}"
        assert cc["default"] == 180
        assert cc.get("category") == "Timers"
        # Every descriptor has the keys the cog dereferences with [] (not .get()).
        for m in meta:
            assert "field" in m and "category" in m

    def test_autocomplete_fallback_when_metadata_empty(self, cog):
        """With _config_metadata=[] the autocomplete falls back to _GAME_CONSTANT_FIELDS."""
        assert not cog._config_metadata, "metadata must be empty for this test"
        interaction = _create_admin_interaction()
        choices = asyncio.run(cog.setting_autocomplete(interaction, "bounty"))
        assert len(choices) > 0, "Autocomplete must return results from _GAME_CONSTANT_FIELDS"
        assert len(choices) <= 25, f"Autocomplete must not exceed 25 choices; got {len(choices)}"
        values = {c.value for c in choices}
        assert any("bounty" in v for v in values), "Expected bounty-related fields in results"
        # All values must be bare field names (no suffixes)
        for choice in choices:
            assert choice.value == choice.value.strip(), "Choice value must not have extra whitespace"

    def test_autocomplete_fallback_dead_endpoint_leaves_metadata_empty(self, cog_dead_client):
        """When the metadata endpoint is unreachable the cog retains empty _config_metadata.

        Uses the ``cog_dead_client`` fixture which swaps ``cog.http_client`` for a
        transport that always raises ConnectError, then restores it via fixture teardown.
        This avoids calling asyncio.run() inside a finally block (which fails when
        pytest-asyncio has already closed the event loop).
        """
        cog_under_test = cog_dead_client
        # The preload method should raise some transport-level error
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout, Exception)):
            asyncio.run(cog_under_test._fetch_config_metadata())

        # The metadata cache must remain empty (the preload loop would have caught this)
        assert not cog_under_test._config_metadata

        # Autocomplete still works (falls back to static list)
        interaction = _create_admin_interaction()
        choices = asyncio.run(cog_under_test.setting_autocomplete(interaction, "check"))
        assert len(choices) > 0
        assert len(choices) <= 25


class TestAutocompleteAtScale:
    """Autocomplete with real metadata loaded: ≤25, substring filter works, type coverage."""

    def test_returns_at_most_25_choices(self, cog_with_real_metadata):
        interaction = _create_admin_interaction()
        # Empty string → all fields: must still honour the 25-choice cap
        choices = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, ""))
        assert len(choices) <= 25, f"Autocomplete returned {len(choices)} choices; Discord cap is 25"

    def test_substring_filter_narrows_results(self, cog_with_real_metadata):
        interaction = _create_admin_interaction()
        choices_all = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, ""))
        choices_filtered = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, "bounty"))
        values_filtered = {c.value for c in choices_filtered}
        assert all("bounty" in v for v in values_filtered), "Filtered choices must all contain the substring 'bounty'"
        assert len(choices_filtered) <= len(choices_all) or len(choices_all) == 25

    def test_no_dict_type_fields_in_choices(self, cog_with_real_metadata):
        """_api_metadata_to_cog_format strips dict-type fields; they must not appear."""
        interaction = _create_admin_interaction()
        choices = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, ""))
        # Verify none of the known retired dict fields appear
        retired_dict_fields = {
            "division_max_tl",
            "bounty_division_reward_mult",
            "primary_tl_band_weights",
            "criminal_cloak_chance_by_division",
        }
        values = {c.value for c in choices}
        overlap = values & retired_dict_fields
        assert not overlap, f"Retired dict fields in autocomplete: {overlap}"

    def test_specific_field_in_choices(self, cog_with_real_metadata):
        """Narrow search for a known field must surface it."""
        interaction = _create_admin_interaction()
        choices = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, "check_cooldown"))
        values = {c.value for c in choices}
        assert "check_cooldown" in values, "check_cooldown must surface via its full name"

    def test_choice_value_is_bare_field_name(self, cog_with_real_metadata):
        """Choice values must be the bare field name; labels may have suffixes."""
        interaction = _create_admin_interaction()
        choices = asyncio.run(cog_with_real_metadata.setting_autocomplete(interaction, "shop"))
        for c in choices:
            assert " " not in c.value, f"Choice value '{c.value}' must be a bare underscore_field_name"


class TestValidate:
    """action:validate renders the validation report embed."""

    def test_validate_hits_real_endpoint_and_renders_embed(self, cog):
        """Calls the live /validate endpoint and renders a status embed."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="validate")
        embed = _get_followup_embed(interaction)
        assert embed is not None, (
            f"validate must produce an embed; actual followup.send call: {interaction.followup.send.call_args}"
        )
        title = embed.title or ""
        assert title, "embed must have a title"
        # Dev guild should be valid
        assert "Valid" in title or "Invalid" in title, f"Title should indicate validation status; got: '{title}'"
        # Fields must show errors/warnings sections
        field_names = {f.name for f in embed.fields}
        assert "❌ Errors" in field_names, f"Expected '❌ Errors' field; got {field_names}"
        assert "⚠️ Warnings" in field_names, f"Expected '⚠️ Warnings' field; got {field_names}"
        _check_discord_limits(embed, label="validate")

    def test_validate_dev_guild_is_valid(self, cog):
        """Dev guild config must report valid (it has channels + roles configured)."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="validate")
        embed = _get_followup_embed(interaction)
        assert embed is not None
        title = embed.title or ""
        assert "✅" in title or "Valid" in title, f"Dev guild should have valid config; embed title: '{title}'"


class TestViewAction:
    """action:view — embed size limits and content correctness."""

    def test_view_overrides_only_true_discord_limits(self, cog):
        """only_overridden=True must produce a single embed within Discord limits."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="view", only_overridden=True)
        embed = _get_followup_embed(interaction)
        assert embed is not None, (
            f"view must produce an embed; actual followup.send call: {interaction.followup.send.call_args}"
        )
        _check_discord_limits(embed, label="view/overrides-only")

    def test_view_overrides_shows_check_cooldown_and_tier_change_cooldown(self, cog):
        """Dev guild has check_cooldown=15 and tier_change_cooldown=300 set."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="view", only_overridden=True)
        embed = _get_followup_embed(interaction)
        assert embed is not None, (
            f"view must produce an embed; actual followup.send call: {interaction.followup.send.call_args}"
        )
        desc = embed.description or ""
        assert "check_cooldown" in desc, f"check_cooldown must appear in overrides embed; desc={desc!r}"
        assert "15" in desc, "check_cooldown value 15 must appear in description"
        assert "tier_change_cooldown" in desc, "tier_change_cooldown must appear in overrides embed"
        assert "300" in desc, "tier_change_cooldown value 300 must appear in description"

    def test_view_only_overridden_false_all_page_embeds_within_discord_limits(self, cog_with_real_metadata):
        """All paginated embeds (every category page) must stay within Discord limits.

        This is the highest-value check: a 110+ field view is the most likely real-world
        breakage point. We iterate all category pages and assert each embed independently.
        """
        import discord as _discord

        interaction = _create_admin_interaction()
        _run_admin_config(cog_with_real_metadata, interaction, action="view", only_overridden=False)

        # Capture the ConfigPageView passed to followup.send
        call = interaction.followup.send.call_args
        assert call is not None, "view must call followup.send"

        # Extract embed and view from call kwargs (may come via positional or keyword arg)
        sent_embed = call.kwargs.get("embed")
        sent_view = call.kwargs.get("view")
        if sent_embed is None and call.args:
            # Plain string error message was sent instead of embed
            raise AssertionError(
                f"view (only_overridden=False) sent a plain-text message instead of an embed: "
                f"{call.args[0]!r}. "
                "This usually means the HTTP call to bot-core failed."
            )
        assert isinstance(sent_embed, _discord.Embed), (
            f"view must send a discord.Embed; got {type(sent_embed).__name__}"
        )
        _check_discord_limits(sent_embed, label="view/full/page-0")

        # If a paginated view was sent, check every page
        if sent_view is not None and hasattr(sent_view, "categories"):
            n_pages = len(sent_view.categories)
            for idx in range(n_pages):
                sent_view.current_idx = idx
                page_embed = sent_view.build_embed()
                _check_discord_limits(page_embed, label=f"view/full/page-{idx}/{sent_view.categories[idx]!r}")
        else:
            # Single embed (no pagination — only_overridden=False with static fallback)
            _check_discord_limits(sent_embed, label="view/full/single-embed")

    def test_view_no_overridden_fields_shows_no_overrides_message(self):
        """With a fresh cog pointing at a guild with no overrides, the embed says so.

        Uses a fake guild_id (987654321) which won't exist in the live dev stack;
        the game-constants endpoint returns an empty dict → no overrides message.
        Note: bot-core returns 404 for unknown guilds; we test with a known empty guild
        by creating the config first or by using respx. Since we're in an E2E harness
        and don't want to create a spurious guild, we skip this path test here —
        the unit tests in test_adminCog_unified_config.py already cover it with respx.
        """
        pytest.skip(
            "Skipped in E2E harness: testing no-overrides path requires a guild with no config. "
            "The respx-based unit test in test_adminCog_unified_config.py covers this path."
        )


class TestHelpAction:
    """action:help — per-setting embed with type, range, default, current value."""

    def test_help_overview_no_setting(self, cog):
        """action:help with no setting shows a usage overview embed."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="help", setting=None)
        embed = _get_followup_embed(interaction)
        assert embed is not None, "help overview must produce an embed"
        title = embed.title or ""
        assert "Help" in title, f"Expected 'Help' in title; got '{title}'"
        _check_discord_limits(embed, label="help/overview")

    def test_help_scalar_field_with_real_metadata(self, cog_with_real_metadata):
        """Per-setting help for a scalar field shows Type, Range, Default, Current, Set with."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog_with_real_metadata, interaction, action="help", setting="check_cooldown")
        embed = _get_followup_embed(interaction)
        assert embed is not None, "per-setting help must produce an embed"
        title = embed.title or ""
        assert "check_cooldown" in title, f"Setting name must appear in title; got '{title}'"
        field_names = {f.name for f in embed.fields}
        assert "Type" in field_names, f"Expected 'Type' field; got {field_names}"
        assert "Range" in field_names, f"Expected 'Range' field; got {field_names}"
        # check_cooldown is currently overridden to 15 in the dev guild;
        # the embed should show it as overridden (15) not the global default.
        current_vals = [f.value for f in embed.fields if f.name == "Current"]
        assert current_vals, "Expected a 'Current' field"
        # The current value display must mention 15 (the override) with *(overridden)* annotation
        current_text = current_vals[0]
        assert "15" in current_text and "overridden" in current_text, (
            f"Current field must show '15' as overridden value; got '{current_text}'"
        )
        _check_discord_limits(embed, label="help/check_cooldown")

    def test_help_bool_field_type_annotation(self, cog_with_real_metadata):
        """criminal_exclude_emp_weapons is bool — help embed must show type=bool."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog_with_real_metadata, interaction, action="help", setting="criminal_exclude_emp_weapons")
        embed = _get_followup_embed(interaction)
        assert embed is not None
        type_vals = [f.value for f in embed.fields if f.name == "Type"]
        assert type_vals, "Expected a 'Type' field"
        assert "bool" in type_vals[0].lower(), f"Expected type=bool for bool field; got '{type_vals[0]}'"
        _check_discord_limits(embed, label="help/criminal_exclude_emp_weapons")

    def test_help_unknown_field_no_metadata_graceful(self, cog):
        """When metadata is not loaded, help for any field shows a fallback embed."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="help", setting="check_cooldown")
        embed = _get_followup_embed(interaction)
        assert embed is not None, "help must produce an embed even without loaded metadata"
        title = embed.title or ""
        # Should show the setting name and some mention of current value
        assert "check_cooldown" in title, f"Setting name must appear in title; got '{title}'"


class TestSetAction:
    """action:set — happy path, bounds violation, type violation."""

    def test_set_happy_path_float_then_reset(self, cog):
        """Set shop_combat_module_prob=0.5 on live dev guild, verify success, then reset.

        This test MUTATES dev config; teardown resets the field via direct API call so
        dev state (check_cooldown=15, tier_change_cooldown=300) is preserved intact.
        """
        # Pre-condition: field must not already be set
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp.raise_for_status()
        pre = resp.json()
        assert pre.get(_SAFE_FLOAT_FIELD) is None, (
            f"{_SAFE_FLOAT_FIELD} must be null before the set test; got {pre.get(_SAFE_FLOAT_FIELD)}"
        )

        interaction = _create_admin_interaction()
        try:
            _run_admin_config(cog, interaction, action="set", setting=_SAFE_FLOAT_FIELD, float_value=_SAFE_FLOAT_VALUE)
        finally:
            _reset_field_via_api(_SAFE_FLOAT_FIELD)

        embed = _get_followup_embed(interaction)
        assert embed is not None, "set must produce an embed on success"
        title = embed.title or ""
        assert "✅" in title or "updated" in title.lower(), f"Expected success embed; got title '{title}'"
        # Embed must mention the field and the new value
        all_text = (embed.title or "") + (embed.description or "") + "".join(f.name + f.value for f in embed.fields)
        assert _SAFE_FLOAT_FIELD in all_text, "Success embed must name the field"
        assert str(_SAFE_FLOAT_VALUE) in all_text, "Success embed must show the new value"
        _check_discord_limits(embed, label="set/happy-path")

    def test_set_no_put_after_happy_path_reset(self, cog):
        """Confirm the dev guild field was reset back to null after the set test."""
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        assert data.get(_SAFE_FLOAT_FIELD) is None, (
            f"Teardown must have reset {_SAFE_FLOAT_FIELD} to null; got {data.get(_SAFE_FLOAT_FIELD)}"
        )

    def test_set_bounds_violation_no_put_issued(self, cog_with_real_metadata):
        """loot_band1_select_pct=150 violates [0,100] range — no PUT should be issued.

        Verify by GET before/after: if the value is still null, no write happened.
        """
        field = "loot_band1_select_pct"

        # Pre: must be null
        resp_before = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp_before.raise_for_status()
        pre_val = resp_before.json().get(field)
        assert pre_val is None, f"{field} must be null before bounds test; got {pre_val}"

        interaction = _create_admin_interaction()
        _run_admin_config(cog_with_real_metadata, interaction, action="set", setting=field, int_value=150)

        # Post: must still be null (no PUT was issued)
        resp_after = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp_after.raise_for_status()
        post_val = resp_after.json().get(field)
        assert post_val is None, f"Bounds violation must not write to DB; {field} changed to {post_val}"

        # Error message must mention the bounds
        content = _get_followup_content(interaction)
        assert "❌" in content, f"Expected error emoji in response; got: {content!r}"
        assert "between" in content.lower(), f"Error must mention 'between'; got: {content!r}"

    def test_set_type_violation_bool_field_via_int_value(self, cog_with_real_metadata):
        """criminal_exclude_emp_weapons is bool — using int_value must be rejected."""
        field = "criminal_exclude_emp_weapons"

        # Pre: must be null
        resp_before = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp_before.raise_for_status()
        pre_val = resp_before.json().get(field)
        assert pre_val is None, f"{field} must be null before type-violation test; got {pre_val}"

        interaction = _create_admin_interaction()
        _run_admin_config(cog_with_real_metadata, interaction, action="set", setting=field, int_value=1)

        # Post: must still be null
        resp_after = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp_after.raise_for_status()
        post_val = resp_after.json().get(field)
        assert post_val is None, f"Type violation must not write to DB; {field} changed to {post_val}"

        content = _get_followup_content(interaction)
        assert "❌" in content, f"Expected error emoji for type violation; got: {content!r}"
        # Error should mention the correct param to use
        assert "bool_value" in content, f"Error should tell the user to use bool_value; got: {content!r}"

    def test_set_no_value_param_returns_error(self, cog):
        """Providing no typed param must return an error with no HTTP write."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="set", setting="check_cooldown")
        content = _get_followup_content(interaction)
        assert "❌" in content, f"Expected error emoji; got: {content!r}"

    def test_set_missing_setting_returns_error(self, cog):
        """Providing no setting param must return an error."""
        interaction = _create_admin_interaction()
        _run_admin_config(cog, interaction, action="set", setting=None, int_value=5)
        content = _get_followup_content(interaction)
        assert "❌" in content, f"Expected error emoji; got: {content!r}"


class TestDevGuildStateIntegrity:
    """Session-end guard: dev guild overrides must still be exactly check_cooldown=15, tier_change_cooldown=300."""

    def test_check_cooldown_still_15(self):
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        assert data.get("check_cooldown") == 15, (
            f"check_cooldown must be 15 at session end; got {data.get('check_cooldown')}"
        )

    def test_tier_change_cooldown_still_300(self):
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        assert data.get("tier_change_cooldown") == 300, (
            f"tier_change_cooldown must be 300 at session end; got {data.get('tier_change_cooldown')}"
        )

    def test_no_unexpected_overrides_remain(self):
        """Only check_cooldown and tier_change_cooldown should be non-null in game-constants."""
        resp = httpx.get(f"{_LIVE_BOT_CORE_URL}/config/guild/{GUILD_ID}/game-constants", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        overrides = {k: v for k, v in data.items() if v is not None}
        unexpected = {k: v for k, v in overrides.items() if k not in ("check_cooldown", "tier_change_cooldown")}
        assert not unexpected, (
            f"Unexpected game-constant overrides left after E2E run: {unexpected}. "
            "A test failed to clean up its mutations."
        )
