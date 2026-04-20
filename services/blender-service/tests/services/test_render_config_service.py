"""
Unit tests for RenderConfigService and RenderConfig.

Each test uses at most 2 mocks (per project standard).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from services.render_config_service import RenderConfig, RenderConfigService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc() -> RenderConfigService:
    """Return a fresh RenderConfigService with default settings."""
    return RenderConfigService()


# ---------------------------------------------------------------------------
# RenderConfig defaults
# ---------------------------------------------------------------------------


def test_default_config_values(svc: RenderConfigService) -> None:
    """Default config values should match the documented spec."""
    cfg = svc.config
    assert cfg.max_res_x == 3840
    assert cfg.max_res_y == 2160
    assert cfg.min_res_x == 352
    assert cfg.min_res_y == 240
    assert cfg.max_samples == 128
    assert cfg.min_samples == 1
    assert cfg.default_res_x == 3840
    assert cfg.default_res_y == 2160
    assert cfg.default_samples == 128
    assert cfg.max_concurrent_renders == 2
    assert cfg.job_ttl_hours == 1


def test_env_var_override() -> None:
    """Environment variable overrides should be applied on construction."""
    env = {
        "RENDER_MAX_RES_X": "2560",
        "RENDER_MAX_RES_Y": "1440",
        "RENDER_DEFAULT_RES_X": "1280",
        "RENDER_DEFAULT_RES_Y": "720",
        "RENDER_DEFAULT_SAMPLES": "32",
        "RENDER_MAX_SAMPLES": "64",
        "RENDER_MAX_CONCURRENT": "4",
    }
    with patch.dict(os.environ, env, clear=False):
        svc = RenderConfigService()
        cfg = svc.config
    assert cfg.max_res_x == 2560
    assert cfg.max_res_y == 1440
    assert cfg.default_res_x == 1280
    assert cfg.default_res_y == 720
    assert cfg.default_samples == 32
    assert cfg.max_samples == 64
    assert cfg.max_concurrent_renders == 4


# ---------------------------------------------------------------------------
# update()
# ---------------------------------------------------------------------------


def test_update_single_field(svc: RenderConfigService) -> None:
    """Updating a single field should change only that field."""
    original_max_y = svc.config.max_res_y
    svc.update({"max_res_x": 1920})
    assert svc.config.max_res_x == 1920
    # Other fields should remain unchanged.
    assert svc.config.max_res_y == original_max_y


def test_update_multiple_fields(svc: RenderConfigService) -> None:
    """Multiple fields can be updated in a single call."""
    svc.update({"max_res_x": 1280, "default_samples": 16, "job_ttl_hours": 3})
    assert svc.config.max_res_x == 1280
    assert svc.config.default_samples == 16
    assert svc.config.job_ttl_hours == 3


def test_update_unknown_field_ignored(svc: RenderConfigService) -> None:
    """Unknown keys should be silently ignored (no exception raised)."""
    original_dict = svc.config.to_dict()
    svc.update({"nonexistent_field": 9999, "another_fake": "value"})
    # All known fields should remain unchanged.
    assert svc.config.to_dict() == original_dict


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


def test_reset_to_defaults(svc: RenderConfigService) -> None:
    """After mutating config, reset() should restore defaults."""
    svc.update({"max_res_x": 100, "default_samples": 1})
    assert svc.config.max_res_x == 100

    svc.reset()
    # Back to compiled-in defaults (no env vars set).
    assert svc.config.max_res_x == 3840
    assert svc.config.default_samples == 128


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------


def test_to_dict_complete(svc: RenderConfigService) -> None:
    """to_dict() should include all expected keys."""
    expected_keys = {
        "max_res_x",
        "max_res_y",
        "min_res_x",
        "min_res_y",
        "max_samples",
        "min_samples",
        "default_res_x",
        "default_res_y",
        "default_samples",
        "max_concurrent_renders",
        "job_ttl_hours",
    }
    result = svc.config.to_dict()
    assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# RenderConfig standalone
# ---------------------------------------------------------------------------


def test_render_config_dataclass_defaults() -> None:
    """RenderConfig instantiated without arguments should have hardcoded defaults."""
    cfg = RenderConfig()
    assert cfg.max_res_x == 3840
    assert cfg.min_samples == 1
    assert cfg.default_samples == 128
