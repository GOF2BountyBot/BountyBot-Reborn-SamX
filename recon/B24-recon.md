# B.24 Recon — `/route` does not visually highlight "Recently Spotted" system

**Recon completed**: 2026-04-28 by developer (read-only investigation)  
**HEAD at time of recon**: commit `815cd59` (post-rebuild stack)

---

## 1. Entry Points Investigated

| File | Lines | Role |
|---|---|---|
| `services/discord-gateway/src/cogs/bountyCog.py` | 667–750 | `/route` slash command handler + inline embed builder |
| `services/bot-core/src/api/routers/bounties.py` | 261–278 | `GET /bounties/{bounty_id}/route` endpoint |
| `services/bot-core/src/utils/bounty_announcement_payload.py` | 157–263 | Announcement embed builder — route field rendering |
| `services/bot-core/src/services/bounty_service.py` | 1248–1283 | `check_system` — where `recently_spotted` is computed |
| `services/discord-gateway/src/api/routers/announcements.py` | 77–201 | Gateway edit endpoint — image preservation (B.13 fix) |
| `services/bot-core/src/persist/models/bounty.py` | 35–36 | `checked` field definition |

---

## 2. Data Model

`Bounty.checked` (JSON column, line 36 of `bounty.py`):
```
{ system_name: player_id }
```
- `-1` → unchecked sentinel (initial state from spawn)
- `player_id (int > 0)` → checked by that player

**There is no "recently spotted" field in the DB.** The state is a *computed property* derived at render time from:
- `bounty.answer` (the system where the criminal actually is)
- `bounty.route` (ordered list of systems)
- The distance formula: `answer_idx - checked_system_idx ∈ [1, 2]` → recently_spotted

`bounty.answer` is **intentionally hidden** from the public `/route` response to prevent players from spoiling the bounty answer.

---

## 3. `/route` Command Handler — Full Walk-Through

**File**: `services/discord-gateway/src/cogs/bountyCog.py`  
**Command defined**: Line 667 (`@app_commands.command(name="route", ...)`)  
**Embed builder**: Inline (lines 699–723), **not** in a shared module  

### API call
```
GET /api/v1/bounties/{bounty_id}/route
```
**Returns** (`bounties.py:271–278`):
```json
{
  "bounty_id": 2247,
  "criminal_name": "Hongar Meton",
  "division": "bronze",
  "route": ["Pescal Inartu", "Buntta", "V'Ikka", "S'Kolptorr", "K'Ontrr", "Wah'Norr"],
  "checked": {"Buntta": 402296276617527306, "S'Kolptorr": 402296276617527306},
  "status": "active"
}
```

Note: `checked` contains raw player_ids, not status strings. `answer` is **not** returned.

### Rendering logic (lines 708–714)
```python
for i, system_name in enumerate(route_systems, start=1):
    if checked.get(system_name, -1) != -1:
        route_lines.append(f"{i}. ~~{system_name}~~ ✅")
    else:
        route_lines.append(f"{i}. {system_name}")
```

**Binary check only**: any non-`-1` value → strikethrough + ✅. No proximity calculation.

The cog receives `checked` (player-id map) and `route` but **never computes the "recently spotted" status**, because:
1. It lacks `bounty.answer` (not returned by the API endpoint)
2. There is no shared utility imported into this path

---

## 4. Announcement Embed Builder — Full Walk-Through

**File**: `services/bot-core/src/utils/bounty_announcement_payload.py`  
**Trigger path**: `bounty_service._edit_bounty_announcement()` (line 1344) → `build_bounty_announcement_request()` (line 41) → `_build_suffix_fields()` (line 157) → `_build_route_value()` (line 210)

### `_project_checked()` (lines 168–207)
Translates raw `checked` (player-id map) into a **3-state status map**:
```
"found"            → answer system has been hit  
"recently_spotted" → system is 1 or 2 stops before the answer  
"checked"          → checked but not answer and not recently-spotted  
```

Uses `bounty.answer` + `bounty.route` to compute the distance. Identical formula to `check_system()` in `bounty_service.py:1259–1261`.

### `_build_route_value()` (lines 210–235)
Markdown per status:
```
"checked"          → ~~system~~           (strikethrough)
"recently_spotted" → **~~system~~**       (bold + strikethrough)
"found"            → **system**           (bold)
else (unchecked)   → system               (plain)
```

### Route into the gateway
The structured payload (including the pre-rendered route value string) is POSTed to `GET /api/v1/announcements/bounty/channel/{cid}` (or `PUT` for edits), where `announcements.py:192–200` delegates to `build_loadout_embed()`.

---

## 5. Rendering Comparison Table

| System status | Announcement embed | `/route` cog | Gap? |
|---|---|---|---|
| Not checked | plain | plain | ✅ same |
| Checked-not-here | `~~system~~` | `~~system~~ ✅` | minor (extra emoji) |
| Recently spotted (1–2 stops from answer) | `**~~system~~**` | `~~system~~ ✅` | ❌ **BUG — identical rendering to checked-not-here** |
| Found / correct | `**system**` | N/A (bounty resolves) | n/a |

