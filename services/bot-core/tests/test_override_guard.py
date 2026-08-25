"""
issue #70 regression lock — per-guild override surface guard.

Asserts that both sides of the per-guild game-constant override surface stay in sync:
  a. _OVERRIDE_FIELDS in api/routers/config.py == canonical JSON (set equality + same length,
     so duplicates in the runtime tuple are also caught).
  b. Every _OVERRIDE_FIELDS entry is a mapped column on GuildConfig.
  c. Every entry is a declared field on GameConstantsOverridesMixin, and (except BOOL_FIELDS
     and DICT_FIELDS which carry structural validation instead) carries at least one numeric
     bound (ge or le) in the field's metadata.
  d. LIVE-READ: every entry appears as a quoted string in services/bot-core/src outside the
     plumbing files, OR is in LIVE_READ_EXCEPTIONS (fields resolved via f-string computed keys).
  e. REVERSE GUARD: every GuildConfig column NOT in _OVERRIDE_FIELDS must be in
     ALLOWED_NON_OVERRIDE_COLUMNS; any new column must be consciously placed in one set
     or the other.

Canonical JSON lives at tests/data/override_fields.json (same directory as this file's parent
directory's data/ folder). The gateway test suite reads the same file via repo-root path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap sys.path so imports resolve correctly.
# tests/api/__init__.py causes tests/api to shadow src/api when tests/ is on
# sys.path (inserted by tests/conftest.py).  Mirror the fix from
# tests/api/conftest.py: ensure src/ is first and purge any stale api.* entries
# so the real src/api package wins over the test directory shadow.
# ---------------------------------------------------------------------------
_SRC = Path(__file__).parent.parent / "src"
_SRC_STR = str(_SRC)

# Ensure src/ is at position 0 (wins over any tests/ shadow)
if _SRC_STR in sys.path:
    sys.path.remove(_SRC_STR)
sys.path.insert(0, _SRC_STR)

# Purge any stale api.* modules that may have been loaded from tests/api/
for _key in list(sys.modules):
    if _key == "api" or _key.startswith("api."):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_STR not in _file:
            del sys.modules[_key]

from api.routers.config import _OVERRIDE_FIELDS
from api.schemas.config_schema import GameConstantsOverridesMixin
from persist.models.guild_config import GuildConfig

# ---------------------------------------------------------------------------
# Canonical JSON location
# ---------------------------------------------------------------------------
_DATA_DIR = Path(__file__).parent / "data"
_OVERRIDE_FIELDS_JSON = _DATA_DIR / "override_fields.json"

# ---------------------------------------------------------------------------
# BOOL_FIELDS — strict bool, no numeric bounds (structural validation only).
# ---------------------------------------------------------------------------
BOOL_FIELDS: frozenset[str] = frozenset({"criminal_exclude_emp_weapons"})

# ---------------------------------------------------------------------------
# DICT_FIELDS — the 7 deprecated JSONB fields.  They carry custom @field_validators
# for structural validation; no ge/le bounds apply.
# ---------------------------------------------------------------------------
DICT_FIELDS: frozenset[str] = frozenset(
    {
        "division_max_tl",
        "bounty_division_reward_mult",
        "primary_tl_band_weights",
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
    }
)

# ---------------------------------------------------------------------------
# LIVE_READ_EXCEPTIONS — fields consumed ONLY via f-string computed keys; a
# plain quoted-string search of the source won't find them.
# Each entry is annotated with the file path and the pattern that reads it.
# (Minimal: built from what the source scan cannot find.  criminal_cloak_chance_bronze
# would pass the scan via a comment on bounty_service.py:1164, but all 16
# criminal_*_chance_* flat scalars are semantically consumed only by the f-string
# f"{chance_key}_{division.lower()}", so the full group is listed for clarity.)
# ---------------------------------------------------------------------------
# fmt: off
LIVE_READ_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # services/shop_service.py:888-893
        #   f"shop_tl_band_lo_{tier.lower()}"  /  f"shop_tl_band_hi_{tier.lower()}"
        "shop_tl_band_lo_bronze",   "shop_tl_band_hi_bronze",
        "shop_tl_band_lo_silver",   "shop_tl_band_hi_silver",
        "shop_tl_band_lo_gold",     "shop_tl_band_hi_gold",
        "shop_tl_band_lo_platinum", "shop_tl_band_hi_platinum",
        # services/bounty_service.py:1914
        #   f"division_max_tl_{division.lower()}"
        "division_max_tl_bronze", "division_max_tl_silver",
        "division_max_tl_gold",   "division_max_tl_platinum",
        # services/bounty_service.py:1926
        #   f"division_tl_center_{division.lower()}"
        "division_tl_center_bronze", "division_tl_center_silver",
        "division_tl_center_gold",   "division_tl_center_platinum",
        # services/bounty_service.py:1995
        #   f"bounty_division_reward_mult_{division.lower()}"
        "bounty_division_reward_mult_bronze", "bounty_division_reward_mult_silver",
        "bounty_division_reward_mult_gold",   "bounty_division_reward_mult_platinum",
        # services/bounty_service.py:1164–1165
        #   f"{chance_key}_{division.lower()}"  (scalar)
        #   f"{chance_key}_by_division"          (legacy JSONB fallback)
        # All 16 flat scalars + 4 JSONB dict fields resolved exclusively via f-string.
        "criminal_cloak_chance_bronze",    "criminal_cloak_chance_silver",
        "criminal_cloak_chance_gold",      "criminal_cloak_chance_platinum",
        "criminal_booster_chance_bronze",  "criminal_booster_chance_silver",
        "criminal_booster_chance_gold",    "criminal_booster_chance_platinum",
        "criminal_emergency_chance_bronze","criminal_emergency_chance_silver",
        "criminal_emergency_chance_gold",  "criminal_emergency_chance_platinum",
        "criminal_weaponmod_chance_bronze","criminal_weaponmod_chance_silver",
        "criminal_weaponmod_chance_gold",  "criminal_weaponmod_chance_platinum",
        # JSONB dict fields read via f"{chance_key}_by_division" (bounty_service.py:1165)
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
    }
)
# fmt: on

# ---------------------------------------------------------------------------
# ALLOWED_NON_OVERRIDE_COLUMNS — every GuildConfig column NOT in _OVERRIDE_FIELDS,
# with a reason for each group.  Any new column must land here (with a reason)
# OR in _OVERRIDE_FIELDS (to expose it as a per-guild tunable).
# ---------------------------------------------------------------------------
ALLOWED_NON_OVERRIDE_COLUMNS: frozenset[str] = frozenset(
    {
        # ------- identity / housekeeping -------
        "id",  # surrogate primary key
        "guild_id",  # unique Discord guild snowflake
        "created_at",  # row-creation timestamp
        "updated_at",  # last-modified timestamp
        # ------- admin / role config (set via /admin_setup or dedicated endpoints) -------
        "admin_role_id",
        # ------- Discord channel IDs (provisioned by /admin_setup) -------
        "category_id",
        "shop_channel_id",
        "bronze_bounty_channel_id",
        "silver_bounty_channel_id",
        "gold_bounty_channel_id",
        "platinum_bounty_channel_id",
        "hunting_channel_id",
        "discussion_channel_id",
        "image_channel_id",
        # ------- Discord role IDs (provisioned by /admin_setup) -------
        "bounty_hunter_role_id",
        "bronze_role_id",
        "silver_role_id",
        "gold_role_id",
        "platinum_role_id",
        "shop_announcements_role_id",
        # ------- shop inventory size / quantity ranges (UpdateShopConfigRequest) -------
        "ship_count_range",
        "weapon_count_range",
        "secondary_weapon_count_range",
        "module_count_range",
        "turret_count_range",
        "ship_quantity_range",
        "weapon_quantity_range",
        "secondary_weapon_quantity_range",
        "module_quantity_range",
        "turret_quantity_range",
        "tech_level_probabilities",
        # ------- economy (UpdateConfigRequest / dedicated endpoints) -------
        "sale_price_factor",
        "starting_credits",
        # ------- tier progression (UpdateXPThresholdsRequest) -------
        "xp_thresholds",
        # ------- bounty spawn configuration (UpdateBountyConfigRequest) -------
        "bounty_max_per_tier",
        "bounty_expiry_minutes",
        "bounty_spawn_interval_minutes",
        "next_spawn_check_at",
        # ------- combat engine columns (ketar pair deferred to A2; all others now in _OVERRIDE_FIELDS) -------
        # The ketar_i/ii columns exist in GuildConfig but are NOT yet in _OVERRIDE_FIELDS — deferred to unit A2.
        # All other Phase-1 combat engine constants moved to _OVERRIDE_FIELDS in rev 0032 (unit A1).
        "ketar_i_repair_pct_per_sec",
        "ketar_ii_repair_pct_per_sec",
    }
)


class TestOverrideGuard:
    """Regression lock for the per-guild override surface (issue #70)."""

    # (a) JSON equality ----------------------------------------------------

    def test_json_set_and_length_match(self):
        """_OVERRIDE_FIELDS == canonical JSON: same set and same length (no duplicates)."""
        payload = json.loads(_OVERRIDE_FIELDS_JSON.read_text(encoding="utf-8"))
        json_fields: list[str] = payload["fields"]

        assert len(json_fields) == len(_OVERRIDE_FIELDS), (
            f"Length mismatch: JSON has {len(json_fields)}, _OVERRIDE_FIELDS has {len(_OVERRIDE_FIELDS)}. "
            "Update override_fields.json to match config.py _OVERRIDE_FIELDS."
        )
        assert set(json_fields) == set(_OVERRIDE_FIELDS), (
            f"Set mismatch: in JSON only={set(json_fields) - set(_OVERRIDE_FIELDS)!r}, "
            f"in runtime only={set(_OVERRIDE_FIELDS) - set(json_fields)!r}"
        )

    # (b) GuildConfig column coverage -------------------------------------

    def test_all_fields_are_guild_config_columns(self):
        """Every _OVERRIDE_FIELDS entry is a mapped column on GuildConfig."""
        col_names = {c.name for c in GuildConfig.__table__.columns}
        missing = [f for f in _OVERRIDE_FIELDS if f not in col_names]
        assert not missing, f"_OVERRIDE_FIELDS entries missing from GuildConfig.__table__.columns: {missing}"

    # (c) GameConstantsOverridesMixin field + bounds ----------------------

    def test_all_fields_declared_on_mixin(self):
        """Every _OVERRIDE_FIELDS entry is declared on GameConstantsOverridesMixin."""
        mixin_fields = set(GameConstantsOverridesMixin.model_fields)
        missing = [f for f in _OVERRIDE_FIELDS if f not in mixin_fields]
        assert not missing, f"_OVERRIDE_FIELDS entries missing from GameConstantsOverridesMixin.model_fields: {missing}"

    def test_numeric_fields_have_ge_or_le_bounds(self):
        """Non-bool, non-dict override fields must carry at least one ge or le constraint."""
        no_bounds: list[str] = []
        for name in _OVERRIDE_FIELDS:
            if name in BOOL_FIELDS or name in DICT_FIELDS:
                continue  # structural validation only for these groups
            fi = GameConstantsOverridesMixin.model_fields[name]
            has_bound = any(
                getattr(m, "ge", None) is not None or getattr(m, "le", None) is not None for m in fi.metadata
            )
            if not has_bound:
                no_bounds.append(name)
        assert not no_bounds, (
            f"Numeric override fields missing ge/le bounds on GameConstantsOverridesMixin: {no_bounds}. "
            "Add Field(None, ge=..., le=...) to each."
        )

    # (d) LIVE-READ source scan -------------------------------------------

    def test_live_read_coverage(self):
        """Every _OVERRIDE_FIELDS entry appears as a quoted string in non-plumbing source,
        or is in LIVE_READ_EXCEPTIONS (consumed only via f-string computed keys)."""
        src_root = Path(__file__).parent.parent / "src"

        # Plumbing files: the definition sites themselves, not consumption sites.
        _plumbing_rel = {
            Path("api/routers/config.py"),
            Path("api/schemas/config_schema.py"),
            Path("persist/models/guild_config.py"),
            Path("persist/repositories/config_repository.py"),
        }
        _plumbing_prefix = Path("persist/database/revisions")

        combined_source: list[str] = []
        for py_file in src_root.rglob("*.py"):
            rel = py_file.relative_to(src_root)
            if rel in _plumbing_rel:
                continue
            # Skip all migration revision files
            if rel.parts[: len(_plumbing_prefix.parts)] == _plumbing_prefix.parts:
                continue
            combined_source.append(py_file.read_text(encoding="utf-8"))
        combined = "\n".join(combined_source)

        not_found: list[str] = []
        for field in _OVERRIDE_FIELDS:
            if field in LIVE_READ_EXCEPTIONS:
                continue
            if f'"{field}"' not in combined and f"'{field}'" not in combined:
                not_found.append(field)

        assert not not_found, (
            f"Override fields not found as literal quoted strings in non-plumbing "
            f"services/bot-core/src. If they are read via f-string computed keys, "
            f"add them to LIVE_READ_EXCEPTIONS with a justification comment. "
            f"Missing: {not_found}"
        )

    # (e) Reverse guard ---------------------------------------------------

    def test_reverse_guard_all_columns_accounted_for(self):
        """Every GuildConfig column is either in _OVERRIDE_FIELDS or ALLOWED_NON_OVERRIDE_COLUMNS.

        Any new column that falls into neither set causes this test to fail, forcing the
        author to decide: expose it (add to _OVERRIDE_FIELDS) or allowlist it (add to
        ALLOWED_NON_OVERRIDE_COLUMNS with a reason).
        """
        all_columns = {c.name for c in GuildConfig.__table__.columns}
        override_set = set(_OVERRIDE_FIELDS)

        unknown = all_columns - override_set - ALLOWED_NON_OVERRIDE_COLUMNS
        assert not unknown, (
            f"GuildConfig column(s) in neither _OVERRIDE_FIELDS nor "
            f"ALLOWED_NON_OVERRIDE_COLUMNS: {unknown!r}. "
            "Add each column to _OVERRIDE_FIELDS (to expose it as a per-guild tunable) "
            "or to ALLOWED_NON_OVERRIDE_COLUMNS in test_override_guard.py (with a reason)."
        )

        # Sanity: union of both sets == all columns (catches stale entries in ALLOWED set)
        coverage = override_set | ALLOWED_NON_OVERRIDE_COLUMNS
        stale = coverage - all_columns
        assert not stale, (
            f"ALLOWED_NON_OVERRIDE_COLUMNS contains column name(s) that no longer exist "
            f"in GuildConfig: {stale!r}. Remove them."
        )
