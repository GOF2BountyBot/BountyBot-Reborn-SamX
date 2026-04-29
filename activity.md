# Package A Surgical Bundle Activity Log

## Attempt 1 [2026-04-29 UTC]

**Task**: Implement Package A — bot-core surgical bundle (B.17, B.30, B.31a, B.23) per TASK specification  
**Iteration**: 1  
**Status**: completed  

### Work Completed

- **B.17**: Added `old_xp = old_player.xp` capture before `update_player_xp()` mutation in `admin.py`; changed `"old_xp": old_player.xp` to `"old_xp": old_xp` in response dict. Fixed `test_update_player_xp_happy_path` and `test_set_xp_above_threshold_does_not_auto_promote_tier` to use single shared-mock pattern that simulates SQLAlchemy identity-map mutation.
- **B.30**: Added `ConfigDict` import and `model_config = ConfigDict(extra="forbid")` to `UpdateJob` in `scheduler_schema.py`. Added `test_update_job_rejects_unknown_fields` test asserting 422 on wrong-field body.
- **B.31a**: Added `cascade="all, delete-orphan"` to `GuildConfig.shops` relationship in `guild_config.py`. Added `test_reset_to_defaults_with_shops_does_not_raise` integration test.
- **B.23a**: Created `utils/scheduler_holder.py` singleton. Updated `main.py` to call `set_scheduler(scheduler)` after `scheduler.start()`. Updated `_schedule_expiry_job()` to try direct APScheduler Python API first, fall back to HTTP. Added `test_expiry_uses_direct_scheduler_when_holder_has_scheduler` and `test_expiry_falls_back_to_http_when_holder_returns_none` tests.
- **B.23b**: Extended `run_stale_state_recovery_sweep()` to collect stale bounty IDs before bulk UPDATE, then call `_delete_bounty_announcement` for each after commit. Updated existing sweep tests to account for new SELECT execute call. Added `TestSweepAnnouncementCleanup` class with 3 tests and `TestSchedulerHolder` with 2 tests.

### Spec-to-Test Traceability

| Acceptance Criterion | Test File(s) | Status |
|---|---|---|
| B.17: old_xp returns pre-mutation value | `tests/api/test_admin_router.py::TestUpdatePlayerXP::test_update_player_xp_happy_path` | COVERED |
| B.17: identity-map simulation | `tests/api/test_admin_router.py::TestUpdatePlayerXP::test_set_xp_above_threshold_does_not_auto_promote_tier` | COVERED |
| B.30: 422 on wrong field name | `tests/api/test_scheduler_router.py::TestUpdateJob::test_update_job_rejects_unknown_fields` | COVERED |
| B.31a: reset with shops succeeds | `tests/integration/test_config_repository.py::test_reset_to_defaults_with_shops_does_not_raise` | COVERED |
| B.23a: direct scheduler API used | `tests/test_bounty_spawn_executor.py::test_expiry_uses_direct_scheduler_when_holder_has_scheduler` | COVERED |
| B.23a: HTTP fallback when no scheduler | `tests/test_bounty_spawn_executor.py::test_expiry_falls_back_to_http_when_holder_returns_none` | COVERED |
| B.23b: sweep deletes announcements | `tests/test_stale_state_recovery.py::TestSweepAnnouncementCleanup::test_sweep_calls_delete_announcement_for_each_stale_bounty` | COVERED |
| B.23b: no cleanup on no stale bounties | `tests/test_stale_state_recovery.py::TestSweepAnnouncementCleanup::test_sweep_no_announcements_when_no_stale_bounties` | COVERED |
| B.23b: announcement failure non-fatal | `tests/test_stale_state_recovery.py::TestSweepAnnouncementCleanup::test_sweep_announcement_failure_is_non_fatal` | COVERED |
| B.23a: scheduler_holder singleton | `tests/test_stale_state_recovery.py::TestSchedulerHolder::test_set_and_get_scheduler` | COVERED |

### Coverage Summary

