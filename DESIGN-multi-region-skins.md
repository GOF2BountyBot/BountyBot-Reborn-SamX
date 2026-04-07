# Design: Multi-Region Skin System for /render_skin Command

**Author:** Architect Agent  
**Date:** 2026-04-07  
**Status:** Ready for implementation  

---

## 1. Overview

Ships in BountyBot have independently-skinnable regions defined by mask files (`mask1.jpg`, `mask2.jpg`, etc.) in their `.bbship` directory. The blender-service compositing endpoint **already supports** per-region textures, disabled regions, and multi-mask compositing. The gap is in the Discord slash command UX: the `/render_skin` and `/make_skin_texture` commands currently treat all ships as single-region, applying a skin uniformly as the base texture with no per-region control.

This design adds a UX layer in `skinsCog.py` that detects multi-region ships and offers users the choice between "apply everywhere" (simple path) and "customize per region" (power-user path).

---

## 2. Ship Region Distribution (Data Analysis)

| Mask Regions | Ship Count | Examples |
|-------------|------------|---------|
| 0 | 5 | Vol'Noor, Amboss, Wraith, Dark Angel, Vossk Battleship |
| 1 | 27 | Bloodstar, Betty, Kinzer, Ghost, Scimitar, H'Soc |
| 2 | 30 | Aegir, Phantom, Gryphon, Hera, Taipan, Hiro, Azov |
| 3 | 2 | Kinzer RS, Razor 6 |

Key insight: **30 ships (46%) have 2 mask regions** and **2 ships have 3**. These 32 ships benefit from per-region customization. The remaining 32 ships (0-1 regions) need zero UX changes.

---

## 3. API Changes

### 3.1 No Backend Changes Required

The existing endpoints provide everything needed:

**bot-core `GET /about/ships/{name}/render-info`** already returns:
```json
{
  "texture_regions": 3,
  "mask_paths": [
    "/app/data/game-objects/.../mask1.jpg",
    "/app/data/game-objects/.../mask2.jpg",
    "/app/data/game-objects/.../mask3.jpg"
  ],
  "compatible_skins": {"lava": "https://...", "onyx": "https://..."},
  "diffuse_path": "/app/data/game-objects/.../diffuse.bmp",
  "bbship_dir": "/app/data/game-objects/.../Ship.bbship",
  "model_path": "/app/data/game-objects/.../model.obj"
}
```

**blender-service `POST /textures/composite`** already accepts:
- `base_texture` (file upload) or `base_texture_path` (disk path)
- `region_textures[]` (multiple file uploads)
- `region_indices` (comma-separated mask indices)
- `disabled_regions` (comma-separated mask indices)
- `ship_path`, `square_mode`

### 3.2 Recommended Enhancement: Region Count in Preload (Optional)

Currently `_preload_ship_skins()` fetches ship objects for skin names. The `texture_regions` count is not cached during preload. For the feature to work, `_fetch_render_info()` is called at command time, which already retrieves the count. No preload change is strictly necessary.

**Optional optimization**: During preload, also store `texture_regions` per ship to enable region-aware autocomplete hints in the future (e.g., showing "(3 regions)" next to ship names). This is not required for v1.

---

## 4. UX Flow

### 4.1 Decision Tree

```
User runs /render_skin or /make_skin_texture
  │
  ├── Fetch render-info
  │     └── Extract: texture_regions, mask_paths, compatible_skins, diffuse_path
  │
  ├── Is skin == "Default" AND image == None? (default render)
  │     └── YES → Render with diffuse + skinBase only (current behavior, ALL ship types)
  │
  ├── Is len(mask_paths) <= 1?  (single-region or no-mask ship)
  │     └── YES → Apply skin/image as base_texture (current behavior, no extra prompts)
  │
  └── len(mask_paths) >= 2  (multi-region ship with skin/image provided)
        │
        ├── Show RegionModeView:
        │     [🎨 Apply to All Regions] [🔧 Customize Per Region] [❌ Cancel]
        │
        ├── "Apply to All" → Apply skin/image as base_texture (same as single-region)
        │
        └── "Customize Per Region" → Enter per-region flow:
              │
              ├── For each region 1..N, show RegionOptionSelect:
              │     [Select one: "Apply '{skin_name}' ✨" | "🎨 {other_skin_1}" | 
              │      "🎨 {other_skin_2}" | ... | "📤 Upload custom image" | 
              │      "🔲 Keep default look"]
              │
              ├── If user selects a skin → download skin bytes, store for this region
              ├── If user selects "Upload custom" → prompt wait_for("message"), store bytes
              ├── If user selects "Keep default" → mark region as "skip"
              │
              └── After all regions collected → build compositing request → composite → render
```

