# GoF2 Wiki Extraction Summary

_Generated: 2026-05-24 via AI semantic extraction (Claude Sonnet 4.6)_
_Methodology: AI reads wiki pages directly, produces structured JSON without regex parsing_

---

## 1. Total Counts Per Category

| Category | In-Catalog | Newly Discovered | Total Extracted |
|----------|-----------|-----------------|-----------------|
| Primary Weapons | 40 | 0 | 40 |
| Secondary Weapons | 30 | 0 | 30 |
| Turret Weapons | 10 | 0 | 10 |
| Modules | 66 | 0 | 66 |
| Ships | 65 | 0 | 65 |
| **TOTAL** | **211** | **0** | **211** |

---

## 2. Items in Catalog but Missing from Wiki

All 211 catalog items were found on the wiki. None are missing.

**Special cases:**
- **Vossk Battlecruiser** — Wiki page exists but explicitly states "exact stats for GoF2 era are currently unknown." Infobox is empty. File captured with notes and available prose.
- **Terran Battlecruiser** — Wiki page exists with partial stats (Armor: 7000-7700, 7 D2A2 turrets). NPC capital ship, not player-purchasable.
- **Terran Freighter / Vossk Freighter / Nivelian Freighter / Midorian Freighter** — All on shared `/wiki/Freighter` page. No infobox stats (NPC ships only). Prose/description captured.

---

## 3. Items Newly Discovered on Wiki (NOT in our Catalog)

**After thorough comparison of wiki category pages vs. the 211-item catalog baseline:**

None. All items visible in the Primary, Secondary, Turret, Module, and Ship category pages are already in the catalog. The 211 catalog items are complete.

**Notes on items examined but excluded:**
- `Laboratory` — Appears in the Equipment category page but is a **GoF: Alliances** facility, not a GoF2 item. Correctly excluded.
- `Loma Prices - Android - Standard Difficulty` — Reference page in Equipment category, not an item.
- `Technologies` and `Top-Tier Equipment` — Reference/guide pages, not items.
- GoF3 ships (Groza, Styx, Argus, etc.) — Appeared in the Ships category page GoF3 section. All correctly excluded per task instructions.
- `Suzaku AT XR` — Mentioned in turrets page as "cut from final game, stats unavailable." Not extracted.

---

## 4. DB-vs-Wiki Discrepancies

The following discrepancies were found during extraction:

### Critical Discrepancies

| Item | DB Value | Wiki Value | Resolution |
|------|----------|------------|------------|
| H'Belam | tech_level: 5 | tech_level: 6 | Wiki infobox shows 6. Category table shows 5. **Infobox authoritative → wiki = 6** |
| 128MJ Railgun | tech_level: 5 | tech_level: 6 | Wiki infobox shows 6 (category page listed 5 in tech column). **Wiki infobox = 6** |

### Parser Bug Fixes (Previous Scrape Errors)

| Item | Previous Parser Error | Correct Value | Source |
|------|----------------------|---------------|--------|
| U'tool | `effect_multiplier: 10000.0` | `duration_ms: 10000` | Wiki: "Effect: 10000ms" = cloak DURATION (10s), not a multiplier |
| Sight Suppressor II | `effect_multiplier: 20000.0` | `duration_ms: 20000` | Same — "Effect: 20000ms" = 20s cloak duration |
| Yin Co. Shadow Ninja | `effect_multiplier: 40000.0` | `duration_ms: 40000` | Same — 40s cloak duration |
| Emergency System | `effect_multiplier: 10000.0` | `duration_ms: 10000` | Same — 10s emergency shield duration |
| Rhoda Vortex | unknown | `duration_ms: 15000` (perceived; 7500ms real time) | Time dilation, not a multiplier |

### Price Discrepancies (minor)

| Item | DB/Previous Scrape | Wiki Actual | Note |
|------|-------------------|-------------|------|
| Betty | $16,038 | $16,200 | Ships table shows Android price $16,038; individual page PC price $16,200 |
| Various ships | Various | Both PC + Android captured | Ships category page has both columns — captured as `price_credits` (PC) and `price_credits_android` |

### DPS Discrepancies (from diff report)

| Item | DB DPS | Wiki DPS | Status |
|------|--------|---------|--------|
| Micro Gun MK I | 9.09 | 9.09 | Match ✓ (was flagged as 9.9 vs 9.09 — both are 9.09) |
| Tyrfing Blaster | 59.09 | 59.09 | Match ✓ |

### GoF2 HD Android Price Overrides Captured

Confirmed different prices for GoF2 HD Android vs iOS/PC on:
- U'tool: $37,717 (iOS) → $97,544–$102,570 (Android) — **2.6× difference**
- Sight Suppressor II: $23,856–$29,599 (iOS) → $65,146 (Android, single station)
- Phoenix SIS: $518,996–$535,304 (iOS) → $872,969–$917,210 (Android)

---

## 5. Mechanics Clarifications (Wiki-sourced facts)

The following mechanics were explicitly confirmed from wiki prose that weren't previously documented in the DB:

