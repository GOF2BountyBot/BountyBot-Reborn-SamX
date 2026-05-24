#!/usr/bin/env python3
"""
GoF2 Wiki Scraper — BountyBot-Reborn-SamX
==========================================
Scrapes https://galaxyonfire.wiki.gg/wiki/ for Galaxy on Fire 2 item stats.

Usage
-----
    python scrape_gof2.py --category all
    python scrape_gof2.py --category primary
    python scrape_gof2.py --category ship --spot-check
    python scrape_gof2.py --diff-only   # just re-run the DB diff (requires prior scrape)

Output
------
  /tmp/gof2_wiki_raw/<category>/<slug>.json   — one file per item
  /tmp/gof2_wiki_combined.json                — consolidated by category
  /tmp/gof2_wiki_diff.md                      — DB-vs-wiki discrepancy report
  /tmp/gof2_wiki_scraper.log                  — error / ambiguity log

GoF2-family only.  Excludes: GoF 3D, Alliances, GoF3.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WIKI_BASE = "https://galaxyonfire.wiki.gg/wiki/"
USER_AGENT = "BountyBot-Reborn-SamX/data-truer (https://github.com/anomalyco/opencode)"
SLEEP_MIN = 0.5
SLEEP_MAX = 1.0

OUTPUT_DIR = Path("/tmp/gof2_wiki_raw")
COMBINED_FILE = Path("/tmp/gof2_wiki_combined.json")
DIFF_FILE = Path("/tmp/gof2_wiki_diff.md")
LOG_FILE = Path("/tmp/gof2_wiki_scraper.log")

# Game-version identifiers to EXCLUDE (case-insensitive partial match)
EXCLUDED_GAME_VERSIONS = {
    "gof 3d", "gof3d", "gof1", "3d only", "alliances", "gofa",
    "gof:a", "galaxy on fire: alliances", "galaxy on fire 3",
    "gof3", "gof 3", "manticore",
}
# Game-version identifiers to INCLUDE (GoF2 family)
INCLUDED_GAME_VERSIONS = {
    "gof2", "gof2hd", "gof2 hd", "gof 2", "galaxy on fire 2",
    "valkyrie", "supernova",
}

# ---------------------------------------------------------------------------
# Item catalogs
# ---------------------------------------------------------------------------

PRIMARY_WEAPONS = [
    "128MJ Railgun", "64MJ Railgun", "Berger Converge IV", "Berger FlaK 9-9",
    "Berger Focus I", "Berger Focus II A1", "Berger Retribution", "Dark Matter Laser",
    "Dia EMP Mk III", "Disruptor Laser", "Gram Blaster", "H'nookk", "Icarus Heavy AS",
    "K'booskk", "Luna EMP Mk I", "M6 A1 \"Wolf\"", "M6 A2 \"Cougar\"",
    "M6 A3 \"Wolverine\"", "M6 A4 \"Raccoon\"", "Mass Driver MD 10", "Mass Driver MD 12",
    "MaxHeat o20", "Micro Gun MK I", "Micro Gun MK II", "Mimung Blaster",
    "Nirai .50AS", "Nirai Charged Pulse", "Nirai Impulse EX 1", "Nirai Impulse EX 2",
    "N'saan", "ReHeat o10", "Ridil Blaster", "Scram Cannon", "Sh'gaal", "Sh'koom",
    "Sol EMP Mk II", "SunFire o50", "Thermic o5", "Tyrfing Blaster", "V'skorr",
]

SECONDARY_WEAPONS = [
    "AMR Extinctor", "AMR Oppressor", "AMR Saber", "AMR Tormentor", "Armour Rocket",
    "Berger SG-100", "Berger SG-400", "Dephase EMP", "Edo", "EMP GL DX", "EMP GL I",
    "EMP GL II", "EMP Rocket Mk I", "EMP Rocket Mk II", "Fireworks", "Garuda-IV",
    "G'liissk", "Intelli Jet", "Ion Lambda MK1", "Ion Lambda MK2", "Jet Rocket",
    "Ksann'k", "Liberator", "Mamba EMP", "Neétha EMP", "Patala", "Shesha",
    "Shock Blast", "S'koon", "T'Suum",
]

TURRET_WEAPONS = [
    "Berger AGT 20mm", "Hammerhead D1", "Hammerhead D2A2", "HH-AT \"Archimedes\"",
    "L'ksaar", "Matador TS", "PE Ambipolar-5", "PE Fusion H2", "PE Proton",
    "Skuld AT XR",
]

MODULES = [
    # ArmourModule
    "D'iol", "E2 Exoclad", "E4 Ultra Lamina", "E6 D-X Plating", "T'yol",
    # BoosterModule
    "Cyclotron Boost", "Linear Boost", "Me'al", "Polytron Boost", "Synchrotron Boost",
    # CabinModule
    "Large Cabin", "Medium Cabin", "Small Cabin",
    # CloakModule
    "Sight Suppressor II", "U'tool", "Yin Co. Shadow Ninja",
    # CompressorModule
    "Autopacker 2", "Rhoda Blackhole", "Shrinker BT", "Ultracompact", "ZMI Optistore",
    # EmergencySystemModule
    "Emergency System",
    # GammaShieldModule
    "Gamma Shield I", "Gamma Shield II",
    # JumpDriveModule
    "Khador Drive",
    # MiningDrillModule
    "Gunant's Drill", "IMT Extract 1.3", "IMT Extract 2.7", "IMT Extract 4.0X", "K'yuul",
    # PrimaryWeaponModModule
    "Nirai Overcharge", "Nirai Overdrive",
    # RepairBeamModule
    "Nirai SPP-C1", "Nirai SPP-M50",
    # RepairBotModule
    "Ketar Repair Bot", "Ketar Repair Bot II",
    # ScannerModule
    "Hiroto Proscan", "Hiroto Ultrascan", "Telta Ecoscan", "Telta Quickscan",
    # ShieldInjectorModule
    "Phoenix SIS",
    # ShieldModule
    "Beamshield II", "Fluxed Matter Shield", "H'Belam", "Particle Shield",
    "Riot Shield", "Targe Shield",
    # SignatureModule
    "Signature Midorian", "Signature Nivelian", "Signature Terran", "Signature Vossk",
    # SpectralFilterModule
    "Spectral Filter Omega", "Spectral Filter SA-1", "Spectral Filter ST-X",
    # ThrusterModule
    "D'ozzt Thrust", "Mp'zzzm Thrust", "Pendular Thrust", "Pulsed Plasma Thrust",
    "Static Thrust",
    # TimeExtenderModule
    "Rhoda Vortex",
    # TractorBeamModule
    "AB-1 \"Retractor\"", "AB-2 \"Glue Gun\"", "AB-3 \"Kingfisher\"", "AB-4 \"Octopus\"",
    # TransfusionBeamModule
    "Crimson Drain", "Pandora Leech",
    # RepairBeamModule (also listed here by original task)
    # (Nirai SPP-C1 and Nirai SPP-M50 already listed above)
]

SHIPS = [
    "Betty", "Wasp", "Night Owl", "Inflict", "Hiro", "Hector", "Badger", "Cicero",
    "Azov", "Type 43", "Furious", "Berger CrossXT", "Salvéhn", "Taipan", "Vol Noor",
    "Hera", "Teneta", "H'Soc", "Cormorant", "Hatsuyuki", "Anaan", "Dace", "N'Tirrk",
    "Groza", "Razor 6", "Tyrion", "Hernstein", "Velasco", "Terran Freighter",
    "Nivelian Freighter", "Rhino", "Midorian Freighter", "Vossk Freighter", "Nuyang II",
    "Cronus", "Phantom", "Ward", "Wraith", "K'Suukk", "Gryphon", "Kinzer", "Veteran",
    "Typhon", "Aegir", "Mantis", "Blue Fyre", "Gator Custom", "Na'Srrk", "Scimitar",
    "Ghost", "Amboss", "Nemesis", "Groza Mk II", "Dark Angel", "Darkzov", "S'Kanarr",
    "Berger Cross Special", "Phantom XT", "Teneta R.E.D.", "VoidX", "Kinzer RS",
    "Bloodstar", "Specter", "Vossk Battlecruiser", "Terran Battlecruiser",
]

CATALOG: dict[str, list[str]] = {
    "primary": PRIMARY_WEAPONS,
    "secondary": SECONDARY_WEAPONS,
    "turret": TURRET_WEAPONS,
    "module": MODULES,
    "ship": SHIPS,
}

# ---------------------------------------------------------------------------
# Alternate wiki slug overrides (item name → wiki page slug, no base URL)
# These are populated when we discover 404s or redirects during the scrape.
# ---------------------------------------------------------------------------
SLUG_OVERRIDES: dict[str, str] = {
    # Known page-name quirks discovered during scraping:
    "Armour Rocket": "Amour_Rocket",        # wiki spells it wrong
    "Micro Gun MK II": "Micro_Gun_MKII",    # wiki omits space before II
    "Groza Mk II": "Groza_MK_II",           # wiki uses MK uppercase
    "Terran Freighter": "Terran_Freighter",
    "Nivelian Freighter": "Nivelian_Freighter",
    "Midorian Freighter": "Midorian_Freighter",
    "Vossk Freighter": "Vossk_Freighter",
    "Terran Battlecruiser": "Terran_Battle_Cruiser",
    "Vossk Battlecruiser": "Vossk_Battle_Cruiser",
    "Na'Srrk": "Na%27srrk",
    "S'koon": "S%27koonn",                  # wiki adds extra n
    "Teneta R.E.D.": "Teneta_R.E.D.",
    "Berger Cross Special": "Berger_Cross_Special",
    "IMT Extract 4.0X": "IMT_Extract%EF%BB%BF_4.0X",  # BOM in wiki title
    # Signatures — all four live on a single "Signature" page
    "Signature Midorian": "Signature",
    "Signature Nivelian": "Signature",
    "Signature Terran": "Signature",
    "Signature Vossk": "Signature",
    # Battlecruisers — redirect pages exist
    "Nemesis": "Nemesis_(ship)",            # 'Nemesis' page may be a weapon disambiguation
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gof2_scraper")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def name_to_slug(name: str) -> str:
    """Convert a display name to a MediaWiki-style URL slug."""
    # MediaWiki convention: spaces → underscores, then percent-encode special chars
    slug = name.replace(" ", "_")
    # Encode apostrophes and quotes
    slug = slug.replace("'", "%27").replace('"', "%22")
    # Encode accented characters properly via urllib
    slug = urllib.parse.quote(slug, safe="%_.-")
    return slug


def item_url(name: str) -> list[str]:
    """Return list of URLs to try for an item (primary + fallbacks)."""
    urls = []
    if name in SLUG_OVERRIDES:
        urls.append(WIKI_BASE + SLUG_OVERRIDES[name])
    primary = WIKI_BASE + name_to_slug(name)
    if primary not in urls:
        urls.append(primary)
    # Fallback: strip double-quotes entirely
    no_quotes = name.replace('"', "").replace("  ", " ").strip()
    if no_quotes != name:
        urls.append(WIKI_BASE + name_to_slug(no_quotes))
    return urls


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=30,
        )
    return _client


def fetch_page(urls: list[str]) -> tuple[str | None, str | None, int]:
    """
    Try URLs in order.  Returns (html, successful_url, http_status).
    If all fail, returns (None, None, last_status).
    """
    client = get_client()
    last_status = 0
    for url in urls:
        try:
            time.sleep(SLEEP_MIN + (SLEEP_MAX - SLEEP_MIN) * 0.5)  # ~750ms
            resp = client.get(url)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.text, url, 200
            log.warning("HTTP %s for %s", resp.status_code, url)
        except Exception as exc:
            log.error("Fetch error for %s: %s", url, exc)
            last_status = -1
    return None, None, last_status


# ---------------------------------------------------------------------------
# Game-version filtering
# ---------------------------------------------------------------------------

def _is_excluded_version(text: str) -> bool:
    t = text.strip().lower()
    return any(excl in t for excl in EXCLUDED_GAME_VERSIONS)


def _is_included_version(text: str) -> bool:
    t = text.strip().lower()
    return any(incl in t for incl in INCLUDED_GAME_VERSIONS)


# ---------------------------------------------------------------------------
# Infobox parsing
# ---------------------------------------------------------------------------

def _clean_text(tag: Tag | str) -> str:
    if isinstance(tag, Tag):
        return tag.get_text(separator=" ", strip=True)
    return str(tag).strip()


def _parse_number(s: str) -> int | float | str:
    """Try to parse s as int or float; return original string on failure."""
    s = s.strip().replace(",", "").replace("$", "").replace("→", "").strip()
    try:
        v = int(s)
        return v
    except ValueError:
        pass
    try:
        v = float(s)
        return v
    except ValueError:
        pass
    return s


def _extract_number(s: str) -> int | float | None:
    """Pull the first number out of a string (ignoring units)."""
    m = re.search(r"[\d,]+\.?\d*", s.replace(",", ""))
    if m:
        try:
            return int(m.group()) if "." not in m.group() else float(m.group())
        except ValueError:
            pass
    return None


def _extract_price_range(s: str) -> dict[str, Any]:
    """Parse 'Known Price Range' cell → {raw, min_credits, max_credits}."""
    nums = [int(x.replace(",", "")) for x in re.findall(r"[\d,]+", s) if int(x.replace(",", "")) > 0]
    result: dict[str, Any] = {"raw": s.strip()}
    if nums:
        result["min_credits"] = min(nums)
        result["max_credits"] = max(nums)
    return result


def parse_infobox(soup: BeautifulSoup, item_name: str) -> dict[str, Any]:
    """
    Extract all infobox key/value rows.
    Returns {raw_infobox: {...}, canonical fields...}
    """
    result: dict[str, Any] = {}
    raw: dict[str, str] = {}
    version_notes: list[str] = []

    table = soup.find("table", class_="infobox")
    if not table:
        log.warning("No infobox table found for %s", item_name)
        return result

    rows = table.find_all("tr")  # type: ignore[arg-type]
    # Detect multi-column infoboxes (some pages have per-game columns)
    # Check if any row has more than 2 cells
    multi_col = any(len(row.find_all(["td", "th"])) > 2 for row in rows)

    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        # Skip the title row
        if len(cells) == 1 and cells[0].get("class") and "infobox-title" in cells[0].get("class", []):
            continue
        # Skip image rows (colspan=2 + image tag)
        if len(cells) == 1 and cells[0].get("colspan"):
            continue

        if multi_col and len(cells) >= 3:
            # Header row with game-version labels
            header_texts = [_clean_text(c) for c in cells]
            # Find which column indices are GoF2 family
            _gof2_cols: list[int] = []
            for i, h in enumerate(header_texts):
                if i == 0:
                    continue  # label column
                if _is_included_version(h) and not _is_excluded_version(h):
                    _gof2_cols.append(i)
            if _gof2_cols:
                version_notes.append(f"multi-col detected, GoF2 columns: {_gof2_cols}")
            continue

        if len(cells) >= 2:
            label_cell = cells[0]
            # Skip if label cell itself looks like an image row in a ship infobox with GoF2HD label
            label_text = _clean_text(label_cell)

            # Filter out excluded game version rows
            if _is_excluded_version(label_text):
                log.debug("Skipping excluded version row '%s' for %s", label_text, item_name)
                continue

            value_cell = cells[1]
            value_text = _clean_text(value_cell)

            # Skip image placeholder rows
            if not value_text or value_text.startswith("<"):
                continue
            if value_cell.find("img") and not value_text:
                continue

            raw[label_text] = value_text

    result["raw_infobox"] = raw
    if version_notes:
        result["_version_notes"] = version_notes

    # ------------------------------------------------------------------
    # Canonicalise well-known fields
    # ------------------------------------------------------------------
    _canon = {}

    def _get(raw_dict: dict[str, str], *keys: str) -> str | None:
        """
        Lookup raw_infobox by key.  Tries exact match (case-insensitive) first,
        then substring match.  This prevents "Speed" from accidentally matching
        "Loading speed" before "Speed".
        """
        raw_lower = {rk.strip().lower(): rv for rk, rv in raw_dict.items()}
        # Pass 1: exact key match
        for k in keys:
            if k.strip().lower() in raw_lower:
                return raw_lower[k.strip().lower()]
        # Pass 2: substring match (k is substring of raw key)
        for k in keys:
            for rk, rv in raw_dict.items():
                if k.strip().lower() in rk.strip().lower():
                    return rv
        return None

    # Tech level
    tl_raw = _get(raw, "Tech Level", "tech level")
    if tl_raw is not None:
        v = _extract_number(tl_raw)
        if v is not None:
            _canon["tech_level"] = int(v)

    # Damage
    dmg_raw = _get(raw, "Damage")
    if dmg_raw is not None:
        v = _extract_number(dmg_raw)
        if v is not None:
            _canon["damage"] = int(v)

    # Loading speed
    ls_raw = _get(raw, "Loading speed", "Loading Speed", "Cooldown", "Recharge speed")
    if ls_raw is not None:
        v = _extract_number(ls_raw)
        if v is not None:
            _canon["loading_speed_ms"] = int(v)

    # DPS
    dps_raw = _get(raw, "Damage per second", "DPS")
    if dps_raw is not None:
        v = _extract_number(dps_raw)
        if v is not None:
            _canon["dps"] = float(v)

    # Range
    range_raw = _get(raw, "Range")
    if range_raw is not None:
        v = _extract_number(range_raw)
        if v is not None:
            _canon["range_m"] = int(v)

    # Projectile speed
    speed_raw = _get(raw, "Speed")
    if speed_raw is not None:
        v = _extract_number(speed_raw)
        if v is not None:
            _canon["projectile_speed_kmh"] = int(v)

    # Effect (boosters, thrusters, gamma shields)
    effect_raw = _get(raw, "Effect", "Speed boost", "Speed Boost")
    if effect_raw is not None:
        # Try percentage first
        pct_m = re.search(r"([\d.]+)\s*%", effect_raw)
        if pct_m:
            pct = float(pct_m.group(1))
            _canon["effect_pct"] = pct
            _canon["effect_multiplier"] = round(1.0 + pct / 100.0, 6)
        else:
            v = _extract_number(effect_raw)
            if v is not None:
                _canon["effect_multiplier"] = float(v)

    # Duration
    dur_raw = _get(raw, "Duration", "Boost duration", "Boost Duration", "Active duration")
    if dur_raw is not None:
        v = _extract_number(dur_raw)
        if v is not None:
            # Normalise to ms: if value looks like seconds (< 1000 and no 'ms' suffix), convert
            raw_lower = dur_raw.lower()
            if "ms" in raw_lower:
                _canon["duration_ms"] = int(v)
            elif "s" in raw_lower and "ms" not in raw_lower and float(v) < 1000:
                _canon["duration_ms"] = int(float(v) * 1000)
            else:
                _canon["duration_ms"] = int(v)

    # Cooldown — for boosters the infobox key "Loading speed" is actually cooldown.
    # We always capture it as loading_speed_ms (see above).
    # Only set cooldown_ms separately if there's a dedicated "Cooldown" key.
    cd_raw = raw.get("Cooldown") or raw.get("Cooldown speed")
    if cd_raw is not None:
        v = _extract_number(cd_raw)
        if v is not None:
            _canon["cooldown_ms"] = int(v)

    # Capacity (shields)
    cap_raw = _get(raw, "Capacity", "Shield capacity")
    if cap_raw is not None:
        v = _extract_number(cap_raw)
        if v is not None:
            _canon["capacity"] = int(v)

    # Recharge speed (shields)
    rech_raw = _get(raw, "Recharge speed", "Recharge rate")
    if rech_raw is not None:
        v = _extract_number(rech_raw)
        if v is not None:
            _canon["recharge_speed_ms"] = int(v)

    # HP per second (repair bots)
    hps_raw = _get(raw, "HP per second", "Repair rate", "Repair speed")
    if hps_raw is not None:
        v = _extract_number(hps_raw)
        if v is not None:
            _canon["hp_per_second"] = float(v)

    # Time to lock (scanners/tractor beams)
    ttl_raw = _get(raw, "Time to lock", "Lock time", "Scan speed", "Scan time")
    if ttl_raw is not None:
        v = _extract_number(ttl_raw)
        if v is not None:
            raw_lower = ttl_raw.lower()
            if "ms" in raw_lower:
                _canon["time_to_lock_s"] = round(float(v) / 1000.0, 3)
            else:
                _canon["time_to_lock_s"] = float(v)

    # Handling
    hdl_raw = _get(raw, "Handling", "Turn rate")
    if hdl_raw is not None:
        v = _extract_number(hdl_raw)
        if v is not None:
            _canon["handling"] = int(v)

    # Armour (ships and armour modules)
    arm_raw = _get(raw, "Armor", "Armour")
    if arm_raw is not None:
        v = _extract_number(arm_raw)
        if v is not None:
            _canon["armour"] = int(v)

    # Cargo
    cargo_raw = _get(raw, "Cargo hold", "Cargo")
    if cargo_raw is not None:
        v = _extract_number(cargo_raw)
        if v is not None:
            _canon["cargo"] = int(v)

    # Ship slots
    prim_raw = _get(raw, "Primary weapons")
    if prim_raw is not None:
        v = _extract_number(prim_raw)
        if v is not None:
            _canon["max_primaries"] = int(v)

    sec_raw = _get(raw, "Secondary weapons")
    if sec_raw is not None:
        v = _extract_number(sec_raw)
        if v is not None:
            _canon["max_secondaries"] = int(v)

    tret_raw = _get(raw, "Turrets", "Turret")
    if tret_raw is not None:
        v = _extract_number(tret_raw)
        if v is not None:
            _canon["max_turrets"] = int(v)

    eq_raw = _get(raw, "Equipment")
    if eq_raw is not None:
        v = _extract_number(eq_raw)
        if v is not None:
            _canon["max_modules"] = int(v)

    # Ship price (not standard "Known Price Range" key)
    # Use exact match: look for a key that IS exactly "Price" (not "Known Price Range")
    price_raw = raw.get("Price")
    if price_raw is not None:
        v = _extract_number(price_raw)
        if v is not None:
            _canon["value"] = int(v)

    # Known Price Range (weapons/modules)
    kpr_raw = _get(raw, "Known Price Range", "Price Range", "Price range")
    if kpr_raw is not None:
        _canon["known_price_range"] = _extract_price_range(kpr_raw)

    # Manufacturer / Faction
    mfr_raw = _get(raw, "Manufacturer", "Faction", "Origin")
    if mfr_raw is not None:
        _canon["faction"] = mfr_raw

    # Magnitude / blast radius (nukes, mines)
    mag_raw = _get(raw, "Magnitude", "Blast radius")
    if mag_raw is not None:
        v = _extract_number(mag_raw)
        if v is not None:
            _canon["magnitude"] = float(v)

    # Turret type → autonomous flag
    type_raw = _get(raw, "Type")
    if type_raw is not None:
        _canon["item_type"] = type_raw
        t_lower = type_raw.lower()
        if "auto" in t_lower and "cannon" not in t_lower and "turret" not in t_lower:
            _canon["autonomous"] = True
        elif "manual" in t_lower:
            _canon["autonomous"] = False

    # Also check explicit "Automatic" key (used by turret infoboxes)
    auto_raw = raw.get("Automatic") or raw.get("automatic")
    if auto_raw is not None:
        _canon["autonomous"] = auto_raw.strip().lower() in ("yes", "true", "1")

    result.update(_canon)
    return result


# ---------------------------------------------------------------------------
# Prose section parsing
# ---------------------------------------------------------------------------

WANTED_SECTIONS = {
    "in-game description", "in game description", "description",
    "notes", "note",
    "mechanics", "mechanic",
    "strategy", "usage", "stats", "function",
    "characteristics",
}


def parse_sections(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract prose sections from wiki page body."""
    content_div = soup.find("div", class_="mw-parser-output")
    if not content_div:
        return {}

    sections: dict[str, Any] = {}
    current_heading: str | None = None
    current_paras: list[str] = []
    current_bullets: list[str] = []

    for el in content_div.children:  # type: ignore[union-attr]
        if not isinstance(el, Tag):
            continue
        tag_name = el.name.lower() if el.name else ""

        if tag_name in ("h2", "h3"):
            # Save previous section
            if current_heading:
                _save_section(sections, current_heading, current_paras, current_bullets)
            current_heading = el.get_text(strip=True).lower()
            current_paras = []
            current_bullets = []

        elif tag_name == "p" and current_heading and current_heading in WANTED_SECTIONS:
            text = el.get_text(separator=" ", strip=True)
            if text:
                current_paras.append(text)

        elif tag_name == "ul" and current_heading and current_heading in WANTED_SECTIONS:
            for li in el.find_all("li", recursive=False):
                current_bullets.append(li.get_text(separator=" ", strip=True))

        elif tag_name == "table":
            # Don't recurse into infobox or nav tables mid-section
            continue

    if current_heading:
        _save_section(sections, current_heading, current_paras, current_bullets)

    # Also capture in-game description if it appears as italic para before first h2
    if "in-game description" not in sections:
        first_p = content_div.find("p")  # type: ignore[union-attr]
        if first_p:
            em = first_p.find(["i", "em"])
            if em:
                sections["description"] = em.get_text(separator=" ", strip=True)

    return sections


