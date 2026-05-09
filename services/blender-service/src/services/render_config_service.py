"""Centralized render configuration with runtime updates.

Settings are stored in-memory with env var defaults.  Changes persist
only for the lifetime of the process (no database).
"""

import os
from dataclasses import dataclass

from shared import bblogger

flogger = bblogger.get_logger("blender-render-config-service")


@dataclass
class RenderConfig:
    """Current render configuration. All fields are mutable at runtime."""

    # Resolution limits
    max_res_x: int = 3840
    max_res_y: int = 2160
    min_res_x: int = 352
    min_res_y: int = 240

    # Sample limits
    max_samples: int = 128
    min_samples: int = 1

    # Defaults (used when user doesn't specify)
    default_res_x: int = 3840
    default_res_y: int = 2160
    default_samples: int = 128

    # Job queue
    max_concurrent_renders: int = 2
    job_ttl_hours: int = 1

    def to_dict(self) -> dict:
        """Serialize all settings."""
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


class RenderConfigService:
    """Manages render configuration with env var defaults."""

    def __init__(self) -> None:
        self._config = RenderConfig(
            max_res_x=int(os.getenv("RENDER_MAX_RES_X", "3840")),
            max_res_y=int(os.getenv("RENDER_MAX_RES_Y", "2160")),
            default_res_x=int(os.getenv("RENDER_DEFAULT_RES_X", "3840")),
            default_res_y=int(os.getenv("RENDER_DEFAULT_RES_Y", "2160")),
            default_samples=int(os.getenv("RENDER_DEFAULT_SAMPLES", "128")),
            max_samples=int(os.getenv("RENDER_MAX_SAMPLES", "128")),
            max_concurrent_renders=int(os.getenv("RENDER_MAX_CONCURRENT", "2")),
        )
        flogger.info(f"RenderConfig initialized: {self._config.to_dict()}")

    @property
    def config(self) -> RenderConfig:
        """Return the current RenderConfig."""
        flogger.debug("Config read: retrieving current RenderConfig")
        return self._config

    def update(self, updates: dict) -> RenderConfig:
        """Update config fields. Only known fields are applied."""
        flogger.debug(f"Attempting to update config with {len(updates)} field(s)")
        applied_updates = []
        ignored_keys = []
        for key, value in updates.items():
            if hasattr(self._config, key):
                old_value = getattr(self._config, key)
                setattr(self._config, key, value)
                flogger.info(f"Config updated: {key} = {value} (was {old_value})")
                applied_updates.append(key)
            else:
                flogger.debug(f"Ignoring unknown config key: {key}")
                ignored_keys.append(key)
        if ignored_keys:
            flogger.warning(f"Config update: {len(ignored_keys)} unknown key(s) ignored: {ignored_keys}")
        if applied_updates:
            flogger.info(f"Config update complete: {len(applied_updates)} field(s) applied: {applied_updates}")
        else:
            flogger.warning("Config update: no valid fields provided")
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
