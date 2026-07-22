# Refactor-worker follow-ups (suspected production bugs — DO NOT fix in test-only branch)

## R-bc-repos

### module_repository.create_or_update leaks camelCase source keys into extra_atts
- File: `services/bot-core/src/persist/repositories/module_repository.py:68`
- Code: `extra = {k: v for k, v in raw.items() if k not in (*item_fields, "techLevel", "maxEquipped")}`
- Bug: `*item_fields` unpacks the *item_fields dict keys*, which are snake_case
  (`built_in`, `name`, ...). The raw JSON uses camelCase (`builtIn`). So `builtIn`
  is NOT recognised as an already-consumed key and leaks into `extra_atts`, even
  though its value was already correctly mapped to `built_in`. Result: a module
  created from `{"name": ..., "builtIn": False, ...}` ends up with both
  `built_in=False` (correct) AND `extra_atts={"builtIn": False}` (spurious).
- Impact: data-quality — spurious duplicate key persisted in the JSON `extra_atts`
  column for every module whose source JSON carries `builtIn` (and any other
  camelCase key that has a snake_case item_field mapping).
- Test evidence (documents observed, not desired, behaviour — currently passing):
  `services/bot-core/tests/repositories/test_module_repository.py::
  TestModuleRepositoryCreateOrUpdate::test_create_with_item_fields_separated`
  asserts `captured_kwargs["extra_atts"] == {"builtIn": False}`.
- Suggested fix (for a code branch, not here): build the exclusion set from the
  *raw/camelCase* source keys the mapping consumes (e.g. exclude
  `"builtIn", "techLevel", "maxEquipped", "name", "aliases", "emoji", "icon",
  "value", "wiki", "type"`), or map raw→snake first and filter afterward.

## R-bc-api

### time_announcement DELETE calls httpx AsyncClient.delete(url, json=...) — always 500s
- File: `services/bot-core/src/api/routers/announcements/time_announcement.py:219`
- Code: `resp = await client.delete(f"{DISCORD_GATEWAY_BASE_URL}/messages", json=gateway_request, timeout=10)`
- Bug: httpx's `AsyncClient.delete()` (httpx 0.28.1, the pinned version) has NO
  `json`/`content`/`data`/`files` parameter — per the HTTP spec httpx omits a body
  kwarg from the `.delete()` convenience method. Calling it with `json=` raises
  `TypeError: AsyncClient.delete() got an unexpected keyword argument 'json'`.
  The exception is swallowed by the handler's generic `except Exception` and
  returned as a 500 (`"Failed to delete time announcement: ..."`). So the DELETE
  endpoint NEVER forwards the delete to the gateway and NEVER reaches the
  gateway-response / DB-delete logic — it 500s on every call that gets past the
  404 lookup.
- Why it was hidden: the old test replaced `httpx.AsyncClient` with a blanket
  `AsyncMock` whose `.delete` accepted any kwargs and returned a canned 200 — the
  exact "accept-anything" mock pattern this audit exists to catch. The POST/PUT
  paths are fine because `.post()`/`.put()` DO accept `json=`.
- Impact: `/api/v1/time` DELETE (time-announcement teardown) is broken in
  production; callers get 500 and the message is neither removed from Discord nor
  from the DB.
- Suggested fix (code branch, not here): use `await client.request("DELETE", url,
  json=gateway_request, timeout=10)`, or send the ids as query params, matching
  whatever the discord-gateway `DELETE /messages` handler actually expects.
- Test evidence: `services/bot-core/tests/api/test_time_announcement_router.py`
  — the 4 `TestDeleteTimeAnnouncement` cases that reach the gateway
  (`test_delete_happy_path`, `test_delete_gateway_http_error_returns_500`,
  `test_delete_gateway_missing_ids_returns_500`,
  `test_delete_db_record_not_found_after_gateway_logs_warning`) are marked
  `@pytest.mark.xfail(strict=True)`. When the src is fixed they will XPASS and the
  strict marker will flag the markers for removal.

## R-bc-svc-A

