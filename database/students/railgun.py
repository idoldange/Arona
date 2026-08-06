# database/students/railgun.py
#
# Mock data for Blue Archive × A Certain Scientific Railgun T collab characters.
# Used as fallback when SchaleDB API is unavailable.
#
# Sources: Blue Archive Wiki (bluearchive.wiki) — October 2023 collab event
#   "A Certain Scientific Record of Youth" (とある科学の青春記録)
#
# ⚠ IDs below are APPROXIMATE — verify against live SchaleDB JSON
#   by querying /data/en/students.json and searching for these names.
#   Update the Id values + _load_data checks in schale_db.py accordingly.
#
# Stats shown are Level 90 T1 (base transcendence, max level).

# Misaka Mikoto  |  3★ LIMITED  |  Striker  |  Attacker/Middle
# BulletType: Penetration  |  ArmorType: HeavyArmor
# Terrain: Urban D / Outdoor S (SS w/ gear) / Indoor B
MISAKA_MIKOTO_MOCK_DATA = {
    "Id": 10030,  # TODO: verify against live SchaleDB
    "Name": "Misaka Mikoto",
    "FamilyName": "Misaka",
    "PersonalName": "Mikoto",
    "School": "Tokiwadai",
    "Club": None,
    "StarGrade": 3,
    "IsLimited": 1,
    "Released": {"jp": True, "global": True},
    "Birthday": "May 2",
    "Age": "14",
    "Height": "161cm",
    "Hobbies": "Browsing magazines at the convenience store, collecting Gekota goods",
    "Voice": "Sato Rina",
    "TacticRole": "Attacker",
    "SquadType": "Striker",
    "BulletType": "Penetration",
    "ArmorType": "HeavyArmor",
    "Position": "Middle",
    "WeaponType": "AR",
    # Stats at Lv90 T1
    "AttackPower100": 4154,
    "DefensePower100": 104,
    "MaxHP100": 41414,
    "HealPower100": 4809,
    "AccuracyPoint": 951,
    "DodgePoint": 764,
    "CriticalPoint": 1338,
    "CriticalDamageRate": 2800,
    "StabilityPoint": 1336,
    "Range": 650,
    "OppressionPower": 100,
    "OppressionResist": 136,
    "AttackSpeed": 10000,
    # Terrain adaptations (base, without gear)
    "StreetBattleAdaptation": "D",    # Urban
    "OutdoorBattleAdaptation": "S",   # Outdoor → SS with Gunvolt
    "IndoorBattleAdaptation": "B",    # Indoor
    "Tags": ["Collab", "Tokiwadai", "Railgun", "Limited"],
    "Gear": {
        "Name": "Gunvolt",
        "Tier": 1,
        "StatType": ["AttackPower", "MaxHP"],
        "StatValue": [753, 4494],
        "Desc": (
            "Specialty firearm made by the Engineering Club for Misaka Mikoto. "
            "It comes equipped with 'special' functions for those with electrical abilities. "
            "Outdoor area affinity becomes SS. Increases Penetration Efficiency by 10%."
        ),
    },
    "Skills": [
        {
            "SkillType": "ex",
            "Name": "Tokiwadai Railgun",
            "Desc": "Deal <?1> damage to enemies in a straight line.",
            "Parameters": [["544%", "626%", "789%", "871%", "1034%"]],
        },
        {
            "SkillType": "normal",
            "Name": "It's going to tingle!",
            "Desc": (
                "Every 50 seconds, deal <?1> damage to enemies within a circular area. "
                "Apply <?2> continuous electric shock damage for <?3>."
            ),
            "Parameters": [
                ["120%", "126%", "132%", "156%", "162%", "169%", "193%", "199%", "206%", "228%"],
                ["113%", "119%", "124%", "147%", "152%", "157%"],
                ["20 seconds", "20 seconds", "20 seconds", "30 seconds", "30 seconds",
                 "30 seconds", "40 seconds", "40 seconds", "40 seconds", "40 seconds"],
            ],
        },
        {
            "SkillType": "passive",
            "Name": "Electromaster",
            "Desc": "Increase Accuracy by <?1>. Ignore Shokuhou Misaki's Confusion effect.",
            "Parameters": [
                ["14%", "14.7%", "15.4%", "18.2%", "18.9%", "19.6%", "22.4%", "23.1%", "23.8%", "26.6%"],
            ],
        },
        {
            "SkillType": "sub",
            "Name": "Don't make me repeat myself",
            "Desc": "When Normal skill is activated, increase Attack by <?1> for <?2>.",
            "Parameters": [
                ["10%", "11.2%", "12.4%", "13.4%", "14.7%", "15.4%", "17.6%", "18.3%", "19%", "21.4%"],
                ["20 seconds", "20 seconds", "20 seconds", "25 seconds", "25 seconds",
                 "25 seconds", "30 seconds", "30 seconds", "30 seconds", "40 seconds"],
            ],
        },
    ],
}

