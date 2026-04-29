# Package F Design — HTTP Error Helper Theme (B.31b)

**Design date**: 2026-04-29
**Phase**: Design (no code changes this dispatch)
**Targets**: 53 occurrences of `f"❌ API Error: {e}"` across 9 cogs in `services/discord-gateway/src/cogs/`
**Verified site count at design time**: 53 source-code matches via grep `API Error: \{e\}` (54 minus 1 occurrence in `cogs/AGENTS.md` documentation)

---

## Goal

Replace every raw-`HTTPStatusError`-stringification user message with a sanitized, status-aware
embed produced by a single helper. The helper must:

1. Never leak internal hostnames / ports / paths (`http://bot-core:8000/...`).
2. Never leak MDN status-code documentation links (`https://developer.mozilla.org/...`).
3. Surface the status code and a friendly canned phrase, plus the FastAPI `detail` field
   (which is already-sanitized by bot-core) when present.
4. Handle the post-defer race (Discord followup may raise transient `discord.HTTPException`).
5. Be composable with existing pre-checks (e.g. `_is_guild_not_configured`, explicit `404`
   branches in `schedulerCog`, etc.) — those branches stay; the helper replaces only the
   "everything-else" `f"❌ API Error: {e}"` line.

Non-goals: changing what bot-core returns, redesigning logging, introducing custom exception
classes. See **Out-of-scope decisions** below.

---

## Helper API (single recommended signature)

**Module path**: `services/discord-gateway/src/cogs/_shared/http_error_handler.py`

The `_shared/` directory already exists, has `__init__.py`, holds cog-adjacent helpers
(`embed_pagination.py`, `loadout_embed.py`), and is correctly excluded from the bot's cog
auto-loader (which inspects only top-level `.py` files in `cogs/`, not subdirectories — see
`cogs/AGENTS.md` "Auto-Discovery and Loading"). It is the right home.

### Recommended signature (the only one calling sites should use)

```python
async def report_api_error(
    interaction: discord.Interaction,
    exc: Exception,
    *,
    action_label: str | None = None,
    detail_override: dict[int, str] | None = None,
) -> None:
    """
    Send a sanitized ephemeral error reply for a failed bot-core / blender-service call.

    Safe to call after defer(); swallows discord.HTTPException so a followup race
    cannot bubble out and trigger Discord's "This interaction failed" UX.

    Args:
        interaction: The Discord interaction. Must already have been deferred OR be
            in a state where followup.send() is valid; the helper does not call defer().
        exc: The exception caught. Typically httpx.HTTPStatusError. Other types
            (httpx.RequestError, generic Exception) are accepted and produce a
            generic "service unreachable" / "unexpected error" message.
        action_label: Optional human verb-phrase for the failed action (e.g.
            "fetch shop", "spawn bounty"). Used as embed title context only;
            never echoed into a URL or path. Keep short — ≤ 40 chars.
        detail_override: Optional {status_code: friendly_message} mapping that wins
            over the default canned message for that status code. Use to specialize
            (e.g. {404: "Job not found"} instead of generic "Not found").

    Side effect:
        Calls interaction.followup.send(embed=..., ephemeral=True) inside a
        contextlib.suppress(discord.HTTPException) block.
    """
```

### Why this signature

- **One call replaces one line.** The migration is a near-mechanical 1:1 substitution at
  every site, which is what we want for a 53-site sweep.
- **Returns `None`, sends directly.** The alternative (return an `Embed` and let each site
  call `followup.send`) would require 53 edits at two lines each instead of one. Since 100%
  of sites today use `interaction.followup.send(..., ephemeral=True)`, baking that into
  the helper is correct.
- **Race-safety lives inside the helper**, not at every call site. This bakes the B.27 fix
  pattern in by default — future cogs that use the helper inherit the safety.
- **`action_label` is optional** so the simplest migration (just pass `interaction, e`) is
  always available. Sites where context is helpful (e.g. `admin_setup`, scheduler ops) can
  pass an action label. We do **not** require every site to be specialized.
- **`detail_override` is optional** so sites with strong domain knowledge of "what 404 means
  here" can specialize without forking the helper.

### Alternatives considered (briefly)

- `build_error_embed(exc, *, action_label=None) -> discord.Embed` — pure function,
  caller handles send. **Rejected**: doubles the per-site edit count and forces every site
  to remember `ephemeral=True` and the suppress pattern. Loses the race-safety win.