def _save_section(
    sections: dict[str, Any],
    heading: str,
    paras: list[str],
    bullets: list[str],
) -> None:
    if not paras and not bullets:
        return
    key = heading.replace(" ", "_").replace("-", "_").replace("'", "")
    if "description" in heading or "in_game" in heading:
        sections["description"] = " ".join(paras) or (bullets[0] if bullets else "")
    elif "note" in heading:
        sections["notes"] = bullets or paras
    elif "mechanic" in heading:
        sections["mechanics_text"] = " ".join(paras)
    else:
        sections[key] = (bullets or []) + paras if bullets else paras


# ---------------------------------------------------------------------------
# Categories from wiki page
# ---------------------------------------------------------------------------

def parse_categories(soup: BeautifulSoup) -> list[str]:
    catlinks = soup.find(id="mw-normal-catlinks")
    if not catlinks:
        return []
    return [a.get_text(strip=True) for a in catlinks.find_all("a")[1:]]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Per-item scrape
# ---------------------------------------------------------------------------

def scrape_item(name: str, category: str) -> dict[str, Any]:
    """Fetch and parse a single item page."""
    urls = item_url(name)
    html, successful_url, status = fetch_page(urls)

    if html is None:
        log.error("FAILED %s | HTTP %s | urls: %s", name, status, urls)
        return {
            "_status": "fetch_failed",
            "_name": name,
            "_category": category,
            "_url_tried": urls,
            "_http_status": status,
        }

    soup = BeautifulSoup(html, "lxml")
    data: dict[str, Any] = {
        "_status": "ok",
        "_name": name,
        "_category": category,
        "_url": successful_url,
        "_scraped_at": datetime.utcnow().isoformat() + "Z",
    }

    # Infobox
    infobox_data = parse_infobox(soup, name)
    data.update(infobox_data)

    # Prose sections
    sections = parse_sections(soup)
    data.update(sections)

    # Categories
    cats = parse_categories(soup)
    data["_wiki_categories"] = cats

    # Detect GoF2 family membership from categories
    is_gof2 = any(_is_included_version(c) for c in cats)
    is_excluded = any(_is_excluded_version(c) for c in cats)
    if is_excluded and not is_gof2:
        data["_status"] = "excluded_version"
        log.warning("Item %s appears to be non-GoF2 only (cats: %s)", name, cats)

    if not infobox_data.get("raw_infobox"):
        data["_status"] = "no_infobox"
        log.warning("No infobox found for %s at %s", name, successful_url)

    return data


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def slug_for_filename(name: str) -> str:
    """Safe filename from item name."""
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[\s]+", "_", s).lower()
    return s