### 4.2 Flow: Single-Region Ship with Pre-Staged Skin

```
User: /render_skin ship:Bloodstar skin:lava
Bot: [defers, fetches render-info]
     → texture_regions=1, mask_paths=["mask1.jpg"]
     → Single region: skip region prompt
Bot: "🔧 Compositing textures…"
     → Calls /textures/composite with base_texture=lava_skin_bytes, base_texture_path=diffuse
Bot: "🎨 Rendering your ship… this may take a moment."
Bot: [render result + FormatDownloadView]
```

**No change from current behavior.**

### 4.3 Flow: Single-Region Ship with Custom Image

```
User: /render_skin ship:Betty image:<uploaded_file.png>
Bot: [defers, fetches render-info]
     → texture_regions=1, mask_paths=["mask1.jpg"]
     → Single region: skip region prompt
Bot: "🔧 Compositing textures…"
     → Calls /textures/composite with base_texture=uploaded_bytes
Bot: "🎨 Rendering…"
Bot: [render result]
```

**No change from current behavior.**

### 4.4 Flow: Multi-Region Ship with "Apply to All"

```
User: /render_skin ship:Kinzer RS skin:lava
Bot: [defers, fetches render-info]
     → texture_regions=3, mask_paths=["mask1.jpg", "mask2.jpg", "mask3.jpg"]
     → Multi-region detected!

Bot: "**Kinzer RS** has **3 skinnable regions**. How would you like to apply the 'lava' skin?"
     [🎨 Apply to All Regions] [🔧 Customize Per Region] [❌ Cancel]

User: clicks [Apply to All Regions]

Bot: "🔧 Compositing textures…"
     → Calls /textures/composite with:
       base_texture = lava_skin_bytes (replaces entire diffuse)
       base_texture_path = diffuse_path (fallback)
       region_indices = "" (empty)
       disabled_regions = "" (empty)
     → Same compositing as single-region: skin replaces all

Bot: "🎨 Rendering…"
Bot: [render result + FormatDownloadView]
```

### 4.5 Flow: Multi-Region Ship with Per-Region Customization

```
User: /render_skin ship:Kinzer RS skin:lava
Bot: → Multi-region detected (3 regions)

Bot: "**Kinzer RS** has **3 skinnable regions**. How would you like to apply the 'lava' skin?"
     [🎨 Apply to All Regions] [🔧 Customize Per Region] [❌ Cancel]

User: clicks [Customize Per Region]

Bot: "**Region 1 of 3** — Select what to apply:"
     [Select menu ▼]
       ├── ✨ Apply 'lava' (selected skin)
       ├── 📤 Upload custom image
       ├── 🔲 Keep default look
       ├── ── Other skins ──
       ├── 🎨 urban-camo
       ├── 🎨 racing-stripes
       ├── 🎨 ferrari
       ├── 🎨 onyx
       ├── ... (up to 25 total options)
       └── 🎨 candy

User: selects "✨ Apply 'lava'"
Bot: "✅ Region 1 → lava"

Bot: "**Region 2 of 3** — Select what to apply:"
     [Select menu ▼] (same options)

User: selects "📤 Upload custom image"
Bot: "Upload your image for **Region 2**:"
User: [uploads galaxy_pattern.png]
Bot: "✅ Region 2 → custom upload"

Bot: "**Region 3 of 3** — Select what to apply:"
     [Select menu ▼]

User: selects "🔲 Keep default look"
Bot: "✅ Region 3 → default"

Bot: "🔧 Compositing textures…"
     → Calls /textures/composite with:
       base_texture_path = diffuse_path (original texture from disk)
       region_textures = [lava_bytes, galaxy_bytes]  (two file uploads)
       region_indices = "1,2"
       disabled_regions = ""  (region 3 is "skip" — not in disabled_regions)

Bot: "🎨 Rendering…"
Bot: [render result + FormatDownloadView]
```

