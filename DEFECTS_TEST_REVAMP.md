# Defect Report — S11 QA Review (Final Verification)

**Reviewer**: Tester (QA Review agent)
**Date**: 2026-05-08
**Sprint**: S11 — Final verification pass across all three services (B.76 close-out)

---

## DEF-S11-001 — CRITICAL: test_bounty_orchestrator.py Poisons sys.modules at Module Level

**Severity**: High
**Defect Type**: Test
**Service**: bot-core

**Expected**: All 3,236 bot-core tests pass when run as a full suite.

**Actual**: 36 tests fail when run as the full suite, all passing when run in isolation. Root cause: `test_bounty_orchestrator.py` injects module-level stubs at lines 60–165 for `persist`, `persist.database`, `persist.repositories`, and `services` into `sys.modules` using `_ensure_stub()`. These stubs persist for the entire pytest session. When pytest later collects `test_bounty_respawn_executor.py`, `test_bounty_spawn_executor.py`, `test_temperature_decay_executor.py`, `test_shop_refresh_executor.py`, and `test_startup_jobs.py`, those files do `from persist.models.base import Base` — but `persist` is now a `types.ModuleType` stub, not a real package. Result: `ModuleNotFoundError: No module named 'persist.models'` and `ModuleNotFoundError: No module named 'services.game_constants'; 'services' is not a package`.

**Test files affected by contamination**:
- `test_bounty_respawn_executor.py` — 4 of 6 tests FAIL
- `test_bounty_spawn_executor.py` — 18 of 31 tests FAIL
- `test_shop_refresh_executor.py` — 4 of 7 tests FAIL
- `test_startup_jobs.py` — 1 of 10 tests FAIL
- `test_temperature_decay_executor.py` — 13 of 13 tests FAIL

**Contamination does NOT affect** tests ordered BEFORE test_bounty_orchestrator.py in collection order (alphabetically earlier files).

**Fix**: In `test_bounty_orchestrator.py`, move all `sys.modules` stub injections into a module-scoped fixture with cleanup, or use `unittest.mock.patch.dict(sys.modules, ...)` as a context manager per test class/function. The module-level injections must NOT persist beyond the tests in this file.

**Reproduction**: `cd /proj/services/bot-core && python -m pytest tests/ -q --tb=no 2>&1 | tail -5` → shows 36 failed.
**Isolation confirm**: `cd /proj/services/bot-core && python -m pytest tests/test_bounty_respawn_executor.py -q --tb=no` → 6 passed.

---

## DEF-S11-002 — discord-gateway Timing Target Missed (14m20s vs ≤8min)

**Severity**: Medium
**Defect Type**: Test Performance

**Baseline**: 11m 43s (no cov), 13m 26s (with cov).
**Target**: ≤8 minutes (no cov).
**Actual (S11 measurement)**: 14m 20s (2663 tests, no coverage).

The `--cov` was successfully removed from addopts (S1). However, overall timing INCREASED from baseline rather than decreased. This suggests the 290 new tests added during S7–S10 (body assertion rewrites + new cog coverage) added wall-clock time, and the fixture scope optimizations planned in Phase 1 of SCOPE_TEST_QUALITY_AND_SPEED.md were NOT implemented (no `module`-scope fixture changes are visible in the git log). The 8-minute target is not met.

---

## DEF-S11-003 — 282 Bare assert_called_once() Patterns Remain

**Severity**: Low
**Defect Type**: Test Quality

`grep -r "assert_called_once()$" services/*/tests/ | wc -l` = **282**

These are bare `assert_called_once()` assertions (ending the line, no trailing assertion). Many are secondary assertions alongside real value checks (acceptable per S5 gate evaluation). No test has been confirmed to use `assert_called_once()` as its SOLE assertion in the post-blitz state, but this should be verified systematically.

---

## DEF-S11-004 — Ruff: 3 Errors Remaining in Test Files