---

## 6. Source-of-Truth Analysis

**Two independent, duplicated implementations. No shared source of truth.**

| Location | Status logic | Has recently_spotted? |
|---|---|---|
| `bounty_announcement_payload._project_checked()` | 3-state (found/recently_spotted/checked) | ✅ Yes |
| `bountyCog.route()` embed (inline) | 2-state (checked/unchecked) | ❌ No |

The `_project_checked()` function is specific to bot-core's announcement pipeline and is not exposed via any API endpoint. The `/route` endpoint (`bounties.py:261–278`) returns raw `bounty.checked` (player-id map) without status projection.

---

## 7. B.13 Cross-Reference

B.13 fixed the announcement embed edit to preserve the route-map image URL on state-transition edits. The fix lives in `announcements.py:141–154` (gateway edit handler), which reads the existing message's image URL and carries it forward when `payload.metadata.image_url is None`.

`_edit_bounty_announcement()` in `bounty_service.py` calls `build_bounty_announcement_request()` with `route_map_url=None`, triggering the B.13 preservation logic.

**Conclusion**: B.13 is orthogonal to B.24. The announcement edit **does** correctly re-render "recently_spotted" status via `_project_checked()` on every `/check` that triggers an edit. The announcement embed correctly showed Buntta as `**~~Buntta~~**` (bold+strikethrough) after it was spotted. B.13 ensures the route map image is preserved on that edit. Neither defect is a dependency of the other.

---

## 8. Tests

### `/route` cog tests (`tests/cogs/test_bountyCog.py:TestRouteCommand`)
- ✅ Tests checked vs unchecked strikethrough rendering
- ✅ Tests 404 / API error / generic exception error handling
- ✅ Tests division display in embed description
- ❌ **No test for "recently spotted" rendering distinction** — all checked systems treated the same

### `/route` API endpoint tests (`tests/api/test_bounty_router.py:TestGetBountyRoute`)
- ✅ Tests success with raw `checked` dict (player-id map)
- ✅ Tests 404
- ✅ Tests division field in response
- ❌ **No test for `system_statuses` / recently-spotted projection** (field doesn't exist yet)

### Announcement builder tests (`tests/test_bounty_announcement_payload.py`)
- ✅ Tests `_project_checked()` for recently_spotted (distance 1, 2, 3+ cases)
- ✅ Tests `_build_route_value()` with bold+strikethrough for recently_spotted
- ✅ Tests `_build_checked_systems_value()` grouping by status

### Bounty service tests (`tests/services/test_bounty_service.py`)
- ✅ Tests `recently_spotted=True` for distance 1 and 2
- ✅ Tests `recently_spotted=False` for distance ≥ 3 and for ahead-of-answer case
- (These test the `/check` service, not the `/route` endpoint)

---

## 9. Recommended Fix

### Option A (preferred): Add `system_statuses` to the `/route` API response

**bot-core** (`bounties.py:261–278`): Compute and return a pre-projected status map (without exposing `answer`). Map "found" to "checked" in the projected output so the answer system is not identifiable:

```python
# In get_bounty_route():
from utils.bounty_announcement_payload import _project_checked
system_statuses = _project_checked(bounty)
# Mask "found" → "checked" to prevent answer leakage
if system_statuses:
    system_statuses = {k: ("checked" if v == "found" else v) for k, v in system_statuses.items()}
return {
    ...existing fields...,
    "system_statuses": system_statuses,
}
```

**discord-gateway** (`bountyCog.py:708–714`): Use `system_statuses` for rendering:
```python
system_statuses = data.get("system_statuses") or {}
for i, system_name in enumerate(route_systems, start=1):
    status = system_statuses.get(system_name)
    if status == "recently_spotted":
        route_lines.append(f"{i}. **~~{system_name}~~** 🔍")
    elif status in ("checked", "found"):
        route_lines.append(f"{i}. ~~{system_name}~~ ✅")
    else:
        route_lines.append(f"{i}. {system_name}")
```

### Fix scope: **Surgical**
- 2 files changed: `bounties.py` (API endpoint, ~8 lines) + `bountyCog.py` (cog render logic, ~6 lines)
- New tests: `/route` endpoint test for `system_statuses` field, cog test for recently_spotted rendering
- No DB schema changes, no new services

---

## 10. Open Questions

1. **Icon choice**: What emoji/markdown should `/route` use for "recently spotted"? The announcement uses `**~~system~~**` (bold+strikethrough). For `/route`, using a 🔍 suffix (as suggested above) or matching `**~~system~~**` are both reasonable. User preference needed.
2. **"found" masking**: Should a captured bounty's `/route` response show the answer system as "found" (bold) or "checked" (strikethrough+✅)? If the bounty is already `status=captured/expired`, revealing the answer doesn't matter. A conditional could expose "found" only for non-active bounties.
3. **Backward compatibility**: Adding `system_statuses` to the route response is additive and non-breaking (existing cog code falls through to the `else` branch safely if the field is missing).