# Shokuhou Misaki  |  3★ LIMITED  |  Striker  |  Support/Middle
# BulletType: Explosive  |  ArmorType: HeavyArmor
# Terrain: Urban S (SS w/ gear) / Outdoor C / Indoor C
SHOKUHOU_MISAKI_MOCK_DATA = {
    "Id": 10031,  # TODO: verify against live SchaleDB
    "Name": "Shokuhou Misaki",
    "FamilyName": "Shokuhou",
    "PersonalName": "Misaki",
    "School": "Tokiwadai",
    "Club": None,
    "StarGrade": 3,
    "IsLimited": 1,
    "Released": {"jp": True, "global": True},
    "Birthday": None,   # No canonical birthday in source material
    "Age": "14",
    "Height": None,
    "Hobbies": "Drinking tea, eating organic food",
    "Voice": "Azumi Asakura",
    "TacticRole": "Supporter",
    "SquadType": "Striker",
    "BulletType": "Explosive",
    "ArmorType": "HeavyArmor",
    "Position": "Middle",
    "WeaponType": "HG",
    # Stats at Lv90 T1
    "AttackPower100": 4100,
    "DefensePower100": 100,
    "MaxHP100": 45978,
    "HealPower100": 7132,
    "AccuracyPoint": 109,
    "DodgePoint": 999,
    "CriticalPoint": 368,
    "CriticalDamageRate": 200,
    "StabilityPoint": 908,
    "Range": 550,
    "OppressionPower": 138,
    "OppressionResist": 136,
    "AttackSpeed": 10000,
    # Terrain adaptations (base, without gear)
    "StreetBattleAdaptation": "S",    # Urban → SS with Mental Pointer
    "OutdoorBattleAdaptation": "C",   # Outdoor
    "IndoorBattleAdaptation": "C",    # Indoor
    "Tags": ["Collab", "Tokiwadai", "Railgun", "Limited"],
    "Gear": {
        "Name": "Mental Pointer",
        "Tier": 1,
        "StatType": ["AttackPower", "MaxHP"],
        "StatValue": [683, 4927],
        "Desc": (
            "Specialty firearm made by the Engineering Club for Shokuhou Misaki. "
            "A sleek handgun that is perfect as a fashion statement for high school girls. "
            "Urban area affinity becomes SS. Increases Explosive Efficiency by 10%."
        ),
    },
    "Skills": [
        {
            "SkillType": "ex",
            "Name": "Mental Out",
            "Desc": (
                "Deal <?1> damage to the target and two closest enemies within a circular area. "
                "Confuse them for <?2>."
            ),
            "Parameters": [
                ["772%", "888%", "1120%", "1236%", "1468%"],
                ["5 seconds", "5 seconds", "7 seconds", "7 seconds", "7 seconds"],
            ],
        },
        {
            "SkillType": "normal",
            "Name": "Would you help me out a little?",
            "Desc": "Every 30 seconds, deal <?1> damage to enemies within a circular area.",
            "Parameters": [
                ["318%", "334%", "350%", "413%", "429%", "445%", "509%", "525%", "541%", "604%"],
            ],
        },
        {
            "SkillType": "passive",
            "Name": "Queen of Tokiwadai",
            "Desc": "Increase CC Strength by <?1>.",
            "Parameters": [
                ["14%", "14.7%", "15.4%", "18.2%", "18.9%", "19.6%", "22.4%", "23.1%", "23.8%", "26.6%"],
            ],
        },
        {
            "SkillType": "sub",
            "Name": "I'm the smartest here",
            "Desc": (
                "Every time Shokuhou Misaki's EX skill applies Confusion to an enemy, "
                "increase Defense and Evasion by <?1> for <?2>."
            ),
            "Parameters": [
                ["19.3%", "20.3%", "21.3%", "25.1%", "26.1%", "27.1%", "31%", "32%", "33%", "36.7%"],
                ["20 seconds"] * 10,
            ],
        },
    ],
}