def save_item(data: dict[str, Any], category: str) -> Path:
    out_dir = OUTPUT_DIR / category
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = slug_for_filename(data["_name"]) + ".json"
    path = out_dir / fname
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# DB diff helpers
# ---------------------------------------------------------------------------

DB_URL = "postgresql://bounty:bounty@bountybot-db:5432/bountydb"

# DB field → canonical wiki key mapping for comparison
DB_FIELD_MAP = {
    "tech_level": "tech_level",
    "dps": "dps",
    "value": "value",
    "armour": "armour",
    "cargo": "cargo",
    "handling": "handling",
    "max_primaries": "max_primaries",
    "max_secondaries": "max_secondaries",
    "max_turrets": "max_turrets",
    "max_modules": "max_modules",
}


def _severity(db_val: Any, wiki_val: Any, field: str) -> str:
    """Assess severity of a discrepancy."""
    try:
        diff = abs(float(db_val) - float(wiki_val))
        if field in ("tech_level", "max_primaries", "max_secondaries", "max_turrets", "max_modules", "armour"):
            if diff == 0:
                return "ok"
            if diff == 1:
                return "warn"
            return "error"
        if field in ("dps",):
            if diff < 0.1:
                return "info"
            if diff < 1.0:
                return "warn"
            return "error"
        if diff == 0:
            return "ok"
        if diff / (abs(float(db_val)) + 1e-9) < 0.05:
            return "info"
        if diff / (abs(float(db_val)) + 1e-9) < 0.20:
            return "warn"
        return "error"
    except (TypeError, ValueError):
        return "warn" if str(db_val) != str(wiki_val) else "ok"