- A class hierarchy of error formatters. **Rejected**: 50 LOC of behaviour does not need a
  class. Function-based API per the dispatch's "favor simple over clever" rule.
- A decorator wrapping the entire command. **Rejected**: existing `except` blocks have
  pre-checks (404 specials, guild-not-configured) that don't fit a uniform decorator;
  retrofitting 53 sites into a decorator pattern is far more invasive than 53 line swaps.

---

## Status-code mapping policy

Default mapping (used when `detail_override` is not supplied or does not match):

| Status | Friendly canned message            | Embed color (severity) |
|--------|-------------------------------------|------------------------|
| 400    | "Invalid request."                  | red (error)            |
| 401    | "Permission denied."                | red (error)            |
| 403    | "Permission denied."                | red (error)            |
| 404    | "Not found."                        | red (error)            |
| 409    | "Conflict — please retry."          | orange (warning)       |
| 422    | "Invalid input."                    | red (error)            |
| 429    | "Rate limited — please wait."       | orange (warning)       |
| 5xx    | "Service issue, please try again."  | orange (warning)       |
| other  | "Unexpected error."                 | red (error)            |

**Detail extraction rules** (applied AFTER selecting the canned message above):

1. If `exc` is `httpx.HTTPStatusError` AND `exc.response` has a JSON body AND that body has
   a string `detail` field, the detail string is appended:
   `"{canned}: {detail}"` (truncated at 200 chars to fit Discord embed description sanely).
2. If the JSON body has a `detail` that is a list (FastAPI/Pydantic 422 shape), it is
   compressed to "field1: msg1; field2: msg2" up to 200 chars.
3. If the body is not JSON, or `detail` is missing/non-string-non-list, the canned message
   is used alone.
4. If `exc` is `httpx.RequestError` (no response — connection refused, DNS, timeout):
   message is "Service unreachable, please try again." (orange).
5. If `exc` is anything else: message is "Unexpected error." (red). The exception type is
   logged via `flogger` but never shown to the user.

**`detail_override` interaction**: Calling sites pass a `dict[int, str]`. If
`exc.response.status_code` matches a key, that value REPLACES the canned message for that
status. Detail-from-body extraction rules above still apply on top of the override (so
`detail_override={404: "Job not found"}` plus a server response `{"detail": "scheduler"}`
yields `"Job not found: scheduler"`). Calling sites that want to suppress detail-append
can pass an override that already includes the detail, but the simple case is just
`{404: "Job not found"}`.

### Severity → embed style

```python
# pseudocode within helper
title = "❌ Error" if severity == "error" else "⚠️ Warning"
color = discord.Color.red() if severity == "error" else discord.Color.orange()
if action_label:
    title = f"{title}: {action_label}"
embed = discord.Embed(title=title, description=message, color=color)
```

The leading "❌" / "⚠️" emoji is intentional — current code uses "❌" for all API errors;
we are upgrading to "⚠️" for transient/server-side issues to match B.27's existing pattern
of "⚠️ An error occurred."

---

## Sanitization rules

Implemented as a single `_sanitize(text: str) -> str` private helper. Applied to the
final embed description before send, as defense-in-depth (the canned messages and
`detail` field should already be clean, but the user might pass a string we don't fully
control via `detail_override`).

Rules:

1. Strip any substring matching `r"https?://[^\s'\"]+"` — removes both internal
   `http://bot-core:8000/...` URLs and external links (MDN). Replace with empty string,
   then collapse adjacent whitespace.
