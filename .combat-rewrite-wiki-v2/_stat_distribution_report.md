# Combat Stat Distribution Report
**Date**: 2026-05-24  
**Data Source**: `/proj/.combat-rewrite-wiki-v2/_combined.json`  
**Purpose**: Determine rounding requirements for tick-based combat simulator

---

## Executive Summary

**NO ROUNDING NEEDED.** All weapon `loading_speed_ms` values across the entire catalog (77 values) are divisible by **10ms**. The GCD is 10ms, providing a clean natural minimum tick unit. No problematic outliers exist.

---

## Dataset Overview

| Category | Count | Items |
|----------|-------|-------|
| Primary weapons | 40 | loading_speed_ms collected |
| Secondary weapons | 30 | loading_speed_ms collected |
| Turret weapons | 7 | loading_speed_ms collected |
| Modules | 10 | loading_speed_ms (cooldown) collected |
| Ships | 59 | checked (no timing stats) |
| **Total** | **146 items** | **77 weapon loading speeds** |

---

## Combat-Relevant Stat Analysis

### Critical Stat: Weapon `loading_speed_ms`

| Metric | Value |
|--------|-------|
| **Count** | 77 |
| **Min** | 90ms |
| **Max** | 10,000ms |
| **Mean** | 1,726ms |
| **Median** | 680ms |
| **GCD** | **10ms** ✓ |
| **Unique values** | 41 |
| **Outliers** | 0 |

#### Distribution by Type

| Type | Count | Range | GCD | Status |
|------|-------|-------|-----|--------|
| Primary | 40 | 90–1,500ms | 10ms | ✓ Clean |
| Secondary | 30 | 250–10,000ms | 10ms | ✓ Clean |
| Turret | 7 | 100–300ms | 10ms | ✓ Clean |
| Module cooldown | 10 | 2,000–30,000ms | 500ms | ✓ Clean |

#### All Unique Values (sorted)
```
[90, 100, 120, 140, 150, 170, 190, 200, 220, 230, 250, 280, 300, 330, 350,
 380, 400, 430, 450, 500, 530, 600, 680, 700, 750, 850, 900, 1000, 1200,
 1300, 1400, 1500, 2000, 3000, 4000, 5200, 6000, 6500, 7000, 8000, 10000]
```

**Finding**: Every single value is a multiple of 10ms. No exceptions.

---

### Secondary Combat Stats

| Stat | Count | Min | Max | Median | GCD | Status |
|------|-------|-----|-----|--------|-----|--------|
| `damage_per_shot` (weapons) | 47 | 2 | 120 | 9 | 1 | ⚠ |
| `range_m` (weapons) | 77 | 0 | 13,800 | 3,000 | 100m | ✓ |
| `projectile_speed_kmh` | 73 | 0 | 30,000 | 4,500 | 50 km/h | ✓ |
| `dps` (weapons) | 50 | 7.5 | 92.3 | 24.6 | 1 | ⚠ |
| `max_hull` (ships) | — | — | — | — | — | *not in data* |
| `max_armour` (ships) | 59 | — | — | — | 1 | ⚠ |
| `max_shield` (ships) | — | — | — | — | — | *not in data* |
| Module `effect_pct` | 18 | 15 | 300 | 65 | 5 | ✓ |
| Module `duration_ms` | 10 | 3,000 | 40,000 | 8,000 | 200ms | ✓ |

**Key findings**:
- **Weapon damage** (damage_per_shot): GCD = 1, highly variable (individual values matter, no rounding possible)
- **DPS**: Derived stat, floating-point, GCD = 1 (not suitable for rounding)
- **Range**: All multiples of 100m → clean
- **Projectile speed**: All multiples of 50 km/h → clean
- **Module cooldowns**: All multiples of 500ms → clean

---

## Outlier Analysis

### Weapon Loading Speed Outliers

**Criterion**: Values not conforming to dominant pattern (>90% of values).

**Finding**: **Zero outliers.** All 77 values are multiples of 10ms.

Closest to minimum: **90ms** (Icarus, some turrets)  
Closest to maximum: **10,000ms** (Liberator secondary)

#### Fastest Weapons (≤250ms)
- 90ms: Icarus Heavy AS
- 100ms: Berger AGT 20mm
- 120ms: 128MJ Railgun
- 150ms: HH-AT "Archimedes"
- 190ms: Skuld AT XR
- 200ms: Matador TS
- 230ms: *(primary)*
- 250ms: L'ksaar

#### Slowest Weapons (≥5,000ms)
- 5,200ms: EMP GL DX
- 6,000ms: AMR Tormentor, Ion Lambda MK1/MK2, Shock Blast, EMP GL I
- 6,500ms: AMR Oppressor, EMP GL II
- 7,000ms: AMR Extinctor
- 8,000ms: Fireworks
- 10,000ms: Liberator

All divisible by 10ms. ✓

---

## Tick Cadence Recommendation

### Summary

| Factor | Value |
|--------|-------|
| **GCD of all weapon loading_speed_ms** | **10ms** |
| **Minimum tick unit** | 10ms |
| **Fastest weapon** | 90ms ÷ 10 = **9 ticks** |
| **Longest reload** | 10,000ms ÷ 10 = **1,000 ticks** |
| **Ratio (longest/fastest)** | 111:1 |

### Verdict

**✓ NO ROUNDING REQUIRED**

All 77 weapon loading speeds and 10 module cooldown values are clean multiples of 10ms or coarser. The dataset exhibits **no pathological outliers**. 

**Recommended tick accumulator strategy**:
- Use **10ms as the minimum tick unit** (divide all loading_speed_ms by 10 to get tick counts)
- Accumulate ticks in both combatants' accumulators
- Fire weapon when `accumulator >= fire_interval_ticks`
- Example: 120ms loading speed → 12 ticks; Liberator's 10,000ms → 1,000 ticks

This is **mathematically clean**: GCD(90, 100, 120, ..., 10000) = 10 guarantees no rounding errors over arbitrary combat durations.

---

## Notes

1. **Module cooldowns** (2,000–30,000ms, GCD 500ms) are coarser and pose no constraint.
2. **Damage values** are integers but not uniform multiples; acceptable as-is (no timing implication).
3. **DPS** is derived (damage ÷ loading_speed); floating-point representation is standard.
4. **Ship stats** (armour, cargo, etc.) are not timing-critical for this Phase-1 analysis.

---

*Report generated by combat-stats analyzer*