- **discord-gateway/src/bot.py `_autocomplete_health_probe` is untestable in-place.**
  It is a nested closure inside the FastAPI lifespan; the CI-19 startup health probe
  retry/backoff logic cannot be imported or unit-tested. `tests/services/test_ci_ux_batch.py`
  previously "covered" it by re-implementing the loop inline (3 tests), which had already
  drifted from production (prod logs `flogger.warning` after exhaustion; the copied tests
  asserted `.error`). Those 3 tests were deleted (false coverage). FOLLOW-UP: extract the
  probe into a module-level importable helper (e.g. `async def run_health_probe(http, api_base,
  attempts=3, backoff=(1.0, 2.0), logger=...)`) and add a respx-backed test in the
  discord-gateway suite asserting attempt count, backoff sequencing, and the WARNING-on-exhaustion
  path. (Src change — deliberately NOT made by the test-only true-up worker.)

## R-bc-svc-B

Two audit-flagged items were NOT converted to real objects because the
substitution the reports assumed is blocked by a real infrastructure limitation,
not a src bug. `Ship`, `Module`, and their `Item` base all declare `ARRAY(String)`
columns (`src/persist/models/ship.py`, `item.py`), so their tables CANNOT be
created on SQLite — this is exactly why `tests/integration/conftest.py`'s
`_SQLITE_TABLES` deliberately excludes Ship/Module/Item. The bc-svc-2/3 reports'
premise that "db_session already covers PlayerShip/Ship/Module" is inaccurate for
Ship/Module. Both sites are left as faithful boundary fakes (they dispatch on the
*real* queried model entities / faithfully sequence `db.execute`), which is the
correct remediation given the constraint.

- **test_loadout_response_service.py `_make_db_session`** (bc-svc-2, MINOR, effort
  M→L): the fake AsyncSession dispatches on `stmt.column_descriptions[0]["entity"]`
  for PlayerShip/Ship/Module queries. A real SQLite session is infeasible because
  Ship and Module (STI on Item) both carry ARRAY columns. FOLLOW-UP: either add a
  SQLite ARRAY→JSON variant to the Ship/Module/Item models (src change, out of
  test-only scope) or provide a Postgres-backed fixture; only then can these rows
  be seeded for real.