def build_diff_report(combined: dict[str, list[dict[str, Any]]]) -> str:
    """
    Query the DB via psycopg2 (sync) and build a markdown diff table.
    Falls back to import_data/ JSON files if DB is unreachable.
    """
    import glob

    # Try to load DB data from import_data/ JSON files (always available)
    import_data_root = Path("/proj/services/bot-core/import_data")

    db_items: dict[str, dict[str, Any]] = {}

    cat_dirs = {
        "primary": "primary_weapon",
        "secondary": "secondary_weapon",
        "turret": "turret_weapon",
        "module": "module",
        "ship": "ship",
    }

    for cat, subdir in cat_dirs.items():
        cat_dir = import_data_root / subdir
        if not cat_dir.exists():
            continue
        for jf in sorted(cat_dir.glob("*.json")):
            try:
                obj = json.loads(jf.read_text(encoding="utf-8"))
                name = obj.get("name", "")
                if name:
                    db_items[name.lower()] = obj
            except Exception as e:
                log.warning("Could not load %s: %s", jf, e)

    # Also try live DB
    try:
        import psycopg2  # type: ignore

        conn = psycopg2.connect(DB_URL, connect_timeout=5)
        cur = conn.cursor()
        # Pull relevant fields from item, primary_weapon, secondary_weapon, turret_weapon, module, ship tables
        try:
            cur.execute("""
                SELECT i.name, i.type, pw.tech_level as tech_level, pw.dps as dps, i.value
                FROM item i
                LEFT JOIN primary_weapon pw ON pw.id = i.id
                WHERE i.type = 'primary_weapon'
            """)
            for row in cur.fetchall():
                name_key = (row[0] or "").lower()
                db_items[name_key] = db_items.get(name_key, {})
                db_items[name_key].update({
                    "name": row[0],
                    "techLevel": row[2],
                    "dps": row[3],
                    "value": row[4],
                })
        except Exception as e:
            log.warning("DB query error (primary_weapon): %s", e)
        conn.close()
        log.info("DB connection successful — used live DB data for diff")
    except Exception as e:
        log.info("DB not reachable (%s) — using import_data/ JSON files for diff", e)

    lines = ["# GoF2 Wiki vs DB Discrepancy Report", f"\n_Generated: {datetime.utcnow().isoformat()}Z_\n"]

    for cat, items in combined.items():
        rows = []
        for item_data in items:
            if item_data.get("_status") == "fetch_failed":
                continue
            name = item_data["_name"]
            db = db_items.get(name.lower(), {})
            if not db:
                continue

            # Map DB fields
            db_mapped = {
                "tech_level": db.get("techLevel"),
                "dps": db.get("dps"),
                "value": db.get("value"),
                "armour": db.get("armour"),
                "cargo": db.get("cargo"),
                "handling": db.get("handling"),
                "max_primaries": db.get("maxPrimaries"),
                "max_secondaries": db.get("maxSecondaries"),
                "max_turrets": db.get("maxTurrets"),
                "max_modules": db.get("maxModules"),
            }

            for field, db_val in db_mapped.items():
                wiki_val = item_data.get(field)
                if db_val is None or wiki_val is None:
                    continue
                sev = _severity(db_val, wiki_val, field)
                if sev != "ok":
                    rows.append((name, field, db_val, wiki_val, sev))

        if rows:
            lines.append(f"\n## {cat.title()} Weapons / Items\n")
            lines.append("| Item | Field | DB Value | Wiki Value | Severity |")
            lines.append("|------|-------|----------|------------|----------|")
            # Sort by severity: error first, then warn, then info
            sev_order = {"error": 0, "warn": 1, "info": 2}
            rows.sort(key=lambda r: (sev_order.get(r[4], 9), r[0]))
            for name, field, db_val, wiki_val, sev in rows:
                lines.append(f"| {name} | {field} | {db_val} | {wiki_val} | **{sev}** |")

    if len(lines) <= 3:
        lines.append("\n_No discrepancies found (or no matching DB items)._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main scrape flow
# ---------------------------------------------------------------------------

def scrape_category(cat_name: str, names: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scrape all items in a category. Returns (results, stats)."""
    results = []
    stats = {"found": 0, "partial": 0, "failed": 0}

    log.info("=== Scraping category: %s (%d items) ===", cat_name, len(names))
    for i, name in enumerate(names, 1):
        log.info("[%s %d/%d] %s", cat_name, i, len(names), name)
        data = scrape_item(name, cat_name)
        results.append(data)
        save_item(data, cat_name)

        status = data.get("_status", "unknown")
        if status == "ok":
            if data.get("raw_infobox"):
                stats["found"] += 1
            else:
                stats["partial"] += 1
        elif status in ("no_infobox", "excluded_version"):
            stats["partial"] += 1
        else:
            stats["failed"] += 1

        # Polite delay
        time.sleep(SLEEP_MIN + (SLEEP_MAX - SLEEP_MIN) * 0.5)

    return results, stats


def run_scrape(categories: list[str]) -> None:
    combined: dict[str, list[dict[str, Any]]] = {}
    total_stats: dict[str, int] = {"found": 0, "partial": 0, "failed": 0}

    for cat in categories:
        names = CATALOG[cat]
        results, stats = scrape_category(cat, names)
        combined[cat] = results
        for k in total_stats:
            total_stats[k] += stats[k]

    # Save consolidated JSON
    COMBINED_FILE.parent.mkdir(parents=True, exist_ok=True)
    COMBINED_FILE.write_text(
        json.dumps(combined, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Saved combined JSON: %s", COMBINED_FILE)

    # Build + save diff report
    diff_md = build_diff_report(combined)
    DIFF_FILE.write_text(diff_md, encoding="utf-8")
    log.info("Saved diff report: %s", DIFF_FILE)

    # Summary
    total = sum(len(CATALOG[c]) for c in categories)
    log.info(
        "=== SUMMARY === Total: %d | Found: %d | Partial: %d | Failed: %d",
        total, total_stats["found"], total_stats["partial"], total_stats["failed"],
    )


# ---------------------------------------------------------------------------
# Spot-check validation
# ---------------------------------------------------------------------------

SPOT_CHECKS = {
    "128MJ Railgun": {
        "tech_level": 6,
        "damage": 3,
        "loading_speed_ms": 120,
        "dps": 25.0,
        "range_m": 2500,
        "projectile_speed_kmh": 6500,
    },
    "Linear Boost": {
        "tech_level": 4,
        "effect_pct": 60.0,
        # effect_multiplier derivable: 1.6
    },
    "Telta Quickscan": {
        "tech_level": 2,
        # time_to_lock_s: some value
    },
    "Pendular Thrust": {
        "tech_level": 3,
        # handling multiplier value
    },
    "Betty": {
        "armour": 95,
        "cargo": 25,
        "max_primaries": 1,
        "max_secondaries": 1,
        "max_turrets": 0,
        "max_modules": 3,
        "handling": 120,
    },
}


def run_spot_checks() -> None:
    log.info("=== Running spot-checks ===")
    all_pass = True
    for name, expected in SPOT_CHECKS.items():
        # Determine category
        cat = None
        for c, items in CATALOG.items():
            if name in items:
                cat = c
                break
        if cat is None:
            log.error("Spot-check: %s not found in any catalog", name)
            continue

        log.info("Spot-checking %s (category: %s)", name, cat)
        data = scrape_item(name, cat)
        save_item(data, cat)

        if data.get("_status") == "fetch_failed":
            log.error("FAIL spot-check %s: fetch failed", name)
            all_pass = False
            continue

        for field, exp_val in expected.items():
            got = data.get(field)
            if got is None:
                log.error("FAIL spot-check %s.%s: missing (expected %s)", name, field, exp_val)
                all_pass = False
            elif abs(float(got) - float(exp_val)) > 0.01:
                log.error(
                    "FAIL spot-check %s.%s: got %s, expected %s",
                    name, field, got, exp_val,
                )
                all_pass = False
            else:
                log.info("  PASS %s.%s = %s", name, field, got)

    if all_pass:
        log.info("=== All spot-checks PASSED ===")
    else:
        log.warning("=== Some spot-checks FAILED — review log ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape GoF2 wiki item data")
    parser.add_argument(
        "--category",
        choices=["primary", "secondary", "turret", "module", "ship", "all"],
        default="all",
        help="Which category to scrape (default: all)",
    )
    parser.add_argument(
        "--spot-check",
        action="store_true",
        help="Run spot-checks only (5 known items)",
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help="Re-run DB diff from existing /tmp/gof2_wiki_combined.json",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.diff_only:
        if not COMBINED_FILE.exists():
            log.error("No combined JSON found at %s — run a scrape first", COMBINED_FILE)
            sys.exit(1)
        combined = json.loads(COMBINED_FILE.read_text(encoding="utf-8"))
        diff_md = build_diff_report(combined)
        DIFF_FILE.write_text(diff_md, encoding="utf-8")
        log.info("Diff report saved: %s", DIFF_FILE)
        return

    if args.spot_check:
        run_spot_checks()
        return

    if args.category == "all":
        categories = list(CATALOG.keys())
    else:
        categories = [args.category]

    run_scrape(categories)


if __name__ == "__main__":
    main()
