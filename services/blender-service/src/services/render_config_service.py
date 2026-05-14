"""Centralized render configuration with runtime updates.

Settings are stored in-memory with env var defaults.  Changes persist
only for the lifetime of the process (no database).

B.91: the 11 scalar settings are organised into semantic groups
(`PARAM_GROUPS`) and every mutation is checked against the config
invariants (`min <= default <= max`, plus positivity) before it is
committed — see ``RenderConfig.validate`` and ``RenderConfigService.update``.
"""

import os
from dataclasses import dataclass, fields, replace
from typing import ClassVar

from shared import bblogger

flogger = bblogger.get_logger("blender-render-config-service")


class RenderConfigError(ValueError):
    """Raised when a render-config update would violate a semantic invariant.

    Subclasses ``ValueError`` so callers that only care that "the update was
    bad" still catch it, while the config router can catch it specifically
    and map it to HTTP 422.
    """


@dataclass
class RenderConfig:
    """Current render configuration. All fields are mutable at runtime."""

    # Resolution limits — tuned for a 4-core / 8GB CPU-only VPS doing CYCLES renders.
    # Override via env vars (RENDER_MAX_RES_X, etc.) on beefier hosts.
    max_res_x: int = 1920
    max_res_y: int = 1080
    min_res_x: int = 352
    min_res_y: int = 240

    # Sample limits
    max_samples: int = 64
    min_samples: int = 1

    # Defaults (used when user doesn't specify) — 720p / 32 samples is a reasonable
    # cost/quality tradeoff for CPU CYCLES on a small VPS.
    default_res_x: int = 1280
    default_res_y: int = 720
    default_samples: int = 32

    # Job queue — CPU rendering eats all 4 cores; running two concurrently
    # halves throughput and risks OOM on 8GB RAM. Bump on hosts with more headroom.
    max_concurrent_renders: int = 1
    job_ttl_hours: int = 1

    # B.91: semantic grouping of the flat scalar settings. Drives the grouped
    # view in /render_config and keeps "what belongs with what" in one place.
    PARAM_GROUPS: ClassVar[dict[str, tuple[str, ...]]] = {
        "resolution_limits": ("min_res_x", "max_res_x", "min_res_y", "max_res_y"),
        "sample_limits": ("min_samples", "max_samples"),
        "defaults": ("default_res_x", "default_res_y", "default_samples"),
        "concurrency": ("max_concurrent_renders", "job_ttl_hours"),
    }

    # B.91: human-readable description of every invariant enforced by validate().
    # Surfaced to admins so they can see the rules before they trip one.
    INVARIANTS: ClassVar[tuple[str, ...]] = (
        "min_res_x <= default_res_x <= max_res_x",
        "min_res_y <= default_res_y <= max_res_y",
        "min_samples <= default_samples <= max_samples",
        "all resolution and sample bounds must be positive",
        "max_concurrent_renders >= 1 and job_ttl_hours >= 1",
    )

    def to_dict(self) -> dict:
        """Serialize all settings as a flat dict."""
        return {
            "max_res_x": self.max_res_x,
            "max_res_y": self.max_res_y,
            "min_res_x": self.min_res_x,
            "min_res_y": self.min_res_y,
            "max_samples": self.max_samples,
            "min_samples": self.min_samples,
            "default_res_x": self.default_res_x,
            "default_res_y": self.default_res_y,
            "default_samples": self.default_samples,
            "max_concurrent_renders": self.max_concurrent_renders,
            "job_ttl_hours": self.job_ttl_hours,
        }

    def to_grouped_dict(self) -> dict[str, dict[str, int]]:
        """Serialize all settings grouped by semantic category (B.91).

        :return: ``{group_name: {field_name: value}}`` covering every field
            in ``to_dict()``.
        :rtype: dict[str, dict[str, int]]
        """
        flat = self.to_dict()
        return {group: {field: flat[field] for field in fields} for group, fields in self.PARAM_GROUPS.items()}

    def validate(self) -> None:
        """Check the semantic config invariants (B.91).

        :raises RenderConfigError: If any invariant is violated. The message
            lists every violation found, not just the first.
        """
        errors: list[str] = []

        if not (self.min_res_x <= self.default_res_x <= self.max_res_x):
            errors.append(
                f"min_res_x ({self.min_res_x}) <= default_res_x ({self.default_res_x}) "
                f"<= max_res_x ({self.max_res_x}) is violated"
            )
        if not (self.min_res_y <= self.default_res_y <= self.max_res_y):
            errors.append(
                f"min_res_y ({self.min_res_y}) <= default_res_y ({self.default_res_y}) "
                f"<= max_res_y ({self.max_res_y}) is violated"
            )
        if not (self.min_samples <= self.default_samples <= self.max_samples):
            errors.append(
                f"min_samples ({self.min_samples}) <= default_samples ({self.default_samples}) "
                f"<= max_samples ({self.max_samples}) is violated"
            )

        positive_fields = (
            "max_res_x",
            "max_res_y",
            "min_res_x",
            "min_res_y",
            "max_samples",
            "min_samples",
            "default_res_x",
            "default_res_y",
            "default_samples",
        )
        for name in positive_fields:
            value = getattr(self, name)
            if value <= 0:
                errors.append(f"{name} must be positive (got {value})")

        if self.max_concurrent_renders < 1:
            errors.append(f"max_concurrent_renders must be >= 1 (got {self.max_concurrent_renders})")
        if self.job_ttl_hours < 1:
            errors.append(f"job_ttl_hours must be >= 1 (got {self.job_ttl_hours})")

        if errors:
            raise RenderConfigError("; ".join(errors))