**Severity**: Low (test files only)
**Defect Type**: Linting

`python -m ruff check services/` reports 3 errors:
1. `services/bot-core/tests/api/test_players_router.py:135` — E501 (line too long, 131 chars)
2. `services/bot-core/tests/api/test_players_router.py:147` — E501 (line too long, 125 chars)
3. `services/discord-gateway/tests/cogs/test_setupCog.py:413` — F401 (`discord` imported but unused) [auto-fixable]

These are in test files, not production code. No production ruff errors found.

---

## S11 Measurements Summary

| Metric | Baseline | S11 Measured | Target | Status |
|--------|----------|--------------|--------|--------|
| bot-core tests pass rate | 3,223/3,224 | 3,199+1 skipped/3,236 | 100% | ❌ 36 failures (DEF-S11-001) |
| bot-core timing (no cov) | 40s | 65s | <5min | ✅ |
| discord-gateway tests pass rate | 2,370/2,370 | 2,663/2,663 | 100% | ✅ |
| discord-gateway timing (no cov) | 11m43s | 14m20s | ≤8min | ❌ (DEF-S11-002) |
| blender-service tests pass rate | 127/127 | 222/222 | 100% | ✅ |
| blender-service timing | 2.7s | 3.1s | <30s | ✅ |
| blender-service router coverage | 27-47% | 100% | 90%+ | ✅ |
| confirm_view coverage | 39% | 100% | 90%+ | ✅ |
| duelCog coverage | 68% | 95% | 90%+ | ✅ |
| skinsCog coverage | 75% | 98% | 85%+ | ✅ |
| adminCog coverage | 85% | 89% | ≥88% | ✅ (per S10 gate) |
| Bare assert_called_once() count | unknown | 282 | 0 sole | ⚠️ unverified if sole |
| Mutation testing | unavailable | unavailable | — | N/A |

---

*Generated: 2026-05-08 by QA Reviewer (S11)*

---

# Defect Report — S7 QA Review

**Reviewer**: Tester (QA Review agent)
**Date**: 2026-05-08
**Sprint**: S7 — Test Quality Blitz (tautological test fix)
**Files Reviewed**:
- `services/discord-gateway/tests/api/test_threads_extended.py` (66 tests)
- `services/discord-gateway/tests/api/test_tags_deep.py` (43 tests)
- `services/discord-gateway/tests/api/test_channels_extended.py` (74 tests)

---

## DEF-S7-001 — G2 VIOLATION: 76 Tests Assert Only Status Code, Zero Body Fields

**Severity**: High  
**Defect Type**: Test  
**Acceptance Criterion Violated**: G2 — "No test asserts only on status code with zero body field assertions"

**Expected**: Every test in all 3 files asserts ≥1 response body field (e.g., `response.json()["status"]`, `response.json()["detail"]`, etc.) OR a meaningful value assertion beyond just call counts.

**Actual**: 76 tests (out of 183) assert ONLY on `response.status_code` with zero assertions on any body field.

**Affected Tests by File**:

### test_threads_extended.py — 26 violations