- **test_modules.py `TestBuilderIntegration`** (bc-svc-2, MINOR, effort M, "lower
  priority" per report): MagicMock ORM Module/Ship/PlayerShip over a call-count
  sequenced `db.execute`. Same ARRAY blocker for the Ship/Module rows the builder
  loads. The rest of test_modules.py uses real ModuleStats/ShipLoadout/TickResolver;
  this builder-integration section remains the only entity-fidelity gap. Same
  FOLLOW-UP as above (SQLite ARRAY variant or Postgres fixture).

Also intentionally left (accepted per reports, not defects):
- **test_player_service.py `TestCreateStarterLoadout`** — patches whole repo modules
  via `patch.dict(sys.modules, ...)`; a deliberate wiring test of the starter-loadout
  orchestration (DB boundary). Report rated SMELL/lower-priority; entities elsewhere
  in the file are now real Player/User/GuildConfig/PlayerShip instances.
- **test_secondary_ammo.py repo-constructor monkeypatch** (`_pr.PlayerRepository =
  lambda: mock`) — report says "acceptable as a DB boundary; no action required".
  Left as-is; all PlayerShip entities in the file are now real models.

## R-bc-integration

- **LoadoutConsistencyService — evacuate anti-dup guard destroys a legit second
  copy of the same item NAME** (surfaced by the real-service property sweep in
  `tests/integration/test_loadout_consistency_property.py`, effort M, LATENT
  ITEM-LOSS BUG): `equip_one` never checks other ships, so a player who
  legitimately owns 2 copies of one item name (cargo qty 2) can equip one on ship
  A and one on ship B — a state the design invariant I1 ("no name on 2 ships")
  says should not exist but the equip path permits. When either ship is then
  evacuated (sell_ship / transfer_ship / activate-reconcile →
  `evacuate_ship_loadout_to_inventory`), the anti-dup guard
  (`_remove_one_slot_reference_from_other_ships`,
  `src/services/loadout_consistency_service.py:224` / used at :778) removes the
  OTHER ship's reference AND mints only one copy back, so total ownership drops
  2→1 (one copy destroyed). Root cause: the guard cannot distinguish a legit
  second copy from a B.19 phantom dup because the model tracks items by name with
  no per-instance identity. Fix options (src, out of test scope): (a) `equip_one`
  rejects equipping a name already equipped on another of the player's ships
  (enforce I1 at write time), or (b) the guard keys on inventory provenance before
  dropping. Documented by the strict-xfail test
  `test_evacuate_destroys_legit_second_copy_of_same_name`; the conservation sweep
  deliberately does not generate the same-name-on-two-ships state so I1/I2 stays
  well-defined for the many other real operations it exercises.

## R-gw-cogs-0

No genuine production bugs were surfaced by truer tests in this group (unlike some
other groups' xfail findings) — the discrepancies found were all test-side (wrong
exception type, wrong mock, gate-blocked "success" tests that never reached the
code they claimed to exercise). Fixed in-place; nothing needed to be xfailed.

### Remaining test-suite work not completed (time-boxed; document rather than half-fix)

- **`test_adminCog.py` GROUP V2-A (~110 tests across ~24 classes)**: the bulk
  accept-anything `http_client.<verb> = AsyncMock(...)` responder pattern is still
  present for the non-destructive/non-highest-blast-radius command tests (e.g.
  `TestAdminCheckCommand`, `TestAdminSetupCommand` happy paths, `TestAdminDuel`,
  `TestAdminSpawnBountyAdditional`, `TestAdminConfigBounty`, etc.). This audit's
  Priority 1 items (the highest-blast-radius destructive commands —
  `admin_player` set_credits leg, `admin_give_ship`, `admin_remove_ship`,
  `admin_remove_item`, `admin_uninstall` DELETE, `render_config` set/reset) were
  closed via new respx contract-test classes (`TestAdminGiveShipRespx`,
  `TestAdminRemoveShipRespx`, `TestAdminRemoveItemRespx`,
  `TestAdminPlayerSetCreditsRespx`, `TestAdminUninstallRespx`) plus payload/URL
  assertions added to the existing `TestRenderConfig::test_render_config_set_success`
  / `test_render_config_reset_success`. The remaining ~110 tests are lower blast
  radius (mostly read/view paths or already covered indirectly by a sibling respx
  class) — effort L to fully migrate; the house pattern
  (`_with_real_http_client` + `respx.mock(assert_all_called=True)`, see
  `TestAdminGiveItemRespx` / the classes added above) is proven and ready to extend.
- **`test_aboutCog.py`**: `TestMakeRouteCommand`, `TestCreateObjectEmbed`, and
  `TestD006IconValidationCache` still use the accept-anything MagicMock responder
  for the `/about` object-fetch GET and the icon-validation HEAD request (no
  transport-level route assertion); only `test_about_happy_path_ship`,
  `test_about_object_not_found_404`/`_api_error_non_404`/`_generic_exception` got
  a URL assertion added (S-effort minimum fix per the audit's "minimum acceptable"
  bar). Full respx migration of the HEAD-request icon-cache tests is effort S–M
  each but was not done for time.
- **`test_bountyCog.py`**: the ~60 command tests (`TestCheckCommand`,
  `TestBountiesCommand`, `TestRouteCommand`, `TestCriminalLoadoutCommand`, etc.)
  still use the shared `make_mock_response` fixture (now a REAL `httpx.Response`
  per the shared conftest fix, so `raise_for_status` genuinely raises — this alone
  closed the systemic V2 "accept-anything" defect for this file without further
  changes) but are not migrated to respx for route-level pinning; the report notes
  this blast radius is already limited because the three existing respx classes
  (`TestPreloadData`, `TestCheckCommandRespx`, `TestBountyCommandsRespx`) lock the
  happy-path URLs. `TestCheckCommandTierRoleUpdate` (3 tests, V3 >2 mocks) was left
  untouched — effort L, partially justified (real discord Role/Member need a
  client). Only 3 small SMELL fixes were applied here: `test_setup_function` now
  asserts a real `BountyCog` instance was added, the dead nested `@pytest.fixture`
  in `test_check_multi_capture_embed_shows_payout_breakdown_per_bounty` was
  deleted, and the `TestBountiesNoTimestampsInBadLocations` vacuous
  `if embed is None: return` guards now hard-assert `embed is not None`.
- **`test_duelCog.py`**: not touched. It benefits from the same shared
  `make_mock_response` conftest fix (real `httpx.Response`, so the "no-op
  raise_for_status" defect is already closed file-wide), and the file was
  baseline-verified passing (117/117) before and after this group's other work.
  The report's remaining item — migrating the ~31 command/error tests to respx
  and asserting the `stakes`/`guild_id` challenge JSON body — is effort M and was
  not attempted for time. `test_setup_function` here was explicitly rated
  "Acceptable" by the audit report and intentionally left as-is.
- **`test_adminCog_bounty_commands.py` / `test_admin_render_commands.py`**: the
  route-level respx pinning noted as effort M in the report ("responses are
  `_make_*_response()`/`_make_mock_http_response()` bare MagicMocks... though URL
  substrings/params/payload ARE asserted at call_args level") was not done; the
  higher-value fix (real `httpx.Response` factories so `raise_for_status` actually
  raises, closing the latent "500 silently treated as success" trap) WAS applied
  to `test_adminCog_bounty_commands.py`'s four `_make_*_response` builders.
  `test_admin_render_commands.py`'s `_make_mock_http_response` was left as a bare
  MagicMock (lower priority — no test in that file relies on a non-200 status
  through that factory) but its V1 (real `discord.Permissions`) and V2+V3
  (blanket `httpx.AsyncClient.__aenter__` patch → respx) items were fixed.

## R-gw-cogs-2

No genuine production bugs were surfaced by truer tests in this group — every
new respx contract test and payload assertion passed cleanly against the
existing prod-validated source; nothing needed to be xfailed.

### test_schedulerCog.py clock-job classification (per task instructions)

Checked explicitly: `src/cogs/schedulerCog.py` and every test in
`tests/cogs/test_schedulerCog.py` operate on the GENERIC scheduler interface
only (`/scheduler_list`, `/scheduler_view`, `/scheduler_update`,
`/scheduler_delete`, `/admin_reset_scheduler`, `/admin_clear_scheduler` — all
CRUD over arbitrary APScheduler jobs identified by `job_id`/`job_type`, e.g.
`"bounty_spawn"`, `"bounty_expire"`). There is no time-announcement/"clock" job
prototype anywhere in this cog or its test file — no test constructs a
clock/time-announcement job payload, and neither `schedulerCog.py` nor its
tests reference "clock" or "time announcement" in any form. **All 54 tests in
this file were classified as generic-interface and were in scope for
refactoring**; none were excluded as clock-specific. (The actual clock/
time-announcement prototype lives in bot-core's
`api/routers/announcements/time_announcement.py`, already covered by a
separate audit group — see R-bc-api above — and is unrelated to this cog.)

### Remaining test-suite work not completed (time-boxed; documented rather than half-fixed)

- **`test_playerCog.py`**: added dedicated respx contract-test classes
  (`TestLeaderboardCommandRespx`, `TestPrestigeCommandRespx`) locking the
  leaderboard/prestige/role-config URL+method contracts (mirroring the existing
  `TestProfileCommandRespx` pattern), and hard-asserted the two vacuous
  `TestProfileNoTimestampsInBadLocations` tests. The bulk `TestLeaderboardCommand`
  (5 tests) and `TestPrestigeConfirmFlow` (11 tests) happy-path/error-path tests
  themselves were left on the accept-anything `AsyncMock(http_client.*)` pattern —
  the new dedicated Respx classes close the "wrong URL/verb ships green" blast
  radius without migrating every individual test (effort L to fully migrate all
  ~150 tests in this 4200-line file). `_make_config_resp`/`_make_promo_resp`
  module helpers (used at ~30 call sites) were also left as accept-anything
  MagicMock responders for the same reason — their config-URL contract is now
  covered once by `TestPrestigeCommandRespx`.
- **`test_shopCog.py`**: added `TestShopCommandRespx`, `TestSellCommandRespx`
  (both /sell and /sell-ship payload assertions), `TestShopsCommandRespx`. Bulk
  `TestShopCommand`/`TestShopCommandBranches`/`TestShopCommandWithStats`
  (~30 tests), `TestSellCommand` (~9 tests), `TestShopsCommand` (~6 tests) were
  left on `make_mock_response`/inline MagicMock responders — same rationale as
  above (dedicated contract test closes the blast radius; full per-test
  migration is effort L).
- **`test_shipsCog.py`**: added `TestShipsCommandRespx`, `TestShipCommandRespx`,
  `TestNicknameCommandRespx` (payload-asserted), and a respx contract test in
  `TestGetPlayerIdHelper`. Bulk `TestShipsCommand`/`TestShipCommand`/
  `TestNicknameCommand` tests were left on accept-anything mocks for the same
  reason. Note: the audit report flagged `TestSetactiveAutocomplete` and
  `TestShipAutocomplete` as V2 ("still fetches over mocked HTTP"), but on
  inspection both already use the real cache pattern (`_ac_init_caches` +
  `_ac_ship_nc` real `NormalizedChoice`) — `utils/autocomplete_helpers.py`'s
  `player_ships_autocomplete` reads from `autocomplete_state` caches only, never
  `cog.http_client`, so the `make_mock_response` fixture parameter present on a
  few of those test signatures is simply unused, not a live HTTP mock. No change
  was needed there; the report's classification for those two classes appears to
  be a false positive.
- **`test_skinsCog.py`**: added posted-payload assertions to the
  timeout/generic-exception variants in `TestCompositeTextures` and
  `TestCompositeTexturesWithUpload` (the mock's `call_args` is captured before
  the side_effect raises, so this was a pure test-side fix, no src change).
  `TestCollectPerRegionChoices` and `TestMakeSkinTextureErrorPaths` mock at the
  helper-method level (`_composite_textures`, `_download_skin_image`, etc.)
  rather than at `blender_client.post`, so there was no HTTP payload to assert
  there — left as-is (legitimate boundary, not a V2 pattern).

## R-gw-api-0

**Real production bug found (not fixed — out of scope for a test-refactor pass):**
`src/api/routers/guilds.py::create_role` (two call sites, ~line 335 and ~354) does
`raise HTTPException(status_code=status.HTTP_422, detail="Invalid permissions bitmask")`
for both the negative-permissions and permissions-bitmask-mismatch branches. `fastapi.status`
has no `HTTP_422` attribute (only `HTTP_422_UNPROCESSABLE_CONTENT`/the deprecated
`HTTP_422_UNPROCESSABLE_ENTITY`), so this line always raises `AttributeError` instead of
building the intended `HTTPException`. The outer generic `except Exception` then maps that
`AttributeError` to a real 500 via `handle_discord_exception`. Net effect: `POST
/guilds/{id}/roles` can **never** actually return 422 for an invalid permissions bitmask —
it always 500s, with a generic "Failed to create role: HTTP_422" style message instead of
"Invalid permissions bitmask". `tests/api/test_guilds_extended.py`'s
`TestCreateRole::test_create_role_negative_permissions_returns_error` and
`TestCreateRolePermsBitmask::test_create_role_permissions_bitmask_mismatch` were updated to
assert the real (500) behavior instead of the old disjunctive `status_code in (422, 500,
503)` hedge, with a comment pointing back here. Fix: `status.HTTP_422_UNPROCESSABLE_CONTENT`.

**Shared mock-infra gap** (not fixed — `tests/mocks/discord_mock_utils.py` is shared across
all groups and explicitly out of scope for this pass): `DiscordMockUtils.create_mock_member`
sets `.avatar`/`.created_at`/`.public_flags`/`.bot`/`.system` only on the nested `.user`
sub-object, not on the `Member` mock itself. Real `discord.Member` delegates undefined
attribute access for exactly these fields to its underlying `User` via `__getattr__`, and
`UserConverter.user_to_payload` (called from `member_to_payload`) reads them straight off the
`Member` object it's given, matching that real delegation. Without mirroring the delegation,
any unmocked `UserConverter`/converter test that serializes a `create_mock_member(...)`
result raises a pydantic `ValidationError` (`avatar`/`created_at` land as bare `MagicMock`
instances instead of `str`/`None`). Worked around locally in `test_guilds.py`,
`test_guilds_extended.py` and `test_permissions.py` by manually copying
`member.avatar = member.user.avatar` (+ `created_at`/`public_flags`/`bot`/`system`) after
construction, with an explanatory comment at each site. Recommend fixing this once in
`create_mock_member` itself so every group's tests get it for free.

**Files changed** (12; discord-gateway, no DB env needed): `test_channels.py`,
`test_categories.py`, `test_categories_extended.py`, `test_channels_extended.py`,
`test_channels_upload.py`, `test_guilds.py`, `test_guilds_extended.py`, `test_messages.py`,
`test_messages_extended.py`, `test_permissions.py`, `test_announcements.py` — all fully
de-mocked per the audit's SYS-1/2/3/4 remediation (stopped patching
`resolve_bot`/`get_entity_or_404`/`handle_discord_exception`; real converters run against
real-attributed mock Discord objects; disjunctive `status_code in (...)` asserts pinned to
exact values). `test_internal_autocomplete.py` was left untouched (already rated OK/model
file by the audit — verified still green, no changes needed).

**Remaining SYS-4/SYS-5 work not completed in this pass** (documented per audit's "Large
items… otherwise document precisely" rule, `test_channels_extended.py` only — 2148 lines /
74 tests, explicitly rated "effort L (volume)" in the audit):
- The file's *first* app builder (`_build_app`, backing `channels_app_and_mocks`/
  `channels_client`, used by ~58 of the 74 tests) had its module-level
  `sys.modules["discord"]` swap removed and `resolve_bot`/`get_entity_or_404`/
  `handle_discord_exception` unpatched (SYS-1/2/3) — the pre-existing swap was actually the
  root cause forcing `resolve_bot` to stay patched everywhere: it bound
  `DiscordMockUtils.create_mock_bot()`'s `MagicMock(spec=commands.Bot)` against a *different*
  `commands.Bot` class than the one `resolve_bot`'s `isinstance` check resolved once the
  patch was removed, so `resolve_bot` always 500'd until the swap itself was deleted. Fixing
  that unblocked real 404/bot-readiness/generic-exception coverage across the whole file.
  `ChannelConverter`/`PermissionConverter`/`EmbedConverter`/`validate_channel_type`/
  `create_permission_overwrite` remain patched with canned return dicts in this builder
  (SYS-4) — not attempted, given the volume of per-test response-body assertions keyed off
  those canned dicts (~58 tests' worth of success-path bodies would need re-deriving from
  real converter output, similar to the treatment given to `test_guilds_extended.py` and
  `test_messages_extended.py` in this same pass).
- The file's *second* app builder (`_build_app_with_discord_patch`, used by
  `TestGenericExceptionHandlers`, `TestUpdateChannelCategoryResolution`,
  `TestUpdateChannelTypeSpecificFields`, and a handful of other type-dispatch/edge-case
  classes — ~16 call sites) still patches all of `resolve_bot`/`get_entity_or_404`/
  `handle_discord_exception`/all four converters/`validate_channel_type`/
  `create_permission_overwrite`, plus a locally-scoped fake `discord` module for isinstance
  dispatch (a legitimate, already-audit-approved pattern for the type-dispatch tests
  specifically, per the original report's "Category/forum rejection tests... Good pattern"
  note — but the other four helpers/converters there are still SYS-1/2/3/4 violations).
  Recommend a follow-up pass applying the same real-`resolve_bot`/`get_entity_or_404`
  treatment there (the module-identity blocker is already gone at file scope) plus the
  SYS-4 converter unpatch for both builders together, since they share the same channel
  factories (`create_mock_text_channel` et al.) already carrying real attributes.

