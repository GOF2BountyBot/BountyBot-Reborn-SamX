# Custom Events — Implementation Reference (issue #30) — v4, as built (2026-09-04)

**Status:** implemented on branch `feat/custom-events` (PR #102 → dev). This doc now describes what was built; §12 keeps the cut list.

**Status:** spec, not implemented. **Catalog comment:** https://github.com/GOF2BountyBot/BountyBot-Reborn-SamX/issues/30#issuecomment-5526628936
**Legacy:** original bot had no event code — admins ran stat races by hand off `leaderboard -c/-s/-w`. Nothing to port.
**v1 of this doc** carried Δ-counter snapshots, one-time scheduler jobs, an update command, relative date parsing, Discord Server Events, and more. §12 lists every cut and when to add it back.

## 1. The ask
- Admin creates a challenge from a catalog, ~1–2 weeks (`duration_days`, default 7), then seeds a **minted** prize pool (credits / ship / equipment), then starts it (now or scheduled).
- Payout models, composable: **Top N** (same prize ranks 1..N), **per-rank** (1st X, 2nd Y…), **participation** (everyone who met the event's gates).
- **Soft enrolment:** nobody signs up; playing the game feeds the tally. Per-guild; many events may run at once; everything read-only for players.
- Catalog extendable without migrations.

## 2. Model (4 tables)
| Table | Fields |
|---|---|
| `game_events` | `id`, `guild_id`, `type_slug`, `params` JSONB (`division`, `weapon`/`subtype`/`module` — only what the type needs), `state` (`draft`/`scheduled`/`active`/`ended`/`cancelled`), `duration_days`, `scheduled_start_at`, `started_at`, `ends_at`, `created_by_user_id` |
| `game_event_prizes` | `id`, `event_id`, `rank_from`, `rank_to` (both NULL = participation; `k..k` = per-rank; `1..N` = Top N), `kind` (`credits`/`item`/`ship`), `item_ref`, `qty`. Overlap rejected on add. |
| `game_event_metrics` | PK (`event_id`, `player_id`, `metric`), `value` numeric. `player_id` = `players.id` FK (NOT the Discord user id), `ondelete=CASCADE`; same on `event_results`. One row per tally (`nukes`, `fights`, `captures`, …), **created lazily by the first contribution** = soft enrolment. Writes are a single native upsert: `INSERT … ON CONFLICT DO UPDATE SET value = value + excluded.value` (sum) or `= GREATEST(value, excluded.value)` (max) — atomic under concurrent fights, no JSON read-modify-write, no row locks. Rank key is computed from the rows at read time (a few hundred rows per event). Pruned by the existing `db_retention_executor.py` (add one block + `GameConstants.EVENT_METRICS_RETENTION_DAYS = 30`). |
| `event_results` | one row per (`event_id`, `player_id`): `guild_id`, `type_slug`, `rank`, `value`, `qualified`, `prize` (text — may name a rank prize *and* the participation award), `status`, `awarded_at`. Permanent; written on `ended` only. Feeds medals (§5). |

## 3. Scoring — one mechanism
Every type is fed by hooks calling one function:
`event_service.record(session, player, contrib: dict[str, number], *, context, stakes=None)` → for each `active` event in `player.guild_id` whose type consumes any key in `contrib`: skip if `context == "duel"` and `stakes < guild_config.event_min_duel_stakes`; skip if the event is division-scoped and `player.tier != params.division`; upsert each metric row (sum or max per the registry). Wrapped in try/except + log, never aborts the caller (same rule as `_increment_player_stats`). `stakes` reaches the fight hook through one new kwarg `fight_ships(..., stakes=None)` (`combat_service.py:254`), passed by `duel_service` (~L375, has the duel in scope).

Hook sites (all existing code, one call each):
| Site | Provides |
|---|---|
| `combat_service._increment_player_stats` (~L428–500) | per-side summary: `secondary_fired{…}`, `module_activations{cloak,booster,emergency_system}`, `shots_fired/hit`, `damage_dealt/taken` (absorbed), `duration_ticks`, winner, `context`. Extend `_build_fight_summary` (`combat_resolver.py` ~L1177) with two fields: `killing_blow_subtype`, `max_nuke_absorbed` — the timeline is in memory there. |
| `duel_service` ~L392–431 | stakes, winner/loser, stalemate |
| `bounty_service.py:2981–2983` | `systems_checked_count`, capture (+1) — one hook covers both |
| `player_service.py:509` (promotion) and `:574` (demotion) | one call each: delete the player's metric rows in active division-scoped events — they start over in the new tier. Prestige (`:702`) and admin reset (`admin.py:614`) are ignored on purpose (user: repercussions accepted). |

**Duel stakes filter** (user decision): global, default **1000**, guild-settable. New `guild_configs.event_min_duel_stakes` + field on `GameConstantsOverridesMixin` + `_METADATA_FIELDS` + `FIELD_DESCRIPTIONS` (`api/routers/config.py:657` pattern) → appears in `/admin_config` automatically. No per-event override.
**Bounty semantics (user, 2026-09-04):** a capture = found the criminal's system and won the fight (Bronze: capture guaranteed on the find, the battle is a bonus). Every `/check`, including found-but-lost, counts as taking part. Checks are credited to all checkers when the bounty is captured (same rule as the lifetime `systems_checked` stat); an expired bounty credits nobody. Longest-battle values are stored in ticks but displayed in seconds (0.1 s). **Participation = did the activity (user, 2026-09-04).** Every type declares an `activity` metric it also tallies — `duel_fights` for duel types, `fights` for combat types, `checks` for bounty types — so a player who engaged but scored zero (three lost duels in a `duels_won` event) still has an entry, shows at 0 on the board, and earns the participation award. **`min_fights` is a per-event param** (admin sets at create; default 10 on max/ratio types, 1 otherwise): qualified ⇔ `activity ≥ min_fights` (plus any type-specific rule such as no-capture). Design pattern this enables — the *lossless* event: `duels_won`, `min_fights=3`, participation 3000 credits, stakes floor 1000 → a player who loses all three still breaks even. `division` — enforced at write time (above) plus the two tier-change calls.
**Admin `reset_player` / prestige:** irrelevant — events never read `Player` counters (user: current behaviour is fine anyway).
**Combat contexts** are only `duel`, `bounty_pvc`, `bounty_bonus` — no free practice-fight farming vector.

## 4. Catalog (v1)
`agg`: Σ sum · max · ratio. Gate: S = stakes filter (all duel-context contributions, every type) · F = `min_fights` (per-event param, default 10 on max/ratio types else 1) · D = `division` (any type). Activity metric per family: duels → `duel_fights`, combat → `fights`, bounty → `checks`.
| Slug | Metric(s) | agg | Gate | Note |
|---|---|---|---|---|
| `bounty_caps` | captures | Σ | | |
| `systems_checked` | checks | Σ | | |
| `systems_checked_no_capture` | checks, captures | Σ | | qualified iff captures == 0 |
| `duels_won` / `duels_lost` / `duels_fought` | duel outcome | Σ | S | fought counts stalemates |
| `duel_credits_won` / `duel_credits_lost` | stakes | Σ | S | |
| `kills` | captures + duel wins | Σ | S | |
| `kills_by_weapon` (`weapon`) | killing_blow_subtype == weapon | Σ | S | **Scorable today:** `primary`, `turret`, `nuke`, `rocket`, `missile`, `cluster-missile` — the resolver makes `emp-bomb` a no-op and `shock-blast`/`ionizing-missile` deal 0 HP, so they can't be killing blows and are not offered. `secondary_fired` likewise excludes `emp-bomb` (never emits a fire event). | `weapon` ∈ `primary`, `turret` (turret sources `auto`/`manual` are normalised to `turret` at attribution), or a secondary subtype (`nuke`, `rocket`, `missile`, `cluster-missile`, `emp-bomb`, `shock-blast`, `ionizing-missile`) |
| `secondary_fired` (`subtype`) | `secondary_fired[...]` | Σ | S | subtypes: nuke, rocket, missile, cluster-missile, emp-bomb, shock-blast ("most nukes fired" is `subtype=nuke`) |
| `module_activations` (`module`) | `module_activations[...]` | Σ | S | gated by `_ACTIVATION_MODULES` allowlist (`combat_resolver.py` ~L155) |
| `fights_fought` | fights | Σ | S | |
| `longest_battle_won` / `_lost` | `duration_ticks` | max | S,F | |
| `max_damage_dealt_fight` / `max_damage_taken_fight` | absorbed damage | max | S,F | ceiling scales with opponent HP → use D |
| `max_single_nuke_damage` | `max_nuke_absorbed` | max | S,F | needs the summary extension |
| `total_damage_dealt`, `shots_fired` | Σ | Σ | S | volume stats need no battle-count gate; `shots_fired` excludes nukes/shock-blasts |
| `avg_accuracy` | hits / shots | ratio | S,F | division bias → use D |
**Deferred:** `bounty_credits_earned`, `credits_earned`, `xp_gained` — tier-skewed payouts; revisit only as D-scoped.

## 5. Lifecycle, jobs, payout
`draft → (scheduled →) active → ended | cancelled`; `draft`/`scheduled` deletable.
**One recurring bot-core job, `event_tick`** (every 5 min; registered in the default recurring list in `main.py:~115` next to `bounty_failsafe_cleanup_default`, so `/scheduler/reset` re-creates it; dispatched via `utils/job_executor.py`): starts `scheduled` events past `scheduled_start_at`, ends `active` events past `ends_at`. No one-time jobs → survives `/scheduler/reset` and downtime for free. `# ponytail: ±5 min start/end precision; add one-time jobs if anyone notices.`
**Start** (tick or `/admin_event_start`): validate prizes non-overlapping and `discussion_channel_id` set (a tick-driven start that fails validation logs and retries next tick); the announcements role is **optional** — NULL just means no mention; stamp `started_at`/`ends_at`; return the announcement for the caller to post after commit.
**Payout** (tick or `/admin_event_end payout:Yes`): take qualified, non-disqualified entries; **drop players no longer in the guild** (user: must be present to win — in the guild, not online) — one gateway call, `GET /guilds/{guild_id}/members` (`discord-gateway/src/api/routers/guilds.py:98`), via the same bot-core→gateway HTTP path the announcement executors use; rank by `value` with **competition ranking** (`RANK()` — tied players share the rank; 3-way tie for 1st = ranks 1,1,1,4) and **every tied player receives that rank's prize in full** (user: three tied for 1st with a Specter as 1st prize → three Specters minted). Same for Top-N slots: a tie straddling the boundary is in. Slots skipped by a tie (2nd/3rd after a 3-way tie for 1st) go unawarded; mint per slot through the admin-give **service** paths (§8) inside per-slot try/except, status → `event_results`; write results; announce winners + participation count; `ended`. State transition in the same transaction = idempotent. Tick-driven ends audit with `user_id = event.created_by_user_id` (no admin actor); acceptable, noted.
**Announcement/leaderboard content (user, 2026-09-04):** the end announcement lists each placed player **with the prize they received** (from `event_results.prize`, e.g. `1st · SamAccountX — 500 credits + participation 50 credits`), plus the participation count; the per-event leaderboard (`/event_leaderboard event:`) shows a **Prizes** section listing every slot (1st…, Top N…, Participation…). **Announcements** are posted by the caller **after the transaction commits** (`start_event`/`end_event` return the announcement; the tick and the API router post it post-commit) — posting before commit would re-run payout on the next tick if the commit failed. Target `guild_config.discussion_channel_id`, with `<@&event_announcements_role_id>` in **`text_content`** (Discord ignores role mentions in embeds — `shop_announcement.py:144–147`); NULL role → no mention. Start / end / cancelled.
**Medals** (§ leaderboard): derived from `event_results.rank` (1/2/3 = 🥇🥈🥉), Olympic ordering (golds, silvers, bronzes, then events entered). Per-type = same query filtered by `type_slug`.

## 6. Commands (flat `admin_*` / player names, repo convention)
Selector = shared autocomplete over a per-guild events cache in `autocomplete_state` + warm job, invalidated on admin mutation (existing pattern, `utils/autocomplete_warm.py`). State filter per command: prize add/remove → `draft` (+`active` for add); start → `draft`/`scheduled`; end → `active`; delete → `draft`/`scheduled`/`cancelled`; `/events`, `/event_leaderboard` → `scheduled`/`active`/`ended` ≤ 7 d. `type` at create = autocomplete over the registry, fetched once from bot-core `GET /events/types` into a never-expiring cache (the gateway cannot import bot-core); no refresh job.
| Command | Notes |
|---|---|
| `/admin_event_create type duration_days [params]` | draft |
| `/admin_event_view event` | `draft`/`scheduled` selector; full embed (rules, settings, timing, prizes) — the "is this ready to start?" check. Added 2026-09-05. |
| `/admin_event_edit event [duration_days] [division] [min_fights] [subtype\|module\|weapon]` | `draft`/`scheduled` only (`PATCH /events/{id}`; `params` replaced wholesale after the same validation as create; `division:all` clears the filter). Added 2026-09-05 after dev-guild review — admins could seed prizes but not fix a wrong duration/division without delete + recreate. |
| `/admin_event_add_prize event place type [item] qty` | `place` choices: `1st…10th`, `Top N` (+`top_n`), `Participation`; `item` autocomplete filtered by `type` via `interaction.namespace`; validates catalog + overlap on add. Drafts + active. |
| `/admin_event_remove_prize event place` | drafts |
| `/admin_event_start event [at] [utc_offset]` | now, or scheduled; re-running on a `scheduled` event replaces the schedule. `at` = `YYYY-MM-DD HH:MM` (`datetime.strptime`, one format); `utc_offset` = 25 static choices UTC−12…+12, UTC if omitted; reject past / > 90 d; ephemeral confirm shows `<t:…:F>` (viewer-local) behind the shared `ConfirmView`. |
| `/admin_event_end event payout:Yes/No [reason]` | active; confirm button |
| `/admin_event_delete event` | `draft`/`scheduled`/`cancelled` only — `ended` events are history (`event_results` feed medals); their metric rows age out via retention |
| `/admin_event_list [state]` | |
| `/admin_sync_roles [dry_run]` | force the role sync for this guild, counts back |
| `/events [event]` | live ("ends <t:R>") + scheduled ("starts <t:R>"); with `event` → rules/prizes/timestamps. **Also runs `_sync_player_notification_roles` for the caller.** |
| `/event_leaderboard [event] [type]` | no args = all-time medals; `event` = standings (top 10/page + caller's rank; **only qualified players are ranked**, same rule as payout; gated entries footnoted); `type` = medals for that type |

## 7. Event Announcements role (Kibbles) — opt-in default, opt-out
Third instance of the Shop Announcements pattern (migration `0003` precedent). Touchpoints: `guild_setup.py` find-or-create (clone of ~L433–458, `mentionable=True`) + `ADMIN.md:82`; `guild_configs.event_announcements_role_id` through the same files that carry `shop_announcements_role_id`; `players.event_notifications_enabled` (`server_default="true"`, precedent revision 0019); `/notifications` third choice (`playerCog.py:1022`); projection block in `_sync_player_notification_roles` (`:1312`, covers `/profile` = `/register`); `/unregister` `extra_role_ids` (`:1209`); `/admin_uninstall` id list + `_BOUNTYBOT_ROLE_NAMES`; setup embed + `/admin_check`.
**Propagation:** `/profile`, `/notifications`, `/events`, gateway job `notification-role-sync` (12 h + one-shot ~60 s after startup, registered in `register_warm_jobs`, **add-only**, all three roles, creates the event role when NULL), `/admin_sync_roles`.
**Uninstall vs config reset (user):** uninstall = full sweep — events, prizes, entries, results, role, jobs; config reset = values only, running events untouched (like bounties survive a spawn-interval reset). Reset nulls role/channel ids as it does today (`ADMIN.md:686`); the 12 h sync job relinks the event role by name (find-or-create) without admin action.

## 8. Minting paths to reuse
credits `PUT /admin/players/credits` (`/admin_player add_credits`, `adminCog.py:622`) · item `POST /admin/give-item` (`admin.py:917`) · ship `POST /admin/give-ship` (`admin.py:1113`). Call the service layer, audit `event_payout` (`admin_audit_log`: `action`, `resource_type="event"`, `resource_id`, `details`, `status`). Every admin event command audits the same way.

## 9. Verified gotchas
1. Bounty combat logs pruned at 48 h (`combat_log.py`) → accumulate at resolution, never retro-query. 2. Damage in summary is **absorbed** (overkill excluded, `combat_resolver.py` ~L1025–1045). 3. `shots_fired` excludes nukes/shock-blasts. 4. Duel stalemate changes no stats (`duel_service.py:392`). 5. `_ACTIVATION_MODULES` allowlist coupling. 6. `/admin_give_item` has no type filter (`adminCog.py:2480`) — the prize cascade is the first. 7. discord.py 2.7.1; no date picker anywhere in Discord's bot UI. 8. Scheduler jobstore is persistent (`main.py:463`) but `/scheduler/reset` drops one-time jobs. 9. No timezone config exists anywhere in the stack.

## 10. Registry + checks
```python
@dataclass(frozen=True)
class EventType:
    slug: str; display_name: str; rules_text: str; category: str
    metrics: dict[str, str]                # {"nukes": "sum"} | {"duration": "max"} | {"hits": "sum", "shots": "sum"}
    value: Callable[[dict], float] | None = None   # rows → rank key; None = the single metric
    qualified: Callable[[dict], bool] | None = None  # e.g. lambda m: m.get("captures", 0) == 0
    min_fights: int | None = None          # 10 on max/ratio types
    fmt: Callable[[float], str] = str
EVENT_TYPES: dict[str, EventType] = {...}   # DB stores slug + params only
```
Checks (as built): bot-core `tests/services/test_event_service.py` (record/standings/hooks/queries, real SQLite), `tests/services/test_event_payout.py` (ties, ranges, forfeits, idempotency, partial failure, announcement payload shape, gateway failures), `tests/test_event_tick_executor.py` (per-event transactions, announce-after-commit order), `tests/api/test_events_router.py` (validation, 403/409 gates, mocked service), `tests/test_migration_0035_custom_events.py`, `tests/test_migration_0036_event_announcements_role.py`; gateway `tests/cogs/test_events_selector.py`, `tests/api/test_events_push.py`, `tests/utils/test_events_warm.py`, `tests/cogs/test_notifications_event.py`, `tests/utils/test_guild_setup_event_role.py`, `tests/utils/test_notification_role_sync.py`. Live: dev stack rebuilt from the branch, slash commands force-synced (83 commands, 25 offset choices accepted).

## 11. Open
- Per-opponent daily duel cap: **skip** until abuse is seen; the 1000-credit stakes floor is the guard.
- `/admin_event_update`: not built; delete + recreate a draft. Add when an admin asks for "extend".

## 12. Ponytail cut list (v1 → v2)
| Cut | Why | Add back when |
|---|---|---|
| Δ-counter snapshots, `baseline`, bulk enrol at start | one mechanism (hooks) covers every type; matches soft enrolment | never — hooks are strictly simpler |
| One-time `event_start`/`event_end` jobs + failsafe | one 5-min tick does both and survives resets | someone needs to-the-second start/end |
| `/admin_event_update`, `/admin_event_show` | delete+recreate; list + leaderboard cover reads | an admin asks |
| `title`, `announcement_message_id`, `slot_kind`, `tier_at_start`, `disqualified_reason`, `Ranks A–B` slot | unused / derivable | a concrete need |
| Relative date formats, `dateparser`, guild default UTC offset column | one strptime format + UTC default + confirm timestamp | half-hour zones or admins complain |
| Discord Server Events mirroring (Option B) | needs Manage Events + lifecycle sync | players ask for native reminders |
| `/event_rules`, `/event_me`, `/profile` medal tally, records line, category filter | folded or unrequested | requested |
| Role-mention fallback to Bounty Hunter | role job creates the role within ~60 s | never |
| Re-baselining on admin reset | events don't read Player counters | never |
| Prize "vanished" handling beyond per-slot try/except | catalog is static (user) | never |
| Per-event `min_stakes` param | guild config covers it | an admin asks for per-event values |
| (reinstated 2026-09-04) per-event `min_fights` | the user's lossless-event design needs it | — |
| `metrics` JSONB + `value` column + `entries` table (`tier_at_first_activity`, `disqualified`) | one narrow `game_event_metrics` table with a native upsert; division enforced at write time; tier change deletes rows | never — JSON read-modify-write under concurrent fights is a race farm |
| `nukes_fired` slug | it is `secondary_fired(subtype=nuke)` | never |
| `min_fights` gate on Σ types | volume stats can't be won by one lucky fight | never |
| ORM `before_flush` tier listener | two explicit calls in `player_service` are boring and greppable | a third tier-assignment site appears |

## 13. Env vars introduced
`AUTOCOMPLETE_EVENTS_REFRESH_MINUTES` (gateway, default 10) · `NOTIFICATION_ROLE_SYNC_HOURS` (gateway, default 12) · `BOUNTYBOT_EVENT_METRICS_RETENTION_DAYS` (bot-core, default 30). Guild-level: `event_min_duel_stakes` (default 1000) via `/admin_config`.
