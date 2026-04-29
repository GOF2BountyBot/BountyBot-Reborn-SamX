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

---

## Attempt 5 [2026-04-29 UTC]
Iteration: 5
Status: completed

### Work Completed (Package E — B.26 Autocomplete Preload + Cache Framework)

- **AutocompleteCache helper** (`cogs/_shared/autocomplete_cache.py`, ~120 LOC):
  - Generic `AutocompleteCache[K, V]` with optional TTL, async refresh callable, asyncio.Lock concurrency
  - Stale-on-error fallback for lazy refresh path
  - Monotonic injection for deterministic TTL testing
  - Logger: `discord-gateway-AutocompleteCache.<name>`
- **AdminCog static catalogs** (`adminCog.py`, +70/-20 LOC):
  - `_item_catalog` and `_ship_catalog` AutocompleteCache instances (TTL=None)
  - `_preload_static_catalogs()`: 5-attempt exponential-backoff (5s→10s→20s→40s→60s), independent per category
  - `item_name_autocomplete`: reads from `_item_catalog` (zero HTTP per keystroke, was 1–4)
  - `game_ship_autocomplete`: reads from `_ship_catalog` (zero HTTP, was 1)
  - `player_ship_autocomplete` fallback branch: reads from `_ship_catalog` (zero HTTP, was 1)
- **ShopCog shop cache** (`shopCog.py`, +40/-15 LOC):
  - `_shop_cache` AutocompleteCache (TTL=300s, refresh_fn=`_fetch_tier_shop`)
  - `_fetch_tier_shop(guild_id, tier)` private helper
  - `buy_item_autocomplete`: reads tiers from `_shop_cache` (1 HTTP/tier/5min, was 2–5 per keystroke)
  - `buy` post-success: invalidates `(guild_id, tier)` cache entry
  - `sell` post-success: invalidates `(guild_id, player.tier)` cache entry
- **DevCog /reload_autocomplete** (`devCog.py`, +20 LOC):
  - Added `BountyCog._preload_data`, `AdminCog._preload_render_settings` (recon §7.3 gaps)
  - Added `AdminCog._preload_static_catalogs`
  - Added `ShopCog._shop_cache.clear()` as a cache-clear target (separate logic path)
- **Tests**: 29 new tests (14 unit + 15 integration) covering spec tests #1–29

### Spec-to-Test Traceability