### 4.6 Flow: Multi-Region Ship with Default Render (No Skin/Image)

```
User: /render_skin ship:Kinzer RS
Bot: [defers, fetches render-info]
     → skin == "Default", image == None
     → Default render: skip ALL region prompts regardless of region count

Bot: "🔧 Compositing textures…"
     → Calls /textures/composite with base_texture_path=diffuse_path only
Bot: "🎨 Rendering…"
Bot: [render result]
```

**No change from current behavior. No region prompts for default renders.**

---

## 5. Data Flow

### 5.1 Compositing Request Construction

The skinsCog must build different compositing requests depending on the path taken:

| Scenario | `base_texture` | `base_texture_path` | `region_textures[]` | `region_indices` | `disabled_regions` |
|----------|---------------|--------------------|--------------------|-----------------|-------------------|
| Default render (any ship) | *(none)* | diffuse_path | *(none)* | `""` | `""` |
| Single-region + skin | skin_bytes | diffuse_path | *(none)* | `""` | `""` |
| Multi-region + Apply All | skin_bytes | diffuse_path | *(none)* | `""` | `""` |
| Multi-region + Per-Region | *(none)* | diffuse_path | [per-region bytes] | `"1,2"` (example) | `""` |

### 5.2 Per-Region Data Structure

During per-region collection, the cog tracks state:

```
region_choices: dict[int, RegionChoice]

RegionChoice:
  - action: "skin" | "upload" | "skip"
  - skin_name: str | None      # if action == "skin", which skin was selected
  - texture_bytes: bytes | None  # downloaded skin bytes or uploaded file bytes
```

After collection, this is translated to compositing parameters:
- For each region where `action == "skin"` or `action == "upload"`: add bytes to `region_textures[]`, add index to `region_indices`
- For each region where `action == "skip"`: do nothing (region is omitted from both lists)

### 5.3 Skin Download Caching

When multiple regions select the same pre-made skin (e.g., "lava" on regions 1 and 3), the skin should be downloaded **once** and reused. The cog should maintain a `skin_cache: dict[str, bytes]` within the scope of a single command invocation.

---

## 6. Component Changes

### 6.1 Files to Modify

| File | Changes |
|------|---------|
| `services/discord-gateway/src/cogs/skinsCog.py` | Add 2 new Views, modify `render_skin` and `make_skin_texture`, add helpers |

### 6.2 New Discord UI Views

#### `RegionModeView`
- **Purpose**: Presented when a multi-region ship has a skin/image provided. Lets user choose between "Apply to All" and "Customize Per Region".
- **Buttons**: 
  - "🎨 Apply to All Regions" (primary style) → `self.result = "all"`
  - "🔧 Customize Per Region" (secondary style) → `self.result = "custom"`
  - "❌ Cancel" (danger style) → `self.result = None`
- **Timeout**: 60 seconds
- **Pattern**: Same as existing `SquareCheckView`

#### `RegionOptionSelect`
- **Purpose**: Per-region dropdown for choosing what texture to apply.
- **Component**: `discord.ui.Select` (dropdown menu)
- **Options** (dynamic, based on context):
  1. `✨ Apply '{skin_name}'` — only if a skin was provided via slash command
  2. `📤 Upload custom image`
  3. `🔲 Keep default look`
  4. `───` (separator via option descriptions, not real separators)
  5-25. `🎨 {compatible_skin_name}` — one per compatible skin, up to the 25-option Discord limit
- **Timeout**: 120 seconds per region
- **Result**: `self.selected_value` — a string identifier for the chosen action