2. Strip any line containing the literal phrase `For more information check:`
   (httpx's MDN-link prefix). Match is case-insensitive, line-granular.
3. Strip any leading/trailing whitespace and collapse internal runs of whitespace to
   single spaces.
4. Truncate to 1000 chars (Discord embed description limit is 4096; 1000 is plenty and
   keeps logs sane).

**Note**: We sanitize the FINAL message, not the raw `str(exc)`. The helper's normal
path never includes `str(exc)` in user-facing output — it uses canned phrases plus the
JSON `detail` field. The sanitizer is a belt-and-braces guard, not the primary defense.
The primary defense is "we never put `str(exc)` into the user-visible string."

---

## Race-safe send pattern

The helper wraps the actual send in `contextlib.suppress(discord.HTTPException)`. This
matches the B.27 fix (already applied to all six scheduler error handlers in commit
`ec42c4d`) and prevents transient Discord API hiccups from causing "This interaction
failed" after a defer. Implementation sketch:

```python
import contextlib
import discord

async def report_api_error(interaction, exc, *, action_label=None, detail_override=None):
    embed = _build_embed(exc, action_label=action_label, detail_override=detail_override)
    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(embed=embed, ephemeral=True)
```

The helper assumes the caller has already called `defer()` (which is true at every
existing site — all 53 are post-defer). If a caller has NOT deferred, `followup.send`
raises `discord.InteractionResponded`-class errors that fall under `discord.HTTPException`
and will be suppressed. We will not silently fall back to `interaction.response.send_message`
in that case — surfacing the bug via logging is preferable. The helper logs (via
`flogger.exception` from a logger named `discord-gateway-http-error-helper`) only when
the send raises, not on every call.

---

## Migration plan

### Identification

Use `grep -rn 'API Error: {e}' services/discord-gateway/src/cogs/` to enumerate all
sites. Confirmed at design time:

| Cog | Sites | Notes |
|---|---|---|
| `adminCog.py` | 19 | Largest concentration; many already 16-space indent inside `else:` after specific status checks |
| `inventoryCog.py` | 6 | Mix of bare and `else:`-branched |
| `schedulerCog.py` | 6 | All inside `else:` after explicit 404/503 handling; **must preserve those branches** |
| `playerCog.py` | 5 | Some inside `if/else` for guild-not-configured |
| `shopCog.py` | 4 | All inside `else:` after `_is_guild_not_configured(e)` check; **preserve** |
| `bountyCog.py` | 4 | Mixed |
| `shipsCog.py` | 4 | Mixed |
| `duelCog.py` | 3 | Mixed |
| `aboutCog.py` | 2 | Mixed |
| **Total** | **53** | |

(The `cogs/AGENTS.md` template snippet also contains the pattern; this is documentation,
not source. It will be updated separately as part of the migration — see "Implementation
order" §6.)

### Replacement rules per site

For each occurrence:

1. **Locate the enclosing `except httpx.HTTPStatusError as e:` block.**
2. **Preserve all sibling branches** (`if e.response.status_code == 404`, `elif ... == 503`,
   `if _is_guild_not_configured(e)`, etc.). These branches encode site-specific knowledge
   and should NOT be collapsed into the helper.
3. **Replace the line `await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)`
   with `await report_api_error(interaction, e)`.**
4. **Add the import** at the top of the file:
   `from cogs._shared.http_error_handler import report_api_error`.
   (Path is the established import style — confirm by inspecting how `loadout_embed` is
   imported; same module path family.)
5. **Optional specialization**: Where the existing branches above this line indicate the
   command's domain (e.g. scheduler ops with job_id), pass `action_label="scheduler view"`.
   This is OPTIONAL polish; the unflavored `report_api_error(interaction, e)` is already
   correct. Spec recommendation: do specialization only where the surrounding flogger.error
   already names the action — copy the same label string to keep them aligned. This is a
   low-effort win and avoids introducing new naming. Do NOT block migration on it.

### Non-`HTTPStatusError` exceptions

The 53 sites are all inside `except httpx.HTTPStatusError`. The sibling
`except Exception as e:` blocks already use a generic
`"⚠️ An error occurred."` message — those are NOT in scope for this package. Do NOT
modify them. The helper's "other exception" branch exists for completeness (so the helper
is callable with any exception type without crashing), not because we plan to migrate the
broad-except blocks. Leaving them alone is correct: they already have site-specific
contextual messages and are not URL-leak vectors.

If a future contributor wants to consolidate the broad-except blocks too, they can call
`report_api_error(interaction, e)` from those — the helper's "Unexpected error." default
will serve. That's a follow-up, not Package F's scope.

### Diff grouping

To produce review-friendly diffs, the implementation phase will:

- **Step 1 commit**: helper module + unit tests (no migration). Reviewable as a self-
  contained 50–100 LOC addition.
- **Step 2 commit**: migrate `adminCog.py` (19 sites — largest, riskiest cog).
- **Step 3 commit**: migrate the 8 remaining cogs in one commit (34 sites total). They
  share a uniform shape and reviewing them together is easier than 8 separate commits.
- **Step 4 commit**: update `cogs/AGENTS.md` template snippet + integration tests.

This 4-commit split keeps each diff under ~250 LOC and isolates a possible revert of the
helper from a possible revert of the migration.

---

## Test strategy

### Unit tests for the helper

File: `services/discord-gateway/tests/cogs/_shared/test_http_error_handler.py`

Coverage:

| Test | What it verifies |
|---|---|
| `test_404_default_message` | Status 404 → "Not found." in embed description |
| `test_404_with_detail_override` | Override `{404: "Job not found"}` produces "Job not found." |
| `test_400_with_detail_field` | 400 + JSON `{"detail": "Bad guild"}` → "Invalid request: Bad guild" |
| `test_422_with_pydantic_detail_list` | 422 + list-shaped detail compressed correctly |
| `test_5xx_uses_orange_severity` | 500/502/503 → orange embed (warning style) |
| `test_url_stripped_from_message` | Inject a URL into `detail_override` → URL gone from output |
| `test_mdn_link_stripped` | Inject `For more information check: https://developer.mozilla.org/...` → both phrase and URL gone |
| `test_request_error_unreachable` | `httpx.RequestError` → "Service unreachable" |
| `test_unexpected_exception_type` | Plain `RuntimeError` → "Unexpected error." |
| `test_send_failure_suppressed` | `interaction.followup.send` raising `discord.HTTPException` does NOT propagate |
| `test_action_label_in_title` | `action_label="scheduler view"` appears in embed title |
| `test_detail_truncation` | Detail >200 chars truncated cleanly |
| `test_non_json_response_body` | Status 500 + non-JSON body → canned 5xx message, no crash |

Target: ~13 unit tests, ~150 LOC. All use `MagicMock` / `AsyncMock`; no real httpx
required (build a minimal `httpx.HTTPStatusError` with a fake `Response`).

### Integration tests for cog wiring

For each of the 9 cogs, add or extend ONE existing test that exercises the
`HTTPStatusError` path to confirm the helper is wired in correctly. Spec:

- Pick the test most likely to already exist (e.g. `test_admin_setup_api_error` in
  `test_adminCog.py`). If it currently asserts `"❌ API Error" in sent_message`, update
  it to assert (a) the URL `bot-core:8000` is NOT in the sent payload, and (b) an embed
  was sent (vs a plain string).
- Do NOT add a new integration test per site. Per the dispatch: "Trust the helper's unit
  tests for behavior; cog tests just confirm wiring." One integration test per cog × 9
  cogs = 9 cog-level checks total.

If a cog has no existing test that exercises `HTTPStatusError`, add a single minimal one.
Likely to be needed for at most 2–3 cogs.

### Negative test for sanitizer

Include one test where a fully-formed `HTTPStatusError.__str__()` text (with both URL
and MDN link) is fed through the sanitizer directly. Asserts the leak-prone substrings
are gone. Defense-in-depth verification.

### What we are NOT testing

- All 53 sites individually. Over-test per dispatch.
- bot-core's behavior. Out of scope.
- Discord API behavior. We mock `interaction.followup.send`.

---

## Out-of-scope decisions

Considered and rejected for Package F:

| Item | Why rejected |
|---|---|
| Custom exception classes (`ApiClientError`, `ServiceUnavailableError`, etc.) | 50 LOC of behaviour doesn't justify a class hierarchy. The function API is sufficient and matches the simpler-is-better mandate. |
| Logging changes (structured logging, JSON logs, log enrichment) | Separate concern. Existing `flogger.error(...)` calls at most sites already log status code; the helper does not need to log the user-facing message. |
| Changing what bot-core returns (status code semantics, detail field shape) | Cross-service. Not in scope. The helper consumes whatever bot-core returns today (FastAPI standard `{"detail": ...}`). |
| Migrating the broad-except `except Exception` blocks at the same sites | They already use generic context-aware messages; not a URL-leak vector. Future polish, not Package F. |
| Replacing the `_is_guild_not_configured` helper with the new helper | The guild-not-configured message is domain-specific user education ("Ask an admin to run `/admin_setup`"), not a generic API error. Keep it as a separate sibling branch. |
| Centralizing every 404/503/429 special-case in cogs | Each command has unique semantics for "what does 404 mean here" — `Job not found` vs `Player not found` vs `Item not found`. The `detail_override` parameter is the right abstraction; pushing it into the helper would hard-code domain knowledge in the wrong place. |
| Adding the helper to cogs that don't currently leak (e.g. `setupCog`, `skinsCog`, `devCog`, `helpCog`, `healthCog`, `templateCog`, `testCog`) | They have no `f"❌ API Error: {e}"` sites. Migration is purely about the 53 known sites. The template's documentation snippet (`cogs/AGENTS.md`) WILL be updated to show the new pattern so future cogs get it right by default. |
| Renaming `_shared/` or restructuring cogs | Out of scope; the existing `_shared/` is fine. |

---

## Implementation order

The implementation phase (a follow-up dispatch to the developer agent — likely me) should
proceed in this order. Each step is committable independently.

1. **Create helper module** at `services/discord-gateway/src/cogs/_shared/http_error_handler.py`.
   Target: 50–80 LOC of source.
2. **Write unit tests** at `tests/cogs/_shared/test_http_error_handler.py`. Run: confirm
   green before any cog is touched.
3. **Migrate `adminCog.py`** (19 sites). The largest concentration; doing it first surfaces
   any pattern issues. Run `tests/cogs/test_adminCog.py` after.
4. **Migrate the remaining 8 cogs** (34 sites). Group commit; run full
   `pytest tests/cogs/` after.
5. **Update integration tests** (9 cogs × 1 test each) — replace `"❌ API Error" in ...`
   assertions with embed + URL-absence assertions.
6. **Update `cogs/AGENTS.md`** to show `report_api_error(interaction, e)` in the cog
   template snippet (currently shows the old pattern at line 160). Add a one-paragraph
   note under "Standard Error Handling" pointing to the helper.
7. **Run the full discord-gateway test suite** and `ruff check`. Confirm zero new failures.

Validation checklist after step 7:
- `grep -rn 'API Error: {e}' services/discord-gateway/src/cogs/*.py` returns zero matches.
- All cog tests pass.
- Unit tests for the helper pass.
- No new ruff warnings.
- The `cogs/AGENTS.md` template no longer shows the old pattern.

---

## Effort estimate (LOC + sites touched)

| Item | LOC added | LOC removed | Files touched |
|---|---|---|---|
| `cogs/_shared/http_error_handler.py` (new) | ~80 | 0 | 1 |
| `tests/cogs/_shared/test_http_error_handler.py` (new) | ~150 | 0 | 1 |
| `tests/cogs/_shared/__init__.py` (new, empty) | 0 | 0 | 1 |
| Cog migrations (53 sites × ~1 net-line + 1 import per file) | ~9 | ~53 | 9 |
| Cog integration test updates (9 cogs × ~3 LOC) | ~27 | ~10 | 9 |
| `cogs/AGENTS.md` template snippet update | ~5 | ~5 | 1 |
| **Totals** | **~270** | **~70** | **~22** |

Net change: ~+200 LOC of source + tests, with 53 sites migrated and 1 doc update.

The helper itself stays well under the 50–100 LOC band specified in the dispatch (target
~80 including docstrings; the meat is ~40 LOC).

---

## Appendix — sample before/after

**Before** (`adminCog.py:325–326`):
```python
except httpx.HTTPStatusError as e:
    await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
```

**After**:
```python
except httpx.HTTPStatusError as e:
    await report_api_error(interaction, e, action_label="guild setup")
```

**Before** (`schedulerCog.py:188–200`, has special branches — note we preserve them):
```python
except httpx.HTTPStatusError as e:
    if e.response.status_code == 404:
        await interaction.followup.send(f"❌ Job `{job_id}` not found.", ephemeral=True)
    elif e.response.status_code == 503:
        await interaction.followup.send(
            "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
        )
    else:
        flogger.error(
            f"/scheduler_view API error: guild={interaction.guild_id} user={interaction.user.id}"
            f" job_id={job_id} status={e.response.status_code}"
        )
        await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
```

**After** (only the inner `else:` line changes):
```python
except httpx.HTTPStatusError as e:
    if e.response.status_code == 404:
        await interaction.followup.send(f"❌ Job `{job_id}` not found.", ephemeral=True)
    elif e.response.status_code == 503:
        await interaction.followup.send(
            "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
        )
    else:
        flogger.error(
            f"/scheduler_view API error: guild={interaction.guild_id} user={interaction.user.id}"
            f" job_id={job_id} status={e.response.status_code}"
        )
        await report_api_error(interaction, e, action_label="scheduler view")
```

**Before** (`shopCog.py:247–251`, has guild-not-configured pre-check):
```python
except httpx.HTTPStatusError as e:
    if _is_guild_not_configured(e):
        await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
    else:
        await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
```

**After**:
```python
except httpx.HTTPStatusError as e:
    if _is_guild_not_configured(e):
        await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
    else:
        await report_api_error(interaction, e, action_label="shop")
```

In all three cases the change is one line. The pattern is uniform; the migration is
mechanical.

---

*Design completed: 2026-04-29 by architect (read-only design phase, no code modified)*