## R-gw-api-1

**Real production bug found (not fixed — out of scope for a test-refactor pass):**
`src/api/routers/tags.py` has the same nonexistent-`status.HTTP_422`-attribute bug as the
one already logged in R-gw-api-0 for `guilds.py::create_role` — two call sites, in
`create_forum_tag` (~line 128) and `update_tag` (~line 234):
`raise HTTPException(status_code=status.HTTP_422, detail=f"Invalid emoji: {...}")`.
`fastapi.status` has no `HTTP_422` attribute, so both invalid-emoji branches raise
`AttributeError` instead of the intended `HTTPException`, and the outer generic
`except Exception` maps that `AttributeError` to a real 500 via `handle_discord_exception`
(a generic "Failed to create forum tag: ..."/"Failed to update tag: ..." message) instead of
the intended 422 "Invalid emoji: ...". Net effect: `POST /channels/{id}/tags` and
`PUT /tags/{tag_id}` can never actually return 422 for a malformed emoji — they always 500.
Fix: `status.HTTP_422_UNPROCESSABLE_CONTENT` at both sites. No test in this group's files
currently exercises the invalid-emoji request path (none previously did either), so this
was found via code reading while building `test_tags_deep.py`'s real-object coverage, not
via a newly-red test; nothing needed to be xfailed.