class RenderConfigService:
    """Manages render configuration with env var defaults."""

    def __init__(self) -> None:
        self._config = RenderConfig(
            max_res_x=int(os.getenv("RENDER_MAX_RES_X", "1920")),
            max_res_y=int(os.getenv("RENDER_MAX_RES_Y", "1080")),
            default_res_x=int(os.getenv("RENDER_DEFAULT_RES_X", "1280")),
            default_res_y=int(os.getenv("RENDER_DEFAULT_RES_Y", "720")),
            default_samples=int(os.getenv("RENDER_DEFAULT_SAMPLES", "32")),
            max_samples=int(os.getenv("RENDER_MAX_SAMPLES", "64")),
            max_concurrent_renders=int(os.getenv("RENDER_MAX_CONCURRENT", "1")),
        )
        flogger.info(f"RenderConfig initialized: {self._config.to_dict()}")

    @property
    def config(self) -> RenderConfig:
        """Return the current RenderConfig."""
        flogger.debug("Config read: retrieving current RenderConfig")
        return self._config

    def update(self, updates: dict) -> RenderConfig:
        """Update config fields. Only known fields are applied.

        B.91: the prospective config is validated against the semantic
        invariants *before* anything is committed — if validation fails the
        live config is left untouched and ``RenderConfigError`` is raised.

        :param dict updates: Field name -> new value. Unknown keys are ignored.
        :return: The (possibly updated) live ``RenderConfig``.
        :rtype: RenderConfig
        :raises RenderConfigError: If the resulting config would be invalid.
        """
        flogger.debug(f"Attempting to update config with {len(updates)} field(s)")
        # dataclasses.fields() excludes ClassVar attrs (PARAM_GROUPS / INVARIANTS),
        # so only real, settable config fields are accepted.
        known_fields = {f.name for f in fields(RenderConfig)}
        applied: dict[str, object] = {}
        ignored_keys: list[str] = []
        for key, value in updates.items():
            if key in known_fields:
                applied[key] = value
            else:
                flogger.debug(f"Ignoring unknown config key: {key}")
                ignored_keys.append(key)

        if ignored_keys:
            flogger.warning(f"Config update: {len(ignored_keys)} unknown key(s) ignored: {ignored_keys}")

        if not applied:
            flogger.warning("Config update: no valid fields provided")
            return self._config

        # B.91: validate the candidate config before mutating the live one.
        candidate = replace(self._config, **applied)
        try:
            candidate.validate()
        except RenderConfigError as exc:
            flogger.warning(f"Config update rejected — invariant violation: {exc}")
            raise

        for key, value in applied.items():
            old_value = getattr(self._config, key)
            setattr(self._config, key, value)
            flogger.info(f"Config updated: {key} = {value} (was {old_value})")
        flogger.info(f"Config update complete: {len(applied)} field(s) applied: {sorted(applied)}")
        return self._config

    def reset(self) -> RenderConfig:
        """Reset to defaults (re-reads env vars)."""
        flogger.info("Resetting config to environment variable defaults")
        old_config = self._config.to_dict()
        self.__init__()
        new_config = self._config.to_dict()
        changes = {k: (old_config[k], new_config[k]) for k in old_config if old_config[k] != new_config[k]}
        if changes:
            flogger.info(f"Config reset complete: {len(changes)} field(s) changed: {changes}")
        else:
            flogger.debug("Config reset complete: no changes from environment variables")
        return self._config
