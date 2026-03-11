"""Service-specific fixtures for discord-gateway tests with comprehensive test isolation."""
import sys
import os
import types
from unittest.mock import MagicMock, patch
from contextlib import asynccontextmanager
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any application imports.
# The gateway source does ``from shared import bblogger`` at module
# level, so we need fake modules registered in sys.modules before the import
# chain reaches them.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []  # mark as package so sub-imports work

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args: Any, **_kwargs: Any) -> MagicMock:
    """Return a MagicMock that already has common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = _make_mock_logger  # type: ignore[attr-defined]

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Now it is safe to add the src directory and import application code.
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import discord
import httpx

# ---------------------------------------------------------------------------
# Save references to the *real* discord packages here, before any test file
# can replace sys.modules["discord*"] with hand-rolled fakes.
# This is the ONLY reliable way to obtain real discord references once the
# full suite starts running (other test files do module-level
# sys.modules["discord"] = <fake> during collection).
# We save all three as separate variables because a test file may overwrite
# the module object's .ext / .ext.commands attributes even on the real object.
# ---------------------------------------------------------------------------
_REAL_DISCORD = discord
import discord.ext as _real_discord_ext
_REAL_DISCORD_EXT = _real_discord_ext
import discord.ext.commands as _real_discord_ext_commands
_REAL_DISCORD_EXT_COMMANDS = _real_discord_ext_commands

# Add the src directory to the path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(scope="session")
def mock_shared_bblogger() -> Generator[None, None, None]:
    """Yield and clean up mock shared.bblogger module."""
    try:
        yield
    finally:
        # Clean up sys.modules if needed
        if "shared" in sys.modules:
            del sys.modules["shared"]
        if "shared.bblogger" in sys.modules:
            del sys.modules["shared.bblogger"]


@pytest.fixture(scope="session")
def mock_discord_module() -> Generator[None, None, None]:
    """Yield and clean up mock discord module."""
    with patch("discord") as mock_discord:
        # Set up common Discord types
        mock_discord.Client = MagicMock
        mock_discord.User = MagicMock
        mock_discord.Guild = MagicMock
        mock_discord.TextChannel = MagicMock
        mock_discord.VoiceChannel = MagicMock
        mock_discord.CategoryChannel = MagicMock
        mock_discord.Role = MagicMock
        mock_discord.Message = MagicMock
        mock_discord.Member = MagicMock
        mock_discord.Embed = MagicMock
        mock_discord.Colour = MagicMock
        mock_discord.Intents = MagicMock

        yield mock_discord


@pytest.fixture(scope="session")
def mock_httpx_module() -> Generator[None, None, None]:
    """Yield and clean up mock httpx module."""
    with patch("httpx") as mock_httpx:
        yield mock_httpx


@pytest.fixture
def mock_discord_bot() -> Generator[MagicMock, None, None]:
    """Mock Discord bot instance with comprehensive methods."""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.guilds = []
    bot.get_guild = MagicMock(return_value=None)
    bot.get_channel = MagicMock(return_value=None)
    bot.wait_until_ready = AsyncMock()
    bot.add_cog = MagicMock()
    bot.remove_cog = MagicMock()
    bot.get_cog = MagicMock(return_value=None)
    bot.dispatch = AsyncMock()
    bot.http = MagicMock()

    yield bot

    # Clean up
    bot.reset_mock()


@pytest.fixture
def test_app() -> Generator[FastAPI, None, None]:
    """Create a test FastAPI app for discord-gateway with all routers."""
    app = FastAPI(title="Discord Gateway API Test")

    # Import and register all routers
    from api.routers.health import router as health_router
    from api.routers.categories import router as categories_router
    from api.routers.channels import router as channels_router
    from api.routers.guilds import router as guilds_router
    from api.routers.messages import router as messages_router
    from api.routers.permissions import router as permissions_router
    from api.routers.roles import router as roles_router
    from api.routers.tags import router as tags_router
    from api.routers.threads import router as threads_router
    from api.routers.users import router as users_router

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(categories_router, prefix="/api/v1")
    app.include_router(channels_router, prefix="/api/v1")
    app.include_router(guilds_router, prefix="/api/v1")
    app.include_router(messages_router, prefix="/api/v1")
    app.include_router(permissions_router, prefix="/api/v1")
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(tags_router, prefix="/api/v1")
    app.include_router(threads_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")

    yield app

    # Clean up
    app.state = {}


@pytest.fixture
def client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    """Create a test client for the discord-gateway API."""
    client = TestClient(test_app)

    yield client

    # Clean up
    client.close()


@pytest.fixture
def mock_discord_guild() -> MagicMock:
    """Factory function for mock Discord guild."""
    guild = MagicMock()
    guild.id = 123456789
    guild.name = "Test Guild"
    guild.channels = []
    guild.roles = []
    guild.members = []
    guild.get_channel = MagicMock(return_value=None)
    guild.get_role = MagicMock(return_value=None)
    guild.get_member = MagicMock(return_value=None)
    return guild


@pytest.fixture
def mock_discord_channel() -> MagicMock:
    """Factory function for mock Discord channel."""
    channel = MagicMock()
    channel.id = 987654321
    channel.name = "test-channel"
    channel.guild = MagicMock()
    channel.guild.id = 123456789
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock())
    return channel


@pytest.fixture
def mock_discord_user() -> MagicMock:
    """Factory function for mock Discord user."""
    user = MagicMock()
    user.id = 111222333444
    user.name = "testuser"
    user.discriminator = "1234"
    user.mention = "<@111222333444>"
    return user


@pytest.fixture
def mock_discord_message() -> MagicMock:
    """Factory function for mock Discord message."""
    message = MagicMock()
    message.id = 123456789
    message.content = "test message"
    message.author = MagicMock()
    message.author.id = 111222333444
    message.author.name = "testuser"
    message.channel = MagicMock()
    message.channel.id = 987654321
    message.channel.send = AsyncMock()
    message.guild = MagicMock()
    message.guild.id = 123456789
    message.created_at = MagicMock()
    message.mentions = []
    message.role_mentions = []
    message.channel_mentions = []
    message.reference = None
    return message


@pytest.fixture
def mock_discord_role() -> MagicMock:
    """Factory function for mock Discord role."""
    role = MagicMock()
    role.id = 123456789
    role.name = "test-role"
    role.colour = MagicMock()
    role.permissions = MagicMock()
    return role


@pytest.fixture
def mock_discord_member() -> MagicMock:
    """Factory function for mock Discord member."""
    member = MagicMock()
    member.id = 111222333444
    member.name = "testuser"
    member.guild = MagicMock()
    member.guild.id = 123456789
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.kick = AsyncMock()
    member.ban = AsyncMock()
    member.unban = AsyncMock()
    return member


@pytest.fixture
def mock_discord_embed() -> MagicMock:
    """Factory function for mock Discord embed."""
    embed = MagicMock()
    embed.title = "Test Embed"
    embed.description = "Test description"
    embed.colour = MagicMock()
    embed.add_field = MagicMock()
    return embed


@pytest.fixture
def mock_discord_intents() -> MagicMock:
    """Factory function for mock Discord intents."""
    intents = MagicMock()
    intents.guilds = True
    intents.messages = True
    intents.members = True
    intents.reactions = True
    intents.presences = False
    return intents


@pytest.fixture
def mock_async_discord_method() -> Generator[AsyncMock, None, None]:
    """Fixture for mocking async Discord methods."""
    mock_method = AsyncMock()

    yield mock_method

    # Clean up
    mock_method.reset_mock()


@pytest.fixture
def mock_sync_discord_method() -> Generator[MagicMock, None, None]:
    """Fixture for mocking sync Discord methods."""
    mock_method = MagicMock()

    yield mock_method

    # Clean up
    mock_method.reset_mock()


@pytest.fixture
def mock_http_response() -> MagicMock:
    """Factory function for mock HTTP response."""
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={})
    response.text = MagicMock(return_value="")
    response.headers = {}
    return response


def _make_mock_http_response() -> MagicMock:
    """Build a standalone mock HTTP response (not a fixture)."""
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={})
    response.text = MagicMock(return_value="")
    response.headers = {}
    return response


@pytest.fixture
def mock_httpx_client() -> Generator[MagicMock, None, None]:
    """Mock httpx client with common methods."""
    client = MagicMock()
    client.get = MagicMock(return_value=_make_mock_http_response())
    client.post = MagicMock(return_value=_make_mock_http_response())
    client.put = MagicMock(return_value=_make_mock_http_response())
    client.delete = MagicMock(return_value=_make_mock_http_response())
    client.request = MagicMock(return_value=_make_mock_http_response())

    yield client

    # Clean up
    client.reset_mock()


@pytest.fixture
def mock_database_session() -> Generator[MagicMock, None, None]:
    """Mock database session for testing."""
    session = MagicMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    session.query = MagicMock()

    yield session

    # Clean up
    session.reset_mock()


@pytest.fixture
def mock_fastapi_request() -> MagicMock:
    """Factory function for mock FastAPI request."""
    request = MagicMock()
    request.state = MagicMock()
    request.headers = {}
    request.cookies = {}
    request.query_params = {}
    request.url = MagicMock()
    request.url.host = "localhost"
    request.url.port = 7999
    request.url.scheme = "http"
    return request


@pytest.fixture
def mock_fastapi_response() -> MagicMock:
    """Factory function for mock FastAPI response."""
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={})
    response.text = MagicMock(return_value="")
    response.headers = {}
    return response
