# GoF2 Wiki Scraper

Scrapes https://galaxyonfire.wiki.gg/wiki/ for **Galaxy on Fire 2** item stats.

## Requirements

All dependencies are available in the project Python environment:
- `httpx` (HTTP client)
- `beautifulsoup4` (HTML parsing)
- `lxml` (fast HTML parser backend)
- `psycopg2` (optional: live DB diff — falls back to import_data/ if unavailable)

## Usage

```bash
# Run spot-checks only (5 items, fast validation)
python scrape_gof2.py --spot-check

# Scrape all categories (211 items, ~5–10 min)
python scrape_gof2.py --category all

# Scrape a single category
python scrape_gof2.py --category primary
python scrape_gof2.py --category secondary
python scrape_gof2.py --category turret
python scrape_gof2.py --category module
python scrape_gof2.py --category ship

# Re-run DB diff only (requires prior scrape)
python scrape_gof2.py --diff-only
```

## Output Files

| File | Description |
|------|-------------|
| `/tmp/gof2_wiki_raw/<category>/<slug>.json` | One JSON file per item |
| `/tmp/gof2_wiki_combined.json` | All items consolidated by category |
| `/tmp/gof2_wiki_diff.md` | DB vs wiki discrepancy report (markdown table) |
| `/tmp/gof2_wiki_scraper.log` | Warnings, errors, ambiguous version info |

## Data Format

Each item JSON contains:

```json
{
  "_status": "ok",
  "_name": "128MJ Railgun",
  "_category": "primary",
  "_url": "https://galaxyonfire.wiki.gg/wiki/128MJ_Railgun",
  "_scraped_at": "2026-05-24T...",
  "raw_infobox": {
    "Type": "Auto-cannon",
    "Tech Level": "6",
    "Damage": "3",
    "Loading speed": "120ms",
    "Damage per second": "25.0",
    "Range": "2500m",
    "Speed": "6500km/h",
    "Known Price Range": "-> $22,676 ..."
  },
  "tech_level": 6,
  "damage": 3,
  "loading_speed_ms": 120,
  "dps": 25.0,
  "range_m": 2500,
  "projectile_speed_kmh": 6500,
  "known_price_range": {"raw": "...", "min_credits": 22676, "max_credits": 24675},
  "description": "The 128MJ Multirail was...",
  "notes": ["It is a good alternative for...", ...],
  "_wiki_categories": ["Primary Weapons", "Auto-Cannon", "Tech level 5", ...]
}
```

Failed fetches look like:
```json
{
  "_status": "fetch_failed",
  "_name": "Item Name",
  "_url_tried": ["https://..."],
  "_http_status": 404
}
```

## GoF2-Family Filter

Only GoF2 / GoF2 HD / Valkyrie / Supernova data is captured.  
Excludes: GoF 3D, Galaxy on Fire: Alliances, Galaxy on Fire 3.

## Etiquette

- 500–1000 ms sleep between requests
- User-Agent: `BountyBot-Reborn-SamX/data-truer (https://github.com/anomalyco/opencode)`
- No login or authentication required

## Canonical Field Mapping

| Canonical Key | Source Infobox Row(s) | Type |
|---|---|---|
| `tech_level` | Tech Level | int |
| `damage` | Damage | int |
| `loading_speed_ms` | Loading speed | int (ms) |
| `dps` | Damage per second | float |
| `range_m` | Range | int (m) |
| `projectile_speed_kmh` | Speed | int (km/h) |
| `effect_pct` | Effect | float (%) |
| `effect_multiplier` | Effect | float (derived) |
| `duration_ms` | Duration / Boost duration | int (ms) |
| `cooldown_ms` | Loading speed (boosters) | int (ms) |
| `capacity` | Capacity | int |
| `recharge_speed_ms` | Recharge speed | int (ms) |
| `hp_per_second` | HP per second | float |
| `time_to_lock_s` | Time to lock | float (s) |
| `handling` | Handling | int |
| `armour` | Armor / Armour | int |
| `cargo` | Cargo hold | int |
| `max_primaries` | Primary weapons | int |
| `max_secondaries` | Secondary weapons | int |
| `max_turrets` | Turrets | int |
| `max_modules` | Equipment | int |
| `faction` | Manufacturer / Faction | str |
| `autonomous` | Type: Auto/Manual (turrets) | bool |
| `magnitude` | Magnitude / Blast radius | float |
| `value` | Price (ships) | int |
| `known_price_range` | Known Price Range | dict |

## Next Phase

After verifying the scraped data, update `services/bot-core/import_data/` using a
separate merge script. **Do NOT modify import_data/ directly from this scraper.**