| Acceptance Criterion | Test File(s) | Status |
|---|---|---|
| #1 set then get returns value | `test_autocomplete_cache.py::TestSetAndGet` | COVERED |
| #2 cold miss no refresh_fn returns None | `test_autocomplete_cache.py::TestColdMissNoRefreshFn` | COVERED |
| #3 cold miss with refresh_fn invokes and stores | `test_autocomplete_cache.py::TestColdMissWithRefreshFn` | COVERED |
| #4 second get within TTL does not refresh | `test_autocomplete_cache.py::TestHitWithinTTL` | COVERED |
| #5 get after TTL expiry re-fetches | `test_autocomplete_cache.py::TestTTLExpiry` | COVERED |
| #6 invalidate drops only that key | `test_autocomplete_cache.py::TestInvalidate` | COVERED |
| #7 clear drops all keys | `test_autocomplete_cache.py::TestClear` | COVERED |
| #8 stale-on-error with prior value | `test_autocomplete_cache.py::TestStaleOnError` | COVERED |
| #9 hard miss on error | `test_autocomplete_cache.py::TestHardMissOnError` | COVERED |
| #10 TTL=None never expires | `test_autocomplete_cache.py::TestNoTTLNeverExpires` | COVERED |
| #11 concurrent get invokes refresh_fn once | `test_autocomplete_cache.py::TestConcurrentGetLock` | COVERED |
| #12 keys() and size observability | `test_autocomplete_cache.py::TestObservability` | COVERED |
| #13 preload item catalog all 4 categories | `test_adminCog.py::TestPreloadStaticCatalogs::test_preload_populates_item_catalog_all_categories` | COVERED |
| #14 preload ship catalog | `test_adminCog.py::TestPreloadStaticCatalogs::test_preload_populates_ship_catalog` | COVERED |
| #15 retry on transient error succeeds | `test_adminCog.py::TestPreloadStaticCatalogs::test_preload_retries_on_transient_error_and_succeeds` | COVERED |
| #16 terminal failure leaves caches empty | `test_adminCog.py::TestPreloadStaticCatalogs::test_preload_terminal_failure_leaves_caches_empty` | COVERED |
| #17 item_name_autocomplete after preload no HTTP | `test_adminCog.py::TestItemNameAutocompleteFromCache::test_autocomplete_after_preload_no_http_calls` | COVERED |
| #18 game_ship_autocomplete after preload no HTTP | `test_adminCog.py::TestGameShipAutocompleteFromCache::test_autocomplete_after_preload_no_http_calls` | COVERED |
| #19 item_name_autocomplete filtering by current | `test_adminCog.py::TestItemNameAutocompleteFromCache::test_autocomplete_filters_by_current_substring` | COVERED |
| #20 player_ship_autocomplete fallback from cache | `test_adminCog.py::TestPlayerShipAutocompleteFallbackFromCache::test_fallback_reads_from_ship_catalog_no_http` | COVERED |
| #21 cold cache fetches once, second uses cache | `test_shopCog.py::TestBuyItemAutocompleteWithCache::test_cold_cache_fetches_once_second_hit_uses_cache` | COVERED |
| #22 TTL expiry refetches | `test_shopCog.py::TestBuyItemAutocompleteWithCache::test_after_ttl_expiry_refetches` | COVERED |
| #23 buy success invalidates tier cache | `test_shopCog.py::TestBuyInvalidatesCache::test_buy_success_invalidates_purchased_tier_cache` | COVERED |
| #24 sell success invalidates tier cache | `test_shopCog.py::TestSellInvalidatesCache::test_sell_success_invalidates_seller_tier_cache` | COVERED |
| #25 empty when player resolution fails | `test_shopCog.py::TestBuyItemAutocompleteEdgeCases::test_returns_empty_when_player_resolution_fails` | COVERED |
| #26 Silver player sees Bronze+Silver | `test_shopCog.py::TestBuyItemAutocompleteEdgeCases::test_silver_player_sees_bronze_and_silver_items` | COVERED |
| #27 reload invokes admin static catalogs | `test_devCog.py::TestReloadAutocompletePackageE::test_reload_invokes_admin_preload_static_catalogs` | COVERED |
| #28 reload clears shop cache | `test_devCog.py::TestReloadAutocompletePackageE::test_reload_clears_shop_cache` | COVERED |
| #29 reload invokes bounty/render settings | `test_devCog.py::TestReloadAutocompletePackageE::test_reload_invokes_bounty_preload_and_render_settings` | COVERED |

### Coverage Summary

- discord-gateway: 2165 passed, 0 failed — GREEN
- Ruff src check: All checks passed
- Ruff tests check: All checks passed

---

# Patch I — discord-gateway QA Hardening Activity Log

## Attempt 1 [2026-04-29 UTC]

**Task**: Implement Patch I — 5 QA findings from `/proj/recon/QA-review-A-through-G.md` (discord-gateway only)
**Iteration**: 1
**Status**: completed

### Work Completed

- **F.1**: Extended `_sanitize()` in `http_error_handler.py` with two new regex patterns: `_BARE_HOSTNAME_PATTERN` strips bare service hostnames (`bot-core`, `discord-gateway`, `blender-service`, `db`, with optional `:port` suffix) replacing with `<service>`; `_IPV4_PATTERN` strips IPv4 addresses (with optional `:port`) replacing with `<address>`. 7 new tests added.
- **E.1**: Added detailed TOCTOU/concurrency comment in `autocomplete_cache.py::get()` explaining the double-check locking guarantee (fast-path outside lock + re-check inside lock). Added `TestConcurrentExpiryLock::test_only_one_refresh_fires_when_multiple_gets_hit_expiry` test asserting only one refresh fires when two concurrent gets see an expired entry simultaneously.
- **E.2**: Added explicit guard in `shopCog.py::buy_item_autocomplete` — when `player.get("tier") not in self._valid_tiers`, log `flogger.warning(...)` with guild_id, user_id, and returned tier before returning `[]`. Previously a `ValueError` from `list.index()` was silently swallowed by the outer `except Exception`. Added `TestBuyItemAutocompleteEdgeCases::test_unknown_player_tier_returns_empty_and_logs_warning` test.
- **C.1**: Changed `adminCog.py::render_config` guard from fail-open (`if self._render_settings and ...`) to fail-closed (`if not self._render_settings: return error; if setting not in ...: return error`). Updated `test_render_config_set_empty_preload_skips_guard` → `test_render_config_set_empty_preload_blocks_call` asserting API is NOT called when preload empty. Added logging of WARNING when guard blocks due to empty preload. Also set `_render_settings` in `test_render_config_set` (previously inadvertently relying on fail-open behavior).
- **Cross-1**: Audited all 11 `@is_admin()` sites outside AdminCog. Refactored 9 commands to post-defer inline pattern (matching B.25 fix): `schedulerCog` (6: scheduler_list/view/update/delete/reset/clear) + `devCog` (2: load_data/reload_autocomplete) + `healthCog` `/health`. Documented `healthCog` `/ping` and `helpCog` `/admin_help` as safe (no post-check HTTP calls; immediate `send_message` responses). Import changed from `is_admin` to `_check_is_admin` in schedulerCog/devCog; `is_admin` removed from those imports (still used in healthCog/helpCog for `/ping`/`/admin_help`). Added 13 tests total across 3 test files verifying defer-before-admin-check order.