| Line | Test Name | Asserts |
|------|-----------|---------|
| 380 | `test_update_thread_tags_with_tag_objects_by_id` | `status_code == 200` only |
| 392 | `test_update_thread_tags_with_tag_object_by_name` | `status_code == 200` only |
| 417 | `test_update_thread_tags_empty_list` | `status_code == 200` only |
| 612 | `test_delete_message_bot_has_manage_perm` | `status_code == 200` only |
| 661 | `test_delete_message_message_not_found` | `status_code == 404` only |
| 673 | `test_delete_message_no_guild_member_returns_403` | `status_code == 403` only |
| 718 | `test_get_thread_via_get_channel_fallback` | `status_code == 200` only |
| 754 | `test_get_thread_via_fetch_channel_fallback` | `status_code == 200` only |
| 1216 | `test_update_thread_get_channel_exception_then_fetch` | `status_code == 200` only |
| 1229 | `test_close_thread_get_channel_exception_then_fetch` | `status_code == 200` only |
| 1242 | `test_open_thread_get_channel_exception_then_fetch` | `status_code == 200` only |
| 1282 | `test_get_thread_fetch_channel_forbidden` | `status_code == 404` only |
| 1295 | `test_update_thread_fetch_channel_not_found` | `status_code == 404` only |
| 1308 | `test_update_thread_fetch_channel_forbidden` | `status_code == 404` only |
| 1321 | `test_close_thread_fetch_channel_not_found` | `status_code == 404` only |
| 1334 | `test_close_thread_fetch_channel_forbidden` | `status_code == 404` only |
| 1347 | `test_open_thread_fetch_channel_not_found` | `status_code == 404` only |
| 1360 | `test_open_thread_fetch_channel_forbidden` | `status_code == 404` only |
| 1462 | `test_close_thread_outer_exception` | `status_code == 500` only |
| 1469 | `test_open_thread_outer_exception` | `status_code == 500` only |
| 1476 | `test_update_thread_tags_outer_exception` | `status_code == 500` only |
| 1483 | `test_list_thread_messages_outer_exception` | `status_code == 500` only |
| 1490 | `test_create_thread_message_outer_exception` | `status_code == 500` only |
| 1497 | `test_get_thread_message_outer_exception` | `status_code == 500` only |
| 1504 | `test_edit_thread_message_outer_exception` | `status_code == 500` only |
| 1511 | `test_delete_thread_message_outer_exception` | `status_code == 500` only |

**Note**: The sibling test `test_update_thread_outer_exception` (line 1454) DOES correctly check `assert "detail" in response.json()`. The other 8 outer-exception tests in the same class are missing this body check.

### test_tags_deep.py — 26 violations

| Line | Test Name | Asserts |
|------|-----------|---------|
| 320 | `test_get_tag_object_payload_no_dict_attribute` | `status_code in (200, 500)` only — ambiguous |
| 422 | `test_create_channel_edit_attributeerror_uses_proxy_fallback` | `status_code == 201` only |
| 464 | `test_create_dict_response_emoji_normalize_raises_silently` | `status_code == 201` only |
| 499 | `test_create_object_response_setattr_raises_uses_dict_fallback` | `status_code == 201` only |
| 599 | `test_update_tag_no_edit_no_edit_tag_uses_payload_fallback` | `status_code == 200` only |
| 632 | `test_update_tag_no_edit_no_edit_tag_edit_raises_attributeerror_proxy` | `status_code == 200` only |
| 667 | `test_update_tag_refetch_by_name_when_id_lookup_fails` | `status_code == 200` only |
| 713 | `test_update_tag_refetch_falls_back_to_original` | `status_code == 200` only |
| 756 | `test_update_tag_dict_response_emoji_none_but_requested` | `status_code == 200` only |
| 790 | `test_update_tag_dict_response_emoji_none_normalize_raises` | `status_code == 200` only |
| 835 | `test_update_tag_object_response_setattr_raises_uses_dict_fallback` | `status_code == 200` only |
| 877 | `test_update_tag_object_response_setattr_raises_with_emoji` | `status_code == 200` only |
| 969 | `test_delete_tag_deleted_false_returns_500` | `status_code == 500` only |
| 1067 | `test_get_tag_frozen_payload_with_emoji_normalize_raises` | `status_code == 200` only |
| 1181 | `test_create_proxy_to_dict_is_invoked_with_id` | `status_code == 201` only |
| 1230 | `test_create_proxy_to_dict_with_non_int_id` | `status_code == 201` only |
| 1342 | `test_create_frozen_payload_with_emoji_normalize_raises` | `status_code == 201` only |
| 1622 | `test_update_proxy_to_dict_invoked_with_int_id` | `status_code == 200` only |
| 1673 | `test_update_proxy_to_dict_invoked_with_non_int_id` | `status_code == 200` only |
| 1772 | `test_update_tag_edit_tag_raises_runtime_error` | `status_code == 500` only |
| 1860 | `test_update_dict_response_emoji_normalize_raises_with_emoji_in_request` | `status_code == 200` only |
| 1966 | `test_update_non_dict_response_emoji_none_with_requested_emoji` | `status_code == 200` only |
| 2018 | `test_update_non_dict_response_emoji_none_normalize_raises` | `status_code == 200` only |
| 2117 | `test_delete_all_remaining_malformed_empty_payloads` | `status_code == 200` only |
| 2195 | `test_delete_proxy_to_dict_invoked_with_int_id` | `status_code == 200` only |
| 2234 | `test_delete_proxy_to_dict_invoked_with_non_int_id` | `status_code == 200` only |

