"""Fake data generators for testing."""
from faker import Faker

fake = Faker()


def make_player(**overrides):
    """Generate a fake player dict."""
    data = {
        "id": fake.random_int(min=1, max=99999),
        "discord_id": fake.random_int(min=100000000000000000, max=999999999999999999),
        "name": fake.user_name(),
        "credits": fake.random_int(min=0, max=100000),
    }
    data.update(overrides)
    return data


def make_guild(**overrides):
    """Generate a fake guild dict."""
    data = {
        "id": fake.random_int(min=100000000000000000, max=999999999999999999),
        "name": fake.company(),
    }
    data.update(overrides)
    return data


def make_ship(**overrides):
    """Generate a fake ship dict."""
    data = {
        "id": fake.random_int(min=1, max=999),
        "name": fake.word().capitalize() + " " + fake.word().capitalize(),
        "hull": fake.random_int(min=50, max=500),
        "shield": fake.random_int(min=0, max=300),
        "cargo_capacity": fake.random_int(min=50, max=1000),
    }
    data.update(overrides)
    return data