### Spec-to-Test Traceability

| Acceptance Criterion | Test File(s) | Status |
|---|---|---|
| F.1: bare hostname stripped | `test_http_error_handler.py::test_sanitizer_strips_bare_service_hostnames` | COVERED |
| F.1: hostname:port stripped | `test_http_error_handler.py::test_sanitizer_strips_bare_hostname_with_port` | COVERED |
| F.1: IPv4 stripped | `test_http_error_handler.py::test_sanitizer_strips_ipv4_addresses` | COVERED |
| F.1: hostname in detail field stripped | `test_http_error_handler.py::test_sanitizer_strips_bare_hostname_in_detail_field` | COVERED |
| F.1: IP in detail field stripped | `test_http_error_handler.py::test_sanitizer_strips_ip_in_detail_field` | COVERED |
| E.1: only one refresh fires on concurrent expiry | `test_autocomplete_cache.py::TestConcurrentExpiryLock::test_only_one_refresh_fires_when_multiple_gets_hit_expiry` | COVERED |
| E.2: WARNING logged on unknown tier | `test_shopCog.py::TestBuyItemAutocompleteEdgeCases::test_unknown_player_tier_returns_empty_and_logs_warning` | COVERED |
| C.1: API NOT called when preload empty | `test_admin_render_commands.py::test_render_config_set_empty_preload_blocks_call` | COVERED |
| Cross-1: scheduler_list defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_scheduler_list_defer_before_admin_check` | COVERED |
| Cross-1: scheduler_view defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_scheduler_view_defer_before_admin_check` | COVERED |
| Cross-1: scheduler_update defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_scheduler_update_defer_before_admin_check` | COVERED |
| Cross-1: scheduler_delete defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_scheduler_delete_defer_before_admin_check` | COVERED |
| Cross-1: admin_reset_scheduler defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_admin_reset_scheduler_defer_before_admin_check` | COVERED |
| Cross-1: admin_clear_scheduler defer before admin check | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_admin_clear_scheduler_defer_before_admin_check` | COVERED |
| Cross-1: non-admin rejected via followup | `test_schedulerCog.py::TestCrossOneSchedulerDeferBeforeAdminCheck::test_non_admin_is_rejected_after_defer` | COVERED |
| Cross-1: load_data defer before admin check | `test_devCog.py::TestCrossOneDevCogDeferBeforeAdminCheck::test_load_data_defer_before_admin_check` | COVERED |
| Cross-1: reload_autocomplete defer before admin check | `test_devCog.py::TestCrossOneDevCogDeferBeforeAdminCheck::test_reload_autocomplete_defer_before_admin_check` | COVERED |
| Cross-1: /health defer before admin check | `test_healthCog.py::TestCrossOneHealthDeferBeforeAdminCheck::test_health_defer_before_admin_check` | COVERED |
| Cross-1: /health non-admin via followup | `test_healthCog.py::TestCrossOneHealthDeferBeforeAdminCheck::test_health_non_admin_rejected_via_followup` | COVERED |

### Coverage Summary

- discord-gateway full suite: 2199 passed, 0 failed, 11 warnings — GREEN (18 new tests added over baseline of 2181)
- Ruff check: All checks passed
- Ruff format: 3 test files reformatted; 133 unchanged — clean
- Files outside `services/discord-gateway/` modified: `/proj/DEFECTS.md` (docs update), `/proj/activity.md` (this log)

---

# Patch H — bot-core QA Hardening Activity Log

## Attempt 1 [2026-04-29 UTC]

**Task**: Implement Patch H — address 8 QA findings from `/proj/recon/QA-review-A-through-G.md`
**Iteration**: 1
**Status**: completed