### test_channels_extended.py — 24 violations

| Line | Test Name | Asserts |
|------|-----------|---------|
| 313 | `test_get_channel_not_found` | `status_code == 404` only |
| 318 | `test_get_voice_channel_success` | `status_code == 200` only |
| 323 | `test_get_forum_channel_success` | `status_code == 200` only |
| 414 | `test_update_channel_not_found` | `status_code == 404` only |
| 420 | `test_update_channel_position` | `status_code == 200` only |
| 426 | `test_update_channel_topic_nsfw_slowmode` | `status_code == 200` only |
| 432 | `test_update_channel_empty_payload_no_edit` | `status_code == 200` only |
| 438 | `test_update_voice_channel_bitrate` | `status_code == 200` only |
| 444 | `test_update_category_returns_400` | `status_code == 400` only |
| 509 | `test_delete_channel_not_found` | `status_code == 404` only |
| 538 | `test_list_messages_not_found` | `status_code == 404` only |
| 543 | `test_list_messages_with_limit` | `status_code == 200` only |
| 548 | `test_list_messages_limit_too_large` | `status_code == 422` only |
| 621 | `test_create_message_channel_not_found` | `status_code == 404` only |
| 645 | `test_get_permissions_not_found` | `status_code == 404` only |
| 723 | `test_update_permissions_not_found` | `status_code == 404` only |
| 729 | `test_update_permissions_with_role_overwrite` | `status_code == 200` only |
| 779 | `test_update_permissions_member_not_found_skip` | `status_code == 200` only |
| 840 | `test_list_threads_channel_not_found` | `status_code == 404` only |
| 930 | `test_create_thread_channel_not_found` | `status_code == 404` only |
| 1024 | `test_list_tags_channel_not_found` | `status_code == 404` only |
| 1156 | `test_move_channel_not_found` | `status_code == 404` only |
| 1161 | `test_move_channel_category_not_found` | `status_code == 404` only |
| 2010 | `test_delete_channel_message_channel_not_found_returns_404` | `status_code == 404` only |

**Remediation Required**: Each of the 76 tests needs at least one additional body field assertion. Examples:
- For 200 success: add `assert response.json()["status"] == "success"` (or "updated", "created", "deleted" as appropriate)
- For 404 error: add `assert "detail" in response.json()`
- For 403 error: add `assert "detail" in response.json()`
- For 500 error: add `assert "detail" in response.json()`
- For 400 error: add appropriate detail check

---

## G3 Note — Mock Count

**Note for developer**: The mock count per test frequently exceeds 2 (many tests use 5-6 `patch()` calls). However, after consulting the AGENTS.md project context, these are router-infrastructure isolation patches — all tests for REST API routers in this service use this pattern. The `max 2 mocks per test` rule from AGENTS.md specifically targets cross-service HTTP client mocking in cog tests, not router isolation patches. This is documented as a judgment call: **G3 is evaluated as contextually PASS for router tests**.

---

## Mutation Testing

**Status**: Unavailable — `mutmut` and `cosmic-ray` modules not installed in the environment.

---

*Generated: 2026-05-08 by QA Reviewer*
