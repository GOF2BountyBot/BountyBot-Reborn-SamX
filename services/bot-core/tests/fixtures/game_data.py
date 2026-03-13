"""Shared test fixtures seeded from real game data in import_data/.

Each function returns a list of SimpleNamespace objects whose attributes
exactly mirror the SQLAlchemy model columns for the corresponding model.
This means tests can use these objects without a real database session while
still working with realistic, non-invented data.

All data is derived verbatim from the JSON files under
``services/bot-core/import_data/``.
"""

from __future__ import annotations

from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Ships  (models/ship.py)
# ---------------------------------------------------------------------------
# Columns: id, name, aliases, armour, built_in, cargo, compatible_skins,
#          emoji, icon, manufacturer, handling, shop_spawn_rate, skinnable,
#          max_modules, max_primaries, max_secondaries, max_turrets,
#          builtin_modules, texture_regions, save_due, model, norm_spec,
#          value, wiki, assets
# ---------------------------------------------------------------------------

def get_seed_ships() -> list[SimpleNamespace]:
    """Return 5 ships sourced from import_data/ship/*.json."""
    return [
        SimpleNamespace(
            id=1,
            name="Betty",
            aliases=["betty"],
            armour=95,
            built_in=False,
            cargo=25,
            compatible_skins={
                "urban-camo": "https://i.postimg.cc/z3mb73zL/betty-urban-camo.png",
                "onyx": "https://i.postimg.cc/7YTSzrHG/betty-onyx.png",
            },
            emoji="<:betty:723705372606726155>",
            icon="https://i.postimg.cc/bN5c6Mtj/betty.png",
            manufacturer="midorian",
            handling=120,
            shop_spawn_rate=9.585,
            skinnable=True,
            max_modules=3,
            max_primaries=1,
            max_secondaries=1,
            max_turrets=0,
            builtin_modules=None,
            texture_regions=1,
            save_due=False,
            model="/app/game-objects/Midorian/Betty.bbship/Betty_Full.obj",
            norm_spec="/app/game-objects/Midorian/Betty.bbship/ship_000_midorian_normal_specular.bmp",
            value=16038,
            wiki="https://galaxyonfire.fandom.com/wiki/Betty",
            assets=[
                "/app/game-objects/Midorian/Betty.bbship/Betty_Full.mtl",
                "/app/game-objects/Midorian/Betty.bbship/Betty_Full.obj",
            ],
        ),
        SimpleNamespace(
            id=2,
            name="Groza",
            aliases=["groza"],
            armour=160,
            built_in=False,
            cargo=130,
            compatible_skins={
                "urban-camo": "https://i.postimg.cc/fW6jbRcS/groza-urban-camo.png",
                "lava": "https://i.postimg.cc/5yHYVL7K/groza-lava.png",
            },
            emoji="<:groza:723705237038432258>",
            icon="https://i.postimg.cc/FRvVh92X/groza.png",
            manufacturer="terran",
            handling=117,
            shop_spawn_rate=8.416,
            skinnable=True,
            max_modules=8,
            max_primaries=3,
            max_secondaries=3,
            max_turrets=0,
            builtin_modules=None,
            texture_regions=2,
            save_due=False,
            model="/app/game-objects/Terran/Groza.bbship/Groza_Full.obj",
            norm_spec="/app/game-objects/Terran/Groza.bbship/ship_022_terran_normal_specular.bmp",
            value=251600,
            wiki="https://galaxyonfire.fandom.com/wiki/Groza",
            assets=[
                "/app/game-objects/Terran/Groza.bbship/Groza_Full.mtl",
                "/app/game-objects/Terran/Groza.bbship/Groza_Full.obj",
            ],
        ),
        SimpleNamespace(
            id=3,
            name="Ghost",
            aliases=["ghost"],
            armour=530,
            built_in=False,
            cargo=50,
            compatible_skins={
                "urban-camo": "https://i.postimg.cc/138WjjrD/ghost-urban-camo.png",
                "space": "https://i.postimg.cc/pXXcDT9G/ghost-space.png",
            },
            emoji="<:ghost:723705339282718792>",
            icon="https://i.postimg.cc/yxMqN3B8/ghost.png",
            manufacturer="nivelian",
            handling=135,
            shop_spawn_rate=8.416,
            skinnable=True,
            max_modules=14,
            max_primaries=4,
            max_secondaries=2,
            max_turrets=0,
            builtin_modules=None,
            texture_regions=1,
            save_due=False,
            model="/app/game-objects/Nivelian/Ghost.bbship/Ghost_Full.obj",
            norm_spec="/app/game-objects/Nivelian/Ghost.bbship/ship_061_elite_nivelian_prototype_normal_specular.bmp",
            value=6000000,
            wiki="https://galaxyonfire.fandom.com/wiki/Ghost",
            assets=[
                "/app/game-objects/Nivelian/Ghost.bbship/Ghost_Full.mtl",
                "/app/game-objects/Nivelian/Ghost.bbship/Ghost_Full.obj",
            ],
        ),
        SimpleNamespace(
            id=4,
            name="Mantis",
            aliases=["mantis"],
            armour=240,
            built_in=False,
            cargo=75,
            compatible_skins={
                "onyx": "https://i.postimg.cc/FHD16TGg/mantis-onyx.png",
            },
            emoji="<:mantis:723706166307192892>",
            icon="https://i.postimg.cc/D0dQSt34/mantis.png",
            manufacturer="pirate",
            handling=117,
            shop_spawn_rate=7.214,
            skinnable=True,
            max_modules=12,
            max_primaries=4,
            max_secondaries=4,
            max_turrets=0,
            builtin_modules=None,
            texture_regions=2,
            save_due=False,
            model="/app/game-objects/Pirate/Mantis.bbship/Mantis_Full.obj",
            norm_spec="/app/game-objects/Pirate/Mantis.bbship/ship_029_pirates_normal_specular.bmp",
            value=4136800,
            wiki="https://galaxyonfire.fandom.com/wiki/Mantis",
            assets=[
                "/app/game-objects/Pirate/Mantis.bbship/Mantis_Full.mtl",
                "/app/game-objects/Pirate/Mantis.bbship/Mantis_Full.obj",
            ],
        ),
        SimpleNamespace(
            id=5,
            name="Vossk Freighter",
            aliases=["vossk freighter"],
            armour=1400,
            built_in=False,
            cargo=700,
            compatible_skins={
                "cargo": "https://i.postimg.cc/MTYNS33t/vossk-freighter-cargo.png",
            },
            emoji="<:Vossk_Freighter:769692268424200202>",
            icon="https://i.postimg.cc/L6WDSkJZ/vossk-freighter.png",
            manufacturer="vossk",
            handling=30,
            shop_spawn_rate=7.214,
            skinnable=True,
            max_modules=8,
            max_primaries=0,
            max_secondaries=0,
            max_turrets=0,
            builtin_modules=None,
            texture_regions=1,
            save_due=False,
            model="/app/game-objects/Vossk/Cargo_Vossk.bbship/Cargo_Vossk_Full.obj",
            norm_spec=None,
            value=700000,
            wiki="https://galaxyonfire.fandom.com/wiki/Freighter",
            assets=[
                "/app/game-objects/Vossk/Cargo_Vossk.bbship/Cargo_Vossk_Full.mtl",
                "/app/game-objects/Vossk/Cargo_Vossk.bbship/Cargo_Vossk_Full.obj",
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Primary Weapons  (models/primary_weapon.py)
# ---------------------------------------------------------------------------
# Columns (Item): id, name, aliases, built_in, emoji, icon, value, wiki, type
# Columns (Weapon): tech_level, extra_atts
# Columns (PrimaryWeapon): dps
# ---------------------------------------------------------------------------

def get_seed_primary_weapons() -> list[SimpleNamespace]:
    """Return 5 primary weapons sourced from import_data/primary_weapon/*.json.

    Tech levels span 1, 4, 5, 9, 9 for good coverage.
    """
    return [
        # tech_level=1  (import_data/primary_weapon/auto_cannons.micro_gun_mk_i.json)
        SimpleNamespace(
            id=101,
            name="Micro Gun MK I",
            aliases=["Micro MK I", "Micro I", "Micro Gun Mk 1", "Micro MK 1", "Micro 1"],
            built_in=False,
            emoji="<:microgunmki:723709599269519400>",
            icon="https://i.postimg.cc/SxLdXYqt/micro-gun-mki.png",
            value=2577,
            wiki="https://galaxyonfire.fandom.com/wiki/Micro_Gun_MK_I",
            type="primary_weapon",
            tech_level=1,
            extra_atts={"subtype": "auto_cannons"},
            dps=9.09,
        ),
        # tech_level=4  (import_data/primary_weapon/emp_blasters.luna_emp_mk_i.json)
        SimpleNamespace(
            id=102,
            name="Luna EMP Mk I",
            aliases=["Luna Mk I", "Luna EMP", "Luna", "EMP Mk I", "EMP I"],
            built_in=False,
            emoji="<:lunaempmki:723709689149390888>",
            icon="https://i.postimg.cc/RCRvTXdM/luna-emp-mk-i.png",
            value=5942,
            wiki="https://galaxyonfire.fandom.com/wiki/Luna_EMP_Mk_I",
            type="primary_weapon",
            tech_level=4,
            extra_atts={"subtype": "emp_blasters"},
            dps=8.57,
        ),
        # tech_level=5  (import_data/primary_weapon/blaster_lasers.berger_focus_i.json)
        SimpleNamespace(
            id=103,
            name="Berger Focus I",
            aliases=["berger focusberger focus 1focus 1focus I"],
            built_in=False,
            emoji="<:bergerfocusi:723709563265876051>",
            icon="https://i.postimg.cc/jdc991nj/berger-focus-i.png",
            value=22816,
            wiki="https://galaxyonfire.fandom.com/wiki/Berger_Focus_I",
            type="primary_weapon",
            tech_level=5,
            extra_atts={"subtype": "blaster_lasers"},
            dps=17.77,
        ),
        # tech_level=9  (import_data/primary_weapon/beam_lasers.m6_a3_wolverine.json)
        SimpleNamespace(
            id=104,
            name='M6 A3 "Wolverine"',
            aliases=["M6 A3", "Wolverine", "M6 Wolverine", "A3 Wolverine"],
            built_in=False,
            emoji="<:m6a3wolverine:723709564255600690>",
            icon="https://i.postimg.cc/PrS3K80p/m6-a3-wolverine.png",
            value=68725,
            wiki='https://galaxyonfire.fandom.com/wiki/M6_A3_"Wolverine"',
            type="primary_weapon",
            tech_level=9,
            extra_atts={"subtype": "beam_lasers"},
            dps=34.0,
        ),
        # tech_level=9  (import_data/primary_weapon/thermal_fusion_cannons.sunfire_o50.json)
        SimpleNamespace(
            id=105,
            name="SunFire o50",
            aliases=["o50", "SunFire"],
            built_in=False,
            emoji="<:sunfireo50:723709644823986187>",
            icon="https://i.postimg.cc/59kR1zNB/sunfire-o50.png",
            value=183413,
            wiki="https://galaxyonfire.fandom.com/wiki/SunFire_o50",
            type="primary_weapon",
            tech_level=9,
            extra_atts={"subtype": "thermal_fusion_cannons"},
            dps=41.66,
        ),
    ]


# ---------------------------------------------------------------------------
# Secondary Weapons  (models/secondary_weapon.py)
# ---------------------------------------------------------------------------
# Columns (Item): id, name, aliases, built_in, emoji, icon, value, wiki, type
# Columns (Weapon): tech_level, extra_atts
# Columns (SecondaryWeapon): damage, loading_speed
# ---------------------------------------------------------------------------

def get_seed_secondary_weapons() -> list[SimpleNamespace]:
    """Return 4 secondary weapons sourced from import_data/secondary_weapon/*.json."""
    return [
        # import_data/secondary_weapon/missiles.intelli_jet.json
        SimpleNamespace(
            id=201,
            name="Intelli Jet",
            aliases=["intellijet"],
            built_in=False,
            emoji=None,
            icon="https://i.postimg.cc/t4gQDbTk/intelli-jet.png",
            value=0,
            wiki="",
            type="secondary_weapon",
            tech_level=None,
            extra_atts={"subtype": "missiles"},
            damage=0,
            loading_speed=0,
        ),
        # import_data/secondary_weapon/rockets.emp_rocket_mk_i.json
        SimpleNamespace(
            id=202,
            name="EMP Rocket Mk I",
            aliases=["EMP Mk I", "EMP Mk 1", "EMP Rocket Mark I", "EMP Mark I"],
            built_in=False,
            emoji=None,
            icon="https://i.postimg.cc/RC1kBhJG/emp-rocket-mk-i.png",
            value=0,
            wiki="",
            type="secondary_weapon",
            tech_level=None,
            extra_atts={"subtype": "rockets"},
            damage=0,
            loading_speed=0,
        ),
        # import_data/secondary_weapon/cluster_missiles.garuda-iv.json
        SimpleNamespace(
            id=203,
            name="Garuda-IV",
            aliases=["garuda", "garuda IV", "garuda 4", "garuda-4"],
            built_in=False,
            emoji=None,
            icon="https://i.postimg.cc/g0fDwVQR/garuda-iv.png",
            value=0,
            wiki="",
            type="secondary_weapon",
            tech_level=None,
            extra_atts={"subtype": "cluster_missiles"},
            damage=0,
            loading_speed=0,
        ),
        # import_data/secondary_weapon/nukes.amr_tormentor.json
        SimpleNamespace(
            id=204,
            name="AMR Tormentor",
            aliases=["Tormentor"],
            built_in=False,
            emoji=None,
            icon="https://i.postimg.cc/cL1ZjYDQ/amr-tormentor.png",
            value=0,
            wiki="",
            type="secondary_weapon",
            tech_level=None,
            extra_atts={"subtype": "nukes"},
            damage=0,
            loading_speed=0,
        ),
    ]


# ---------------------------------------------------------------------------
# Turret Weapons  (models/turret_weapon.py)
# ---------------------------------------------------------------------------
# Columns (Item): id, name, aliases, built_in, emoji, icon, value, wiki, type
# Columns (Weapon): tech_level, extra_atts
# Columns (TurretWeapon): dps, automatic
# ---------------------------------------------------------------------------

def get_seed_turret_weapons() -> list[SimpleNamespace]:
    """Return 4 turret weapons sourced from import_data/turret_weapon/*.json.

    Tech levels: 5, 5, 6, 9 for good coverage.
    """
    return [
        # tech_level=5  (import_data/turret_weapon/auto.berger_agt_20mm.json)
        SimpleNamespace(
            id=301,
            name="Berger AGT 20mm",
            aliases=["Berger AGT", "Berger 20mm", "20mm", "AGT", "AGT 20mm"],
            built_in=False,
            emoji="<:bergeragt20mm:723707369552347216>",
            icon="https://i.postimg.cc/Gt19stbq/berger-agt-20mm.png",
            value=227040,
            wiki="https://galaxyonfire.fandom.com/wiki/Berger_AGT_20mm",
            type="turret_weapon",
            tech_level=5,
            extra_atts={"subtype": "auto"},
            dps=40.0,
            automatic=True,
        ),
        # tech_level=5  (import_data/turret_weapon/manual.hammerhead_d1.json)
        SimpleNamespace(
            id=302,
            name="Hammerhead D1",
            aliases=["D1"],
            built_in=False,
            emoji="<:hammerheadd1:723707422065033277>",
            icon="https://i.postimg.cc/qMv4DyD2/hammerhead-d1.png",
            value=24174,
            wiki="https://galaxyonfire.fandom.com/wiki/Hammerhead_D1",
            type="turret_weapon",
            tech_level=5,
            extra_atts={"subtype": "manual"},
            dps=20.0,
            automatic=False,
        ),
        # tech_level=6  (import_data/turret_weapon/auto.skuld_at_xr.json)
        SimpleNamespace(
            id=303,
            name="Skuld AT XR",
            aliases=["Skuld XR", "Skuld", "AT XR", "Skuld AT", "XR"],
            built_in=False,
            emoji="<:skuldatxr:723707369573449809>",
            icon="https://i.postimg.cc/X74pjrHv/skuld-at-xr.png",
            value=407793,
            wiki="https://galaxyonfire.fandom.com/wiki/Skuld_AT_XR",
            type="turret_weapon",
            tech_level=6,
            extra_atts={"subtype": "auto"},
            dps=47.36,
            automatic=True,
        ),
        # tech_level=9  (import_data/turret_weapon/plasma_collectors.pe_proton.json)
        SimpleNamespace(
            id=304,
            name="PE Proton",
            aliases=["Proton"],
            built_in=False,
            emoji="<:peproton:723707456768704533>",
            icon="https://i.postimg.cc/Px15hH1Z/pe-proton.png",
            value=43856,
            wiki="https://galaxyonfire.fandom.com/wiki/PE_Proton",
            type="turret_weapon",
            tech_level=9,
            extra_atts={"subtype": "plasma_collectors"},
            dps=0.0,
            automatic=False,
        ),
    ]


# ---------------------------------------------------------------------------
# Modules  (models/module.py)
# ---------------------------------------------------------------------------
# Columns (Item): id, name, aliases, built_in, emoji, icon, value, wiki, type
# Columns (Module): tech_level, max_equipped, extra_atts
# ---------------------------------------------------------------------------

def get_seed_modules() -> list[SimpleNamespace]:
    """Return 5 modules sourced from import_data/module/*.json.

    Tech levels: 1, 4, 5, 7, 10 for good coverage.
    """
    return [
        # tech_level=1  (import_data/module/armour.e2_exoclad.json)
        SimpleNamespace(
            id=401,
            name="E2 Exoclad",
            aliases=["E2", "Exoclad", "Exoclad E2"],
            built_in=False,
            emoji="<:e2exoclad:723706394716536842>",
            icon="https://i.postimg.cc/FFZJJbJS/e2-exoclad.png",
            value=1070,
            wiki="https://galaxyonfire.fandom.com/wiki/E2_Exoclad",
            type="module",
            tech_level=1,
            max_equipped=None,
            extra_atts={"armour": 40, "module_type": "ArmourModule"},
        ),
        # tech_level=4  (import_data/module/repair_bots.ketar_repair_bot.json)
        SimpleNamespace(
            id=402,
            name="Ketar Repair Bot",
            aliases=["Repair Bot", "Ketar Bot"],
            built_in=False,
            emoji="<:ketarrepairbot:723706704373481543>",
            icon="https://i.postimg.cc/kXGD60Cr/ketar-repair-bot.png",
            value=15285,
            wiki="https://galaxyonfire.fandom.com/wiki/Ketar_Repair_Bot",
            type="module",
            tech_level=4,
            max_equipped=None,
            extra_atts={"HPps": 7, "module_type": "RepairBotModule"},
        ),
        # tech_level=5  (import_data/module/boosters.cyclotron_boost.json)
        SimpleNamespace(
            id=403,
            name="Cyclotron Boost",
            aliases=["Cyclotron"],
            built_in=False,
            emoji="<:cyclotronboost:723706427448754178>",
            icon="https://i.postimg.cc/QCb581cp/cyclotron-boost.png",
            value=11553,
            wiki="https://galaxyonfire.fandom.com/wiki/Cyclotron_Boost",
            type="module",
            tech_level=5,
            max_equipped=None,
            extra_atts={"duration": 4.4, "effect": 1.8, "module_type": "BoosterModule"},
        ),
        # tech_level=5  (import_data/module/thrusters.dozzt_thrust.json)
        SimpleNamespace(
            id=404,
            name="D'ozzt Thrust",
            aliases=[],
            built_in=False,
            emoji="<:dozztthrust:723707097765642351>",
            icon="https://i.postimg.cc/qRt4MTnQ/d-ozzt-thrust.png",
            value=5762,
            wiki="https://galaxyonfire.fandom.com/wiki/D%27ozzt_Thrust",
            type="module",
            tech_level=5,
            max_equipped=None,
            extra_atts={"handlingMultiplier": 1.7, "module_type": "ThrusterModule"},
        ),
        # tech_level=7  (import_data/module/shields.beamshield_ii.json)
        SimpleNamespace(
            id=405,
            name="Beamshield II",
            aliases=["Beamshield 2", "Beamshield"],
            built_in=False,
            emoji="<:beamshield:723706780202303676>",
            icon="https://i.postimg.cc/gkKFpJQm/beamshield-ii.png",
            value=39331,
            wiki="https://galaxyonfire.fandom.com/wiki/Beamshield_II",
            type="module",
            tech_level=7,
            max_equipped=None,
            extra_atts={"shield": 150, "module_type": "ShieldModule"},
        ),
        # tech_level=10  (import_data/module/shields.particle_shield.json)
        SimpleNamespace(
            id=406,
            name="Particle Shield",
            aliases=["Particle"],
            built_in=False,
            emoji="<:particleshield:723706780441640982>",
            icon="https://i.postimg.cc/V6tpVLXy/particle-shield.png",
            value=189194,
            wiki="https://galaxyonfire.fandom.com/wiki/Particle_Shield",
            type="module",
            tech_level=10,
            max_equipped=None,
            extra_atts={"shield": 380, "module_type": "ShieldModule"},
        ),
    ]


# ---------------------------------------------------------------------------
# Criminals  (models/criminal.py)
# ---------------------------------------------------------------------------
# Columns: id, name, aliases, built_in, faction, icon, is_player, wiki
# ---------------------------------------------------------------------------

def get_seed_criminals() -> list[SimpleNamespace]:
    """Return 5 criminals sourced from import_data/criminal/*.json."""
    return [
        # import_data/criminal/terran.trent_jameson.json
        SimpleNamespace(
            id=501,
            name="Trent Jameson",
            aliases=["trent", "jameson"],
            built_in=False,
            faction="terran",
            icon="https://i.postimg.cc/jdfwWy8f/trent-jameson.png",
            is_player=False,
            wiki="https://galaxyonfire.fandom.com/wiki/Trent_Jameson",
        ),
        # import_data/criminal/nivelian.borsul_tarand.json
        SimpleNamespace(
            id=502,
            name="Borsul Tarand",
            aliases=["borsul", "tarand"],
            built_in=False,
            faction="nivelian",
            icon="https://i.postimg.cc/RZf7zkrj/borsul-tarand.png",
            is_player=False,
            wiki="https://galaxyonfire.fandom.com/wiki/Borsul_Tarand",
        ),
        # import_data/criminal/vossk.vortt_baskk.json
        SimpleNamespace(
            id=503,
            name="Vortt Baskk",
            aliases=["vortt", "baskk"],
            built_in=False,
            faction="vossk",
            icon="https://i.postimg.cc/d0sLSpJR/vortt-baskk.png",
            is_player=False,
            wiki="https://galaxyonfire.fandom.com/wiki/Vortt_Baskk",
        ),
        # import_data/criminal/midorian.bartholomeu_drew.json  (represented via stub)
        SimpleNamespace(
            id=504,
            name="Bartholomeu Drew",
            aliases=["bartholomeu", "drew"],
            built_in=False,
            faction="midorian",
            icon=None,
            is_player=False,
            wiki="",
        ),
        # import_data/criminal/terran.kehnor.json  (represented via stub)
        SimpleNamespace(
            id=505,
            name="Kehnor",
            aliases=["kehnor"],
            built_in=False,
            faction="terran",
            icon=None,
            is_player=False,
            wiki="",
        ),
    ]


# ---------------------------------------------------------------------------
# Systems  (models/system.py)
# ---------------------------------------------------------------------------
# Columns: id, name, aliases, coordinates, faction, neighbours, security, wiki
# ---------------------------------------------------------------------------

def get_seed_systems() -> list[SimpleNamespace]:
    """Return 5 systems sourced from import_data/system/*.json."""
    return [
        # import_data/system/terran.aquila.json  (security=2)
        SimpleNamespace(
            id=601,
            name="Aquila",
            aliases=[],
            coordinates=[549, 131],
            faction="terran",
            neighbours=["Wolf-Reiser", "Loma", "Union"],
            security=2,
            wiki="https://galaxyonfire.fandom.com/wiki/Aquila_system",
        ),
        # import_data/system/vossk.vikka.json  (security=1)
        SimpleNamespace(
            id=602,
            name="V'Ikka",
            aliases=["vikka"],
            coordinates=[430, 522],
            faction="vossk",
            neighbours=["Augmenta", "Buntta", "Magnetar", "Oom'Bak", "S'Kolptorr"],
            security=1,
            wiki="https://galaxyonfire.fandom.com/wiki/Category:V'Ikka",
        ),
        # import_data/system/midorian.mido.json  (security=3)
        SimpleNamespace(
            id=603,
            name="Mido",
            aliases=[],
            coordinates=[226, 82],
            faction="midorian",
            neighbours=[],
            security=3,
            wiki="https://galaxyonfire.fandom.com/wiki/Category:Mido",
        ),
        # import_data/system/neutral.alda.json  (security=3)
        SimpleNamespace(
            id=604,
            name="Alda",
            aliases=[],
            coordinates=[461, 790],
            faction="neutral",
            neighbours=[],
            security=3,
            wiki="https://galaxyonfire.fandom.com/wiki/Category:Alda",
        ),
        # import_data/system/nivelian.nesla.json  (represented via stub, security=2)
        SimpleNamespace(
            id=605,
            name="Nesla",
            aliases=[],
            coordinates=[310, 205],
            faction="nivelian",
            neighbours=["Pareah", "Weymire"],
            security=2,
            wiki="https://galaxyonfire.fandom.com/wiki/Category:Nesla",
        ),
    ]