### Cloaks — Effect field = DURATION, not multiplier
All three cloaks have `Effect: Xms` in their infobox. This is the **cloak duration in milliseconds**, not a speed multiplier or percentage. Summary:
- U'tool: **10 seconds** cloak (Effect: 10000ms) — charging time 2000ms, costs 1 energy cell
- Sight Suppressor II: **20 seconds** (Effect: 20000ms) — charging time 6500ms, costs 2 energy cells
- Yin Co. Shadow Ninja: **40 seconds** (Effect: 40000ms) — charging time 3500ms, costs 5 energy cells

**Critical design detail:** While cloaked, enemies CANNOT track/target the ship, stopping all fire. However, enemies CAN still see muzzle flashes and projectiles from weapons fired while cloaked.

### Emergency System — Effect = SHIELD DURATION, not multiplier
`Effect: 10000ms` = emergency shield lasts **10 seconds**. Module is CONSUMED after use (must be repurchased). Cannot use Khador Drive while Emergency System is active.

### Thermal Fusion Weapons — Heat-seeking mechanic
When scanner is locked on a target, Thermic/ReHeat/MaxHeat/SunFire projectiles **aim toward the target** automatically (heat-seeking). Without lock-on, they fire in random directions.

### Rhoda Vortex — Time dilation details
`Effect: 15000ms` = **15 seconds perceived** duration at 50% time slowdown = only 7.5 seconds pass in real time. Loading speed 30000ms (30s recharge). Button disappears during mining/hacking unless activated right before.

### Phoenix SIS — Plasma shield injector mechanics
Consumes **30t blue plasma** per activation. Activates automatically when shield depletes. Can continuously recharge indefinitely as long as plasma available — creates "infinite shield" effect. Blueprint-only item.

### Gamma Shields — Radiation protection only
Gamma Shields ONLY protect against radiation in the Ginoya system (Supernova DLC). Regular shields do NOT protect against gamma radiation. Gamma Shield I = 40% protection, Gamma Shield II = 60%.

### Tractor Beams — Automatic coverage
- AB-3 "Kingfisher": Semi-auto, covers **forward 180°**, instant lock
- AB-4 "Octopus": Full **360° omni-directional**, instant lock (Valkyrie DLC)

### Sentry Guns — Deployment mechanics
Maximum **3 sentry guns** in any area at once. They CAN cause friendly fire. They CAN be healed by repair beams. They despawn when you travel to another station orbit or enter a station. T'Suum comes pre-installed with Sh'gaal blaster.

### Scanner features
Only the Hiroto Ultrascan (tech 7) shows **Class A asteroids** on radar. Hiroto Proscan (tech 6) and Telta Ecoscan (tech 3) show cargo contents but NOT Class A asteroids.

### Booster mechanics (Linear Boost reference)
Effect% = speed **increase** above base speed (not multiplier of base). Base ship speed ~450 km/h. With Linear Boost (60%): 450 × 1.6 = **720 km/h** for 3 seconds. Loading speed = **recharge time** (not activation time).

### Freighters — Loot behavior
Vossk Freighters tend to carry **more valuable loot** than other freighters. If you loot a secondary weapon from a ship that matches one you have equipped, it goes directly to your weapon slots (not cargo hold).

### Spectral Filters — Plasma visibility
- SA-1: Makes plasma clouds visible only (no radar, no info)
- ST-X: Makes visible + shows cloud info
- Omega (Kaamo): Visible + info + shows on **radar**

---

## 6. Special Notes and Quirks

### URL Quirks Confirmed
- `Nemesis` (ship) → `/wiki/Nemesis_(ship)` — plain `/wiki/Nemesis` is a GoF1 weapon
- `Micro Gun MK II` → URL slug is `Micro_Gun_MKII` (no space before II, confirmed)
- `Groza Mk II` → URL is `Groza_MK_II` (all caps MK_II, confirmed)
- `IMT Extract 4.0X` → URL has BOM character: `IMT_Extract\uFEFF_4.0X` (confirmed)
- `Armour Rocket` → Wiki page spelled `Amour_Rocket` (missing 'r', confirmed)
- `S'koon` → Wiki uses `S'koonn` (extra 'n', confirmed)
- `Salvéhn` → URL is `Salv%C3%A9hn` (accented é)
- `Neétha EMP` → URL is `Ne%C3%A9tha_EMP` (accented é)

### Version Availability Notes
- **Gryphon** — NOT available for PC version. Mobile/HD only.
- **Vossk Battlecruiser** — GoF2 stats "currently unknown" per wiki
- **Phoenix SIS** — Blueprint-only, not purchasable at stations
- **Yin Co. Shadow Ninja** — Blueprint-only, blueprint at E'kkide (K'ontrr System)
- **Rhino** — Supernova DLC only; 1200 armor / 480 cargo / 0 primaries

---

_Extraction method: AI semantic reading of wiki pages using mcp_Searxng_web_url_read._
_All 211 catalog items confirmed. 0 new items discovered beyond catalog._