### Work Completed

- **G.1**: Fixed `duplicates_dropped` counter in `evacuate_ship_loadout_to_inventory` — replaced bare `pass` with `duplicates_dropped += removed_other`. Added `assert result["duplicates_dropped"] == 1` to `test_admin_remove_ship_does_not_mint_phantom_duplicate_twice`.
- **A.1**: Changed `payload: dict | None = {}` to `payload: dict = Field(default_factory=dict)` in `UpdateJob` schema — makes null payload reject with HTTP 422. Updated `test_payload_none` → `test_payload_none_raises_validation_error`. Added `test_update_job_rejects_null_payload` to scheduler router tests.
- **G.4**: Added None-filtering in `_get_slot` — `return [x for x in raw if x is not None]` with WARNING log on corrupt entries. Added `test_none_entries_filtered_from_slot_lists` to loadout consistency service tests.
- **G.3**: Added `if __debug__:` post-condition check in `repair_player` after `flush()` — re-scans player's ships and logs WARNING if residual duplicates remain. Added `test_post_condition_check_is_clean_after_successful_repair`.
- **A.2**: Fixed lifespan test mock setup — patched `run_stale_state_recovery_sweep` and `run_stale_respawn_recovery` directly. Added `test_lifespan_b23b_announcement_cleanup_called_for_stale_bounties` with synchronous `.all()` return value `[(1, 67890)]`.
- **G.5**: Added `test_downgrade_after_upgrade_is_safe_noop` to migration tests — uses `importlib.util.spec_from_file_location` to load the migration module and call `downgrade()` directly.
- **G.6**: Added `test_transfer_ship_real_service_phantom_duplicate_state_yields_single_inventory_entry` — uses real `LoadoutConsistencyService` with injected mock repos in a pre-seeded duplicate state. Asserts `add_item.await_count == 1` (not 2).
- **G.2**: Added comment section clarifying simulator vs. real-service test distinction. Added 3 real-service property tests (`test_real_service_*`) using the integration conftest `db_session` fixture against a real SQLite-in-memory session.
- Also updated `test_schemas.py::TestUpdateJobSchema::test_payload_none` to `test_payload_none_raises_validation_error` (the test was testing the now-fixed bug).

### Spec-to-Test Traceability

| Acceptance Criterion | Test File(s) | Status |
|---|---|---|
| G.1: duplicates_dropped counter increments | `test_loadout_consistency_service.py::TestAntiDuplicationExploitClosure::test_admin_remove_ship_does_not_mint_phantom_duplicate_twice` | COVERED |
| A.1: null payload returns 422 | `test_scheduler_router.py::TestUpdateJob::test_update_job_rejects_null_payload` | COVERED |
| A.1: null payload ValidationError | `test_schemas.py::TestUpdateJobSchema::test_payload_none_raises_validation_error` | COVERED |
| G.4: None in slot list filtered | `test_loadout_consistency_service.py::TestEvacuateShipLoadoutToInventory::test_none_entries_filtered_from_slot_lists` | COVERED |
| G.3: post-condition check OK after repair | `test_loadout_consistency_service.py::TestRepairPlayer::test_post_condition_check_is_clean_after_successful_repair` | COVERED |
| A.2: B.23b cleanup branch exercised | `test_main_coverage.py::TestLifespan::test_lifespan_b23b_announcement_cleanup_called_for_stale_bounties` | COVERED |
| G.5: downgrade is safe no-op | `test_migration_b19_repair.py::test_downgrade_after_upgrade_is_safe_noop` | COVERED |
| G.6: transfer_ship real service phantom-dup closure | `test_ship_transfer.py::TestShipTransfer::test_transfer_ship_real_service_phantom_duplicate_state_yields_single_inventory_entry` | COVERED |
| G.2: real-service property tests (evacuate) | `test_loadout_consistency_property.py::test_real_service_evacuate_clears_slots_and_mints_inventory` | COVERED |
| G.2: real-service property tests (repair) | `test_loadout_consistency_property.py::test_real_service_repair_player_deduplicates_across_ships` | COVERED |
| G.2: real-service property tests (anti-dup) | `test_loadout_consistency_property.py::test_real_service_evacuate_anti_duplication_guard_single_mint` | COVERED |

### Coverage Summary

- Line coverage: bot-core full suite — 3146 passed, 1 skipped, 84 warnings — GREEN
- Ruff check: All checks passed
- Ruff format: 239 files formatted — clean
- No files outside `services/bot-core/` modified