### 6.3 New Helper Methods

#### `_resolve_region_mode(interaction, render_info, skin_bytes, skin_name) → str`
- Checks `len(render_info["mask_paths"])` 
- If ≤ 1: returns `"all"` immediately (no prompt)
- If ≥ 2: shows `RegionModeView`, returns `"all"`, `"custom"`, or `None` (cancel)

#### `_collect_per_region_choices(interaction, render_info, skin_name, skin_bytes) → dict[int, RegionChoice] | None`
- Iterates through each mask region (1..N)
- Shows `RegionOptionSelect` for each region
- If user selects a compatible skin: downloads it (with caching)
- If user selects "Upload custom": prompts `wait_for("message")` for file upload
- If user selects "Keep default": records skip
- Returns `None` on cancel or timeout
- Handles: timeout per region, cancellation, download errors

#### `_build_composite_request(render_info, region_choices) → tuple[dict, list]`
- Converts the collected region choices into the multipart form data and files for the `/textures/composite` endpoint
- Returns `(data_dict, files_list)` ready for `self.blender_client.post()`

### 6.4 Modified Methods

#### `render_skin` command
- After fetching render-info and resolving skin_bytes, call `_resolve_region_mode()`
- If `"all"`: proceed with current compositing logic (skin as base_texture)
- If `"custom"`: call `_collect_per_region_choices()`, then `_build_composite_request()`, then call compositor
- If `None`: cancel

#### `make_skin_texture` command
- Same region-mode branching as `render_skin` (without the Blender render step)

#### `_composite_textures` (existing method)
- Extend signature to accept optional `region_textures: dict[int, bytes]` and `disabled_regions: list[int]`
- Or: create a new `_composite_textures_multiregion()` method to keep the single-region path clean
- The method builds the multipart form with `region_textures[]` files and `region_indices` / `disabled_regions` form fields

### 6.5 Refactoring: `_collect_region_textures` (existing, line 334-377)

The existing `_collect_region_textures` method uses sequential `wait_for("message")` prompts with text commands ("skip", "disable"). This should be **replaced** by the new View-based `_collect_per_region_choices()` which uses Select menus. The old method can be removed.

---

## 7. Acceptance Criteria

### 7.1 Single-Region Ships (No Regression)

| # | Criterion |
|---|-----------|
| AC-1 | When a user runs `/render_skin` on a ship with 0 or 1 mask regions, the system shall produce the same result as before this change with no additional prompts or interaction steps |
| AC-2 | When a user runs `/render_skin` on a ship with 0 or 1 mask regions and provides a skin selection, the system shall apply that skin as the base texture without presenting a region mode choice |
| AC-3 | When a user runs `/render_skin` on a ship with 0 or 1 mask regions and provides a custom image upload, the system shall use that image as the base texture without presenting a region mode choice |

### 7.2 Multi-Region: Region Mode Selection

| # | Criterion |
|---|-----------|
| AC-4 | When a user runs `/render_skin` on a ship with 2+ mask regions AND provides a skin or image, the system shall present a choice between "Apply to All Regions" and "Customize Per Region" |
| AC-5 | When a user runs `/render_skin` on a ship with 2+ mask regions with no skin and no image (default render), the system shall NOT present a region mode choice and shall render with the default texture |
| AC-6 | When the user selects "Cancel" on the region mode choice, the system shall abort the operation and inform the user |
| AC-7 | When the region mode choice times out (60 seconds of inactivity), the system shall inform the user that the operation timed out |

### 7.3 Multi-Region: Apply to All

| # | Criterion |
|---|-----------|
| AC-8 | When the user selects "Apply to All Regions", the system shall apply the skin/image uniformly to the entire ship (replacing the diffuse texture as base), producing the same result as a single-region render |
| AC-9 | When "Apply to All" is selected, the compositing request shall use the skin/image as `base_texture` with empty `region_indices` and empty `disabled_regions` |

### 7.4 Multi-Region: Per-Region Customization