# Saten Ruiko  |  1★ FREE (event)  |  Special  |  Attacker/Back
# BulletType: Penetration  |  ArmorType: SpecialArmor
# Terrain: Urban C / Outdoor A / Indoor B (gear → Indoor A)
SATEN_RUIKO_MOCK_DATA = {
    "Id": 10032,  # TODO: verify against live SchaleDB
    "Name": "Saten Ruiko",
    "FamilyName": "Saten",
    "PersonalName": "Ruiko",
    "School": "Sakugawa",
    "Club": None,
    "StarGrade": 1,
    "IsLimited": 0,   # Free — obtainable via event
    "Released": {"jp": True, "global": True},
    "Birthday": None,   # No canonical birthday in source material
    "Age": "12",
    "Height": "160cm",
    "Hobbies": "Online mahjong, trying out sweets",
    "Voice": "Itō Kanae",
    "TacticRole": "Attacker",
    "SquadType": "Special",
    "BulletType": "Penetration",
    "ArmorType": "SpecialArmor",
    "Position": "Back",
    "WeaponType": "SMG",
    # Stats at Lv90 T1
    "AttackPower100": 3829,
    "DefensePower100": 2263,
    "MaxHP100": 38015,
    "HealPower100": 6490,
    "AccuracyPoint": 659,
    "DodgePoint": 1182,
    "CriticalPoint": 253,
    "CriticalDamageRate": 2200,
    "StabilityPoint": 1582,
    "Range": 1500,
    "OppressionPower": 138,
    "OppressionResist": 100,
    "AttackSpeed": 10000,
    # Terrain adaptations (base, without gear)
    "StreetBattleAdaptation": "C",    # Urban
    "OutdoorBattleAdaptation": "A",   # Outdoor
    "IndoorBattleAdaptation": "B",    # Indoor → A with Bullet Swinger
    "Tags": ["Collab", "Sakugawa", "Railgun"],
    "Gear": {
        "Name": "Bullet Swinger",
        "Tier": 1,
        "StatType": ["AttackPower", "MaxHP", "HealPower"],
        "StatValue": [597, 3896, 1117],
        "Desc": (
            "Specialty firearm made by the Engineering Club for Saten Ruiko. "
            "It's strong enough not to break even if you take a swing at something with it. "
            "Indoor area affinity becomes A. Increases maximum Cost by 0.5."
        ),
    },
    "Skills": [
        {
            "SkillType": "ex",
            "Name": "Full Swing",
            "Desc": "Deal <?1> damage to enemies in a circular area.",
            "Parameters": [
                ["446%", "513%", "646%", "713%", "847%"],
            ],
        },
        {
            "SkillType": "normal",
            "Name": "Secret Mackerel Curry",
            "Desc": (
                "Every 45 seconds, increase Attack of one ally by <?1> for <?2>. "
                "Every time Ruiko uses a Normal skill, the number of targets increases by 1 "
                "(up to a maximum of 3)."
            ),
            "Parameters": [
                ["10.9%", "11.4%", "12%", "14.1%", "14.7%", "15.2%", "17.3%", "18%", "18.6%", "20.6%"],
                ["20 seconds"] * 10,
            ],
        },
        {
            "SkillType": "passive",
            "Name": "Going all out!",
            "Desc": "Increase Attack by <?1>.",
            "Parameters": [
                ["14%", "14.7%", "15.4%", "18.2%", "18.9%", "19.6%", "22.4%", "23.1%", "23.8%", "26.6%"],
            ],
        },
        {
            "SkillType": "sub",
            "Name": "Me too!",
            "Desc": "Increase Attack of all allies by <?1>.",
            "Parameters": [
                ["9.1%", "9.5%", "10%", "11.8%", "12.3%", "12.7%", "14.5%", "15%", "15.5%", "17.3%"],
            ],
        },
    ],
}