"""Root-level pytest fixtures shared across all services."""
import pytest
import asyncio


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_guild():
    """Sample guild data for tests."""
    return {"id": 123456789, "name": "Test Guild"}


@pytest.fixture
def sample_player():
    """Sample player data for tests."""
    return {
        "id": 1,
        "discord_id": 987654321,
        "name": "TestPlayer",
        "credits": 1000,
    }


@pytest.fixture
def sample_ship():
    """Sample ship data for tests."""
    return {
        "id": 1,
        "name": "Test Ship",
        "hull": 100,
        "shield": 50,
        "cargo_capacity": 200,
    }
