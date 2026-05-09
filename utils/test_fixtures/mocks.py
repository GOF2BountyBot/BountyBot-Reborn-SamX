"""Common mock objects for testing."""
from unittest.mock import AsyncMock, MagicMock


def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    return session


def mock_httpx_client(status_code=200, json_data=None):
    """Create a mock httpx async client."""
    client = AsyncMock()
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.raise_for_status = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.put = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)
    return client


def mock_discord_bot():
    """Create a mock Discord bot instance."""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.guilds = []
    bot.get_guild = MagicMock(return_value=None)
    bot.get_channel = MagicMock(return_value=None)
    bot.wait_until_ready = AsyncMock()
    return bot