- All 2951 tests pass (1 pre-existing skip)
- No new failures introduced
- Ruff format clean; ruff check has 5 pre-existing errors not in scope

---

# B.4 Defect Recon Activity Log

## Attempt 1 [2026-04-28 13:45 UTC]

**Task**: Read-only reconnaissance on defect **B.4** in `/proj/DEFECTS.md` — Redo of previous cycle-12 investigation which dismissed the issue as "cosmetic, no-action-needed."

**Iteration**: 1  
**Status**: completed  

---

## Scope

- **Research question**: Is the swap-confirmation affordance in `/equip` truly adequate, or was the previous dismissal premature?
- **Constraints**: Read-only investigation; no code changes; may only modify `/proj/DEFECTS.md`
- **Themes**: 
  1. User-facing UX surface of `/equip` swap flow
  2. Comparison with parallel swap patterns (module/turret)
  3. Discord UI behavior and placeholder discoverability
  4. Test coverage gaps

---

## Investigation Findings

### Complete User-Facing Surface Traced

1. **Initial response (slot_full case)**:
   - Orange embed with title: **"🔄 Slot Full — Choose an item to swap"**
   - Clear description: **"All {equipment_type} slots are full. Select an item below to replace with {new_item}."**
   - This embed text provides EXCELLENT primary affordance
   - File: `inventoryCog.py` lines 747–754

2. **Select dropdown component**:
   - Placeholder: **"Choose an item to swap out…"** (only visible after click)
   - Options: Plain item names only, **NO descriptions**
   - Cancel button: Visible, secondary style, always present
   - File: `inventoryCog.py` lines 69–88

3. **Post-selection behavior**:
   - Two sequential API calls (unequip + equip)
   - Success embed shown with green color
   - **No confirmation step** — selecting an item immediately executes

4. **Module swap pattern (for comparison)**:
   - Uses buttons (Swap/Cancel) instead of dropdown
   - More explicit UI pattern
   - File: `inventoryCog.py` lines 765–788

5. **Unequip flow**:
   - Direct single API call
   - No confirmation UI needed
   - File: `inventoryCog.py` lines 810–862

### Discord UI Behavior

**Critical finding**: Select placeholders are light gray italic text inside the dropdown button and are **only visible AFTER the user clicks**. This means:
- Placeholder is NOT discoverable without interaction
- Embed text is the true primary affordance
- Users may not realize the dropdown is the selection interface until they click it

### Code Paths Verified

| Component | File | Lines | Finding |
|---|---|---|---|
| `/equip` handler | `inventoryCog.py` | 665–808 | Calls equip-check; branches on status |
| Slot_full branch | `inventoryCog.py` | 742–763 | Creates embed + WeaponSwapView |
| WeaponSwapView | `inventoryCog.py` | 41–89 | Select with placeholder; Cancel button |
| Option construction | `inventoryCog.py` | 69–72 | `SelectOption(label=item["name"], value=item["name"])` |
| Select callback | `inventoryCog.py` | 90–136 | API calls + confirmation |

### Test Coverage Analysis

Tests validate:
- ✅ WeaponSwapView is instantiated
- ✅ View is sent alongside embed

Tests do **NOT** validate:
- ❌ Embed title/description content
- ❌ Placeholder string
- ❌ Option descriptions
- File: `test_inventoryCog.py` lines 1131–1159

### Comparison with Related Patterns

| Pattern | UI | Affordance | Issue |
|---|---|---|---|
| WeaponSwapView | Dropdown | Medium (placeholder hidden) | Options lack descriptions |
| UniqueModuleSwapView | Buttons | High (explicit) | None found |
| Unequip | Direct | High (immediate) | None found |

---

## Root Cause Assessment

**Previous researcher's conclusion was incomplete**:

- ✅ Correctly identified placeholder exists
- ✅ Correctly identified affordance is present
- ❌ Only inspected the placeholder string, not full UX chain
- ❌ Did not analyze Discord UI placeholder visibility behavior
- ❌ Did not compare with more explicit module-swap pattern
- ❌ Did not check for option descriptions