| # | Criterion |
|---|-----------|
| AC-10 | When the user selects "Customize Per Region", the system shall present a selection interface for each mask region sequentially (region 1 first, then region 2, etc.) |
| AC-11 | For each region prompt, the system shall offer at minimum: the command's selected skin (if one was provided), an option to upload a custom image, and an option to keep the default look |
| AC-12 | For each region prompt, the system shall list the ship's compatible skins as additional selectable options (up to the platform's selection limit) |
| AC-13 | When a user selects a compatible skin for a region, the system shall download that skin image and apply it to that specific region via the mask |
| AC-14 | When a user selects "Upload custom image" for a region, the system shall prompt for a file upload and wait up to 120 seconds for the user to provide one |
| AC-15 | When a user selects "Keep default look" for a region, that region shall retain the base texture + skinBase composite appearance (the region is omitted from the compositing region list) |
| AC-16 | In per-region mode, the compositing request shall use the ship's original diffuse texture (from disk) as `base_texture_path`, with collected per-region textures in `region_textures[]` and their corresponding mask indices in `region_indices` |

### 7.5 Compositing Correctness

| # | Criterion |
|---|-----------|
| AC-17 | When different skins are selected for different regions, each region shall display its assigned skin (not the skin from another region) |
| AC-18 | When the same compatible skin is selected for multiple regions, the system shall download that skin only once per command invocation |
| AC-19 | When a per-region skin download fails, the system shall inform the user and treat that region as "default" (skip), continuing with remaining regions |

### 7.6 Both Commands

| # | Criterion |
|---|-----------|
| AC-20 | The `/make_skin_texture` command shall support the same multi-region flows as `/render_skin` (region mode selection, apply-to-all, per-region customization) |
| AC-21 | The region selection logic shall be shared between `/render_skin` and `/make_skin_texture` (no duplicated UX flow) |

### 7.7 Edge Cases

| # | Criterion |
|---|-----------|
| AC-22 | Ships with `texture_regions: 0` (skinnable but no mask files) shall render without region prompts, identical to current behavior |
| AC-23 | When the `mask_paths` array in render-info is empty (regardless of `texture_regions` integer value), the system shall treat the ship as having 0 regions |
| AC-24 | When a per-region file upload times out, the system shall skip that region and continue with the next region (not abort the entire operation) |
| AC-25 | When ALL regions are set to "Keep default look" in per-region mode, the system shall produce the same result as a default render (no skin applied) |
| AC-26 | When the user cancels during per-region collection (closes/ignores the select menu until timeout for all remaining regions), the system shall composite with whatever regions were already configured and inform the user |

---

## 8. Edge Cases — Detailed Analysis

### 8.1 Ships with 0 Mask Regions

