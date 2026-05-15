"""
Unit tests for RenderConfigService and RenderConfig.

Each test uses at most 2 mocks (per project standard).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from services.render_config_service import RenderConfig, RenderConfigError, RenderConfigService

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
    """Default config values should match the documented spec.

    Defaults are tuned for a small CPU-only VPS (4-core / 8GB):
    720p default render, 1080p ceiling, 32/64 samples, 1 concurrent render.
    """
    cfg = svc.config
    assert cfg.max_res_x == 1920
    assert cfg.max_res_y == 1080
    assert cfg.min_res_x == 352
    assert cfg.min_res_y == 240
    assert cfg.max_samples == 64
    assert cfg.min_samples == 1
    assert cfg.default_res_x == 1280
    assert cfg.default_res_y == 720
    assert cfg.default_samples == 32
    assert cfg.max_concurrent_renders == 1
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
    # Use a within-invariant mutation: max_res_x=1280 keeps min<=default<=max valid.
    svc.update({"max_res_x": 1280, "default_samples": 1})
    assert svc.config.max_res_x == 1280

    svc.reset()
    # Back to compiled-in defaults (no env vars set).
    assert svc.config.max_res_x == 1920
    assert svc.config.default_samples == 32


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
    assert cfg.max_res_x == 1920
    assert cfg.min_samples == 1
    assert cfg.default_samples == 32


# ---------------------------------------------------------------------------
# B.91 — PARAM_GROUPS / to_grouped_dict()
# ---------------------------------------------------------------------------


def test_param_groups_cover_every_field_exactly_once() -> None:
    """Every RenderConfig data field belongs to exactly one PARAM_GROUP."""
    from dataclasses import fields as dataclass_fields

    grouped_fields = [f for fields in RenderConfig.PARAM_GROUPS.values() for f in fields]
    assert set(grouped_fields) == {f.name for f in dataclass_fields(RenderConfig)}
    assert len(grouped_fields) == len(set(grouped_fields)), "a field appears in more than one group"


def test_to_grouped_dict_matches_to_dict(svc: RenderConfigService) -> None:
    """to_grouped_dict() carries the same values as to_dict(), just grouped."""
    grouped = svc.config.to_grouped_dict()
    assert set(grouped.keys()) == set(RenderConfig.PARAM_GROUPS.keys())
    flat_from_groups = {k: v for group in grouped.values() for k, v in group.items()}
    assert flat_from_groups == svc.config.to_dict()


# ---------------------------------------------------------------------------
# B.91 — validate() invariants
# ---------------------------------------------------------------------------


def test_validate_accepts_default_config() -> None:
    """The compiled-in default config satisfies all invariants."""
    RenderConfig().validate()  # must not raise


def test_validate_rejects_default_res_above_max() -> None:
    """default_res_x above max_res_x violates the resolution invariant."""
    cfg = RenderConfig(default_res_x=4000, max_res_x=1920)
    with pytest.raises(RenderConfigError, match="default_res_x"):
        cfg.validate()


def test_validate_rejects_min_above_max() -> None:
    """min_res_x above max_res_x breaks the min<=default<=max chain."""
    cfg = RenderConfig(min_res_x=2000, max_res_x=1920)
    with pytest.raises(RenderConfigError):
        cfg.validate()


def test_validate_rejects_default_samples_above_max() -> None:
    """default_samples above max_samples violates the sample invariant."""
    cfg = RenderConfig(default_samples=128, max_samples=64)
    with pytest.raises(RenderConfigError, match="default_samples"):
        cfg.validate()


def test_validate_rejects_nonpositive_bound() -> None:
    """A zero or negative resolution bound is rejected."""
    cfg = RenderConfig(min_res_x=0)
    with pytest.raises(RenderConfigError, match="min_res_x must be positive"):
        cfg.validate()


def test_validate_reports_every_violation() -> None:
    """validate() lists all violations, not just the first one found."""
    cfg = RenderConfig(default_res_x=9999, default_samples=9999)
    with pytest.raises(RenderConfigError) as exc_info:
        cfg.validate()
    assert "default_res_x" in str(exc_info.value)
    assert "default_samples" in str(exc_info.value)


def test_render_config_error_is_value_error() -> None:
    """RenderConfigError subclasses ValueError so broad ValueError handlers still catch it."""
    assert issubclass(RenderConfigError, ValueError)


# ---------------------------------------------------------------------------
# B.91 — update() validates the candidate config before committing
# ---------------------------------------------------------------------------


def test_update_rejects_invariant_violating_change(svc: RenderConfigService) -> None:
    """update() raises RenderConfigError when the resulting config would be invalid."""
    # max_res_x=100 falls below default_res_x=1280 — invariant violation.
    with pytest.raises(RenderConfigError):
        svc.update({"max_res_x": 100})


def test_update_invalid_change_leaves_live_config_untouched(svc: RenderConfigService) -> None:
    """A rejected update must not mutate the live config (atomic update)."""
    before = svc.config.to_dict()
    with pytest.raises(RenderConfigError):
        svc.update({"max_res_x": 100, "max_res_y": 50})
    assert svc.config.to_dict() == before


def test_update_valid_multi_field_change_applies(svc: RenderConfigService) -> None:
    """A multi-field update that respects every invariant is committed."""
    svc.update({"max_res_x": 2560, "max_res_y": 1440, "default_res_x": 2000})
    assert svc.config.max_res_x == 2560
    assert svc.config.max_res_y == 1440
    assert svc.config.default_res_x == 2000