**The affordance gap IS real**:

1. **Primary affordance (embed text)**: ✅ EXCELLENT — clearly explains "choose item to swap"
2. **Secondary affordance (dropdown UI)**: ⚠️ THIN — placeholder hidden, options lack context, immediate action on select

**Why user confusion is valid**:

- Dropdown button looks like a button (not obviously clickable as select)
- Placeholder only visible after click (requires discovery)
- Options show only item names (no context on which slot each occupies)
- Action is immediate (no confirmation step like module swap has)

---

## Severity Reassessment

**🔵 Low — CONFIRMED**

**Reasoning**:
- Users CAN complete the action (not blocking)
- Embed text provides clear instruction
- Cancel button provides abort affordance
- Only discoverability gap, not functionality gap
- Thin affordance but sufficient

---

## Recommended Actions

**Option A (Recommended)**: Add descriptions to SelectOption objects

```python
# Current:
discord.SelectOption(label=item["name"], value=item["name"])

# Improved:
discord.SelectOption(
    label=item["name"],
    value=item["name"],
    description="Swap this item out"
)
```

**Benefits**:
- Minimal code change (1 line)
- Descriptions visible in Discord dropdown UI
- Significantly improves clarity

**Option B**: Update placeholder to more explicit text

```python
placeholder="Select item to replace ↓"
```

**Option C**: Refactor to button pattern like module swap

```python
# Create "Swap [ItemName]" buttons for each item
# More explicit but space-constrained
```

---

## Conclusion

The original user's complaint about unclear affordance was **justified**. The previous researcher's dismissal as "cosmetic" was **technically accurate** but **insufficient analysis**. 

**Key insight**: The affordance is present (embed + placeholder + cancel) but thin (placeholder hidden, no descriptions). The UX works, but could be improved with minimal effort.

**No blocking issues found**. Severity remains 🔵 Low. Recommended action is Option A (add descriptions to options).

---

## Handoff Status

This is a **read-only investigation** with no code changes. B.4 entry in `/proj/DEFECTS.md` has been comprehensively updated with findings.

**No handoff required** — investigation is complete and documented.

---

**Completed**: 2026-04-28 13:50 UTC
**Investigator**: Researcher (read-only mode)
**Evidence collected**: 7 empirical data points, 3 code comparisons, 1 test coverage analysis
**Deliverable**: Updated B.4 entry in DEFECTS.md with comprehensive root cause analysis and concrete recommendations

---

## Attempt N [2026-04-29 UTC] — Package C Cross-Service Surgical Bundle (B.32/B.24/A.31)

**Task**: Implement Package C — 3 cross-service defects (A.31, B.32, B.24)
**Iteration**: 1
**Status**: in_progress

### Work Completed

- **A.31** (`about.py:102-109`): Added `tech_level` and `manufacturer` fields to `list_objects_for_category()` preload response using `getattr(obj, field, None)`. Fixes `/list_category tech_level:N` and `manufacturer:` filters which received `None` for every object due to missing fields.
- **B.32 cog** (`adminCog.py:955-977`): Added guard in `render_config` `action == "set"` branch: if `self._render_settings` is non-empty and `setting not in self._render_settings`, sends ephemeral error embed with list of valid settings and returns WITHOUT calling the API.
- **B.32 service** (`blender-service/routers/config.py:26-43`): Added import of `HTTPException` and `RenderConfig`; added pre-call check in `update_render_config` — raises HTTP 422 when `updates` dict contains no recognized `RenderConfig` fields.
- **B.24 API** (`bounties.py:261-280`): Added import of `_project_checked` from `utils/bounty_announcement_payload`; computed `system_statuses` server-side with `"found"` masked to `"checked"` to prevent answer leakage; added `system_statuses` field to route response.
- **B.24 cog** (`bountyCog.py:705-725`): Updated `/route` embed builder to use `data.get("system_statuses")` for 3-state rendering: `recently_spotted` → `**~~system~~** 🔍`, `checked/found` → `~~system~~ ✅`, unchecked → plain.