**Minor dead-code observation** (informational only, not fixed): `src/api/routers/roles.py`
`update_role`'s permissions-bitmask-mismatch check —
`perms = discord.Permissions(role_data.permissions); if perms.value != role_data.permissions: raise 422`
— is unreachable with a real `discord.Permissions`: its `__init__` stores the input int
verbatim (`self.value = permissions`) with no masking/validation, so `perms.value` always
equals the input. Only the preceding `role_data.permissions < 0` check can ever fire for
real. `test_roles_extended.py::TestUpdateRoleExtended::test_update_role_invalid_permissions_bitmask`
still exercises this branch, but only by patching `api.routers.roles.discord.Permissions`
itself to return a mismatched `.value` — documented in that test's docstring as a justified
mock of an otherwise-unreachable defensive branch, not a real object standing in for a live
one.

**Shared mock-infra fix applied** (in scope per this task's assignment):
`tests/mocks/discord_mock_utils.py::DiscordMockUtils.create_mock_member` now mirrors
`avatar`/`created_at`/`public_flags`/`bot`/`system` from the constructed `.user` onto the
`Member` mock itself, matching real `discord.Member`'s `__getattr__` delegation to its
underlying `User` for these fields (previously only set on `member.user`, forcing every
group to work around it locally with a manual copy — R-gw-api-0's `test_guilds.py`/
`test_guilds_extended.py` had one such workaround, now redundant but harmless since it
re-sets the same values). Verified all of `tests/api/` (559 tests) plus the two other test
modules that call `create_mock_member` outside `tests/api/`
(`tests/cogs/test_inventoryCog_give.py`, `tests/utils/test_discord_converters.py`) still
pass after the change.

**Files changed** (11; discord-gateway, no DB env needed): `test_tags.py`, `test_tags_extended.py`,
`test_tags_extra.py`, `test_tags_deep.py`, `test_threads.py`, `test_threads_extended.py`,
`test_roles.py`, `test_roles_extended.py`, `test_users.py`, `test_users_extended.py`,
`test_permissions_extended.py` — all de-mocked per the audit's remediation guidance: stopped
stubbing `get_entity_or_404`/`find_thread_by_id`/`resolve_bot`/`handle_discord_exception`
(except at genuine network/readiness boundaries, and only in the specific tests whose intent
is to force an unexpected-exception 500 path); real `ChannelConverter`/`RoleConverter`/
`UserConverter`/`MessageConverter`/`PermissionConverter` run against real-attributed,
`spec=discord.X`-typed mock Discord objects so `isinstance` checks, `hasattr` checks, and
serialized response bodies are all genuine; removed all `sys.modules["discord"]` swaps
(they were the root cause forcing helper-patching, same pattern found by the sibling group
in `test_channels_extended.py`); reduced per-fixture patch counts toward the ≤2 target,
with remaining multi-mock fixtures commented to justify each mock (e.g.
`permissions_for()`/`overwrite.pair()` in `test_permissions_extended.py`, which stand in for
discord.py's own permission math — unit-tested separately, matching the audit's note).
Also fixed: `tests/mocks/discord_mock_utils.py::create_mock_member` (shared infra, see
above).

**Notable design choices for future readers:**
- `test_tags*.py`: mock `ForumChannel`/`ForumTag` objects use `spec=discord.ForumChannel`/
  `spec=discord.ForumTag`. The installed discord.py (2.7.1) has neither
  `ForumTag.edit`/`.delete` nor `ForumChannel.edit_tag`/`.delete_tag`, so by default
  `hasattr()` on these spec'd mocks is faithfully `False` for those, and the router's real
  last-resort `channel.edit(available_tags=...)` fallback runs by default — exactly as
  production does today. Tests that specifically target the "if the library exposes a nicer
  method" branches explicitly attach that method to the spec'd mock (spec restricts unset
  *reads*, not writes) to model a hypothetical richer discord.py variant, with a comment
  saying so.
- A handful of `test_tags_deep.py`/`test_tags_extra.py` tests exercise branches that are
  provably dead code for the real `ChannelConverter` (which always returns a plain dict —
  see `tag_to_dict` in `src/utils/discord_helpers.py`) or for the real
  `tags_to_edit_payload` (which always sanitizes tag ids to int-or-absent before building a
  payload dict). Those tests patch only the single narrowest function/method needed
  (`patch.object(ChannelConverter, "forum_tag_to_payload")` or
  `patch("api.routers.tags.tags_to_edit_payload", ...)`) to reach them, each with a comment
  explaining why no real object can take that path.

**Gate:** `cd services/discord-gateway && python -m pytest tests/api/ --timeout=120 -q` →
559 passed. Ruff clean (line-length 120) on all touched files. Test counts: no file shrank;
`test_threads.py` 15→16, `test_roles.py` 21→23, `test_users.py` 11→12 (added tests
strengthening real-object coverage, e.g. multi-entity role selection, real 404-on-fetch
paths); all other touched files unchanged in count.