Five ships (Vol'Noor, Amboss, Wraith, Dark Angel, Vossk Battleship) are `skinnable: true` but have 0 masks. Their `mask_paths` array is empty. The compositing pipeline handles this gracefully: base + skinBase composite, no mask operations. **No special handling needed** — the `len(mask_paths) <= 1` check treats them identically to single-region ships.

### 8.2 Ships with 10+ Mask Regions (Future-Proofing)

No current ships exceed 3 regions, but the design must not impose an artificial cap. The per-region Select menu flow scales linearly with region count. With 10 regions × 120s timeout = 20 minutes, which exceeds Discord's 15-minute interaction limit.

**Mitigation**: For ships with more than 5 regions, the per-region timeout should be reduced to 60 seconds per region. Alternatively, the system could batch regions into groups. This is a future concern — no ships currently have more than 3.

### 8.3 Interaction Timeout

Discord interactions expire 15 minutes after the initial response. The per-region flow for a 3-region ship with file uploads could take up to 6 minutes (3 regions × 120s). This is well within the limit. Each intermediate message is sent via `interaction.followup.send()` which does not reset the timer but is allowed as long as the interaction hasn't expired.

### 8.4 Skin Name Collisions in Select Menu

Compatible skin names are short strings like "lava", "onyx", "camo". These are unique per ship. The Select menu uses the skin name as both the label and value. No collision risk within a single ship.

### 8.5 The `texture_regions` Field vs `mask_paths` Array

The `texture_regions` integer on the Ship model and the `mask_paths` array in render-info may theoretically disagree (e.g., if mask files are missing from disk). **The cog should use `len(mask_paths)` as the authoritative region count**, not `texture_regions`. The `mask_paths` array reflects actual files on disk (filtered from the `assets` array by the render-info endpoint).

### 8.6 Concurrent Users

Multiple users may run `/render_skin` simultaneously. All state is scoped to the command invocation (local variables in the async method). No shared mutable state is involved. The skin download cache is per-invocation, not per-cog. **No concurrency issues.**

---

## 9. Implementation Guidance

### For Developer Agent

1. **Start with the new Views** (`RegionModeView`, `RegionOptionSelect`) — these are self-contained and testable in isolation.

2. **Build `_collect_per_region_choices()` next** — this is the most complex new method. It orchestrates the per-region flow using the Views, handles skin downloads with caching, and manages timeouts.

3. **Modify `render_skin`** to add the region-mode branching. Keep the existing `_composite_textures()` for the "apply to all" path. Create `_composite_textures_multiregion()` for the per-region path, OR extend `_composite_textures()` with optional region parameters.

4. **Apply the same pattern to `make_skin_texture`** by extracting the shared region-resolution logic into a reusable helper.

5. **Use `len(render_info.get("mask_paths", []))` as the region count** — not the `texture_regions` integer.

6. **The `RegionOptionSelect` options should be built dynamically**:
   ```
   options = []
   if skin_name:  # command-level skin was provided
       options.append(SelectOption(label=f"Apply '{skin_name}'", value=f"skin:{skin_name}", emoji="✨"))
   options.append(SelectOption(label="Upload custom image", value="upload", emoji="📤"))
   options.append(SelectOption(label="Keep default look", value="skip", emoji="🔲"))
   for name in compatible_skins:
       if name != skin_name and len(options) < 25:
           options.append(SelectOption(label=name, value=f"skin:{name}", emoji="🎨"))
   ```

7. **Skin download caching** within the command scope:
   ```
   skin_cache: dict[str, bytes] = {}
   async def get_skin_bytes(skin_name: str) -> bytes | None:
       if skin_name in skin_cache:
           return skin_cache[skin_name]
       bytes_ = await self._download_skin_image(...)
       if bytes_:
           skin_cache[skin_name] = bytes_
       return bytes_
   ```

8. **The existing `_collect_region_textures()` method (lines 334-377)** should be replaced by the new `_collect_per_region_choices()`. The old method uses text-based prompts ("skip", "disable") which are inferior to Select menus for this use case.

### For Tester Agent

Critical test scenarios:

1. **Single-region ship + skin** → no region prompt, same output as before
2. **Multi-region ship + skin + "Apply to All"** → no per-region prompts, skin applied uniformly
3. **Multi-region ship + skin + "Customize"** → per-region Select menus shown
4. **Per-region: select command skin** → downloads skin, applies to that region's mask
5. **Per-region: select different compatible skin** → downloads different skin for that region
6. **Per-region: upload custom image** → triggers wait_for("message"), applies upload
7. **Per-region: keep default** → region omitted from compositing request
8. **Per-region: same skin on multiple regions** → skin downloaded once (cache hit)
9. **Region mode timeout** → error message, operation cancelled
10. **Per-region select timeout** → that region skipped, next region prompted
11. **Upload timeout within per-region** → that region skipped
12. **Cancel on region mode view** → operation cancelled
13. **0-region ship** → no region prompt
14. **Default render (no skin/image) on multi-region ship** → no region prompt
15. **`make_skin_texture` with multi-region** → same flows as `render_skin`
16. **Skin download failure mid-region** → region falls back to skip, error message, continues

---

## 10. Sequence Diagrams

### 10.1 Apply to All (Multi-Region Ship)

```
User                    skinsCog                 bot-core              blender-service
 │                        │                        │                        │
 │  /render_skin          │                        │                        │
 │  ship=Kinzer RS        │                        │                        │
 │  skin=lava             │                        │                        │
 │───────────────────────>│                        │                        │
 │                        │  GET render-info       │                        │
 │                        │───────────────────────>│                        │
 │                        │  {texture_regions:3,   │                        │
 │                        │   mask_paths:[1,2,3],  │                        │
 │                        │   compatible_skins:{}} │                        │
 │                        │<───────────────────────│                        │
 │                        │                        │                        │
 │                        │  Download lava skin    │                        │
 │                        │───────GET skin URL────>│                        │
 │                        │<──────skin_bytes───────│                        │
 │                        │                        │                        │
 │  RegionModeView        │                        │                        │
 │  [All] [Custom] [X]   │                        │                        │
 │<───────────────────────│                        │                        │
 │  clicks [Apply All]   │                        │                        │
 │───────────────────────>│                        │                        │
 │                        │                        │                        │
 │                        │  POST /textures/composite                      │
 │                        │  base_texture=lava_bytes                       │
 │                        │  base_texture_path=diffuse                     │
 │                        │  region_indices=""                              │
 │                        │───────────────────────────────────────────────>│
 │                        │<──────────composite_bytes─────────────────────│
 │                        │                        │                        │
 │                        │  POST /render/                                 │
 │                        │  texture=composite_bytes                       │
 │                        │  model_path=model.obj                          │
 │                        │───────────────────────────────────────────────>│
 │                        │<──────────render_bytes───────────────────────│
 │                        │                        │                        │
 │  [render image +       │                        │                        │
 │   download buttons]    │                        │                        │
 │<───────────────────────│                        │                        │
```

### 10.2 Per-Region Customization

```
User                    skinsCog                 blender-service
 │                        │                        │
 │  clicks [Customize]   │                        │
 │───────────────────────>│                        │
 │                        │                        │
 │  Region 1 Select      │                        │
 │  [lava ✨|upload|skip] │                        │
 │<───────────────────────│                        │
 │  selects "lava ✨"    │                        │
 │───────────────────────>│                        │
 │                        │  (downloads lava skin, │
 │                        │   caches bytes)        │
 │                        │                        │
 │  Region 2 Select      │                        │
 │<───────────────────────│                        │
 │  selects "Upload 📤"  │                        │
 │───────────────────────>│                        │
 │  "Upload for Region 2"│                        │
 │<───────────────────────│                        │
 │  [uploads file]       │                        │
 │───────────────────────>│                        │
 │                        │                        │
 │  Region 3 Select      │                        │
 │<───────────────────────│                        │
 │  selects "Keep default"│                        │
 │───────────────────────>│                        │
 │                        │                        │
 │                        │  POST /textures/composite
 │                        │  base_texture_path=diffuse
 │                        │  region_textures=[lava_bytes, upload_bytes]
 │                        │  region_indices="1,2"
 │                        │  disabled_regions=""
 │                        │───────────────────────>│
 │                        │<──────composite────────│
 │                        │                        │
 │                        │  POST /render/         │
 │                        │───────────────────────>│
 │                        │<──────render───────────│
 │  [render image]       │                        │
 │<───────────────────────│                        │
```

---

## 11. Summary of Changes

| Area | Change | Effort |
|------|--------|--------|
| `skinsCog.py` — New Views | `RegionModeView`, `RegionOptionSelect` | Small |
| `skinsCog.py` — Region resolution | `_resolve_region_mode()`, `_collect_per_region_choices()` | Medium |
| `skinsCog.py` — Compositing | `_build_composite_request()` or extended `_composite_textures()` | Medium |
| `skinsCog.py` — Command modification | `render_skin` branching, `make_skin_texture` branching | Small |
| `skinsCog.py` — Cleanup | Remove old `_collect_region_textures()` | Trivial |
| bot-core | None | Zero |
| blender-service | None | Zero |
| Tests | New tests for Views, per-region flow, compositing request building | Medium |

**Total scope**: ~1 file modified, ~150-250 lines of new code, ~40 lines removed.

---

*End of design document*