### Tests Written

- `test_about_router.py::TestListObjectsForCategory::test_list_objects_includes_tech_level_and_manufacturer` — asserts both fields present in preload response
- `test_about_router.py::TestListObjectsForCategory::test_list_objects_tech_level_none_when_missing` — field present even for object types without tech_level
- `test_bounty_router.py::TestGetBountyRoute::test_get_route_includes_system_statuses` — A=3 stops(checked), B/C=1-2 stops(recently_spotted)
- `test_bounty_router.py::TestGetBountyRoute::test_get_route_system_statuses_masks_found` — "found" never in response values
- `test_bounty_router.py::TestGetBountyRoute::test_get_route_system_statuses_empty_for_unchecked_bounty` — empty dict when nothing checked
- `test_config_router.py::test_update_config_unknown_key_returns_422` — renamed from test_update_config_unknown_key_ignored; asserts 422
- `test_config_router.py::test_update_config_unknown_key_samples_returns_422` — exact B.32 scenario
- `test_config_router.py::test_update_config_mixed_valid_unknown_succeeds` — valid+unknown still succeeds
- `test_admin_render_commands.py::test_render_config_set_unknown_setting_blocked` — cog returns error, API not called
- `test_admin_render_commands.py::test_render_config_set_valid_setting_calls_api` — valid setting still goes through
- `test_admin_render_commands.py::test_render_config_set_empty_preload_skips_guard` — empty preload = bypass guard
- `test_bountyCog.py::TestRouteCommand::test_route_recently_spotted_uses_bold_strikethrough` — bold+strikethrough+🔍
- `test_bountyCog.py::TestRouteCommand::test_route_checked_system_uses_strikethrough_checkmark` — plain strikethrough+✅
- `test_bountyCog.py::TestRouteCommand::test_route_unchecked_system_is_plain` — no markdown for unchecked
- `test_bountyCog.py::TestRouteCommand::test_route_backward_compat_no_system_statuses_field` — no crash on old API

### Spec-to-Test Traceability

| Acceptance Criterion | Test File(s) | Status |
|---|---|---|
| A.31: tech_level + manufacturer in preload response | `test_about_router.py::test_list_objects_includes_tech_level_and_manufacturer` | COVERED |
| A.31: field present even without attribute | `test_about_router.py::test_list_objects_tech_level_none_when_missing` | COVERED |
| B.32 cog: unknown setting blocked client-side | `test_admin_render_commands.py::test_render_config_set_unknown_setting_blocked` | COVERED |
| B.32 cog: valid setting calls API | `test_admin_render_commands.py::test_render_config_set_valid_setting_calls_api` | COVERED |
| B.32 service: 422 on all-unknown payload | `test_config_router.py::test_update_config_unknown_key_returns_422` | COVERED |
| B.32 service: 422 for 'samples' scenario | `test_config_router.py::test_update_config_unknown_key_samples_returns_422` | COVERED |
| B.32 service: mixed valid+unknown succeeds | `test_config_router.py::test_update_config_mixed_valid_unknown_succeeds` | COVERED |
| B.24 API: system_statuses field present | `test_bounty_router.py::test_get_route_includes_system_statuses` | COVERED |
| B.24 API: "found" never leaked | `test_bounty_router.py::test_get_route_system_statuses_masks_found` | COVERED |
| B.24 cog: recently_spotted → bold+strikethrough+🔍 | `test_bountyCog.py::test_route_recently_spotted_uses_bold_strikethrough` | COVERED |
| B.24 cog: checked → strikethrough+✅ | `test_bountyCog.py::test_route_checked_system_uses_strikethrough_checkmark` | COVERED |
| B.24 cog: unchecked → plain | `test_bountyCog.py::test_route_unchecked_system_is_plain` | COVERED |

### Coverage Summary (all gates passed)

- bot-core: 2956 passed, 1 skipped — GREEN
- blender-service: 127 passed — GREEN
- discord-gateway: 2132 passed — GREEN
- Ruff source check: All checks passed
