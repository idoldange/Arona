---
name: mc-datapack
description: "Guide for creating, editing, and delivering Minecraft datapacks using the run_code sandbox. Covers all datapack component types, pack_format table, workspace management, zipping, and delivery."
---

# Minecraft Datapack Development Guide

## Overview

Minecraft datapacks let you customize the game entirely in vanilla — no mods required. This skill covers how to build, edit, and deliver datapacks using Arona's sandbox tools (`run_code`). All file generation happens inside the Docker workspace; finished packs are zipped and returned to the user.

---

## Sandbox Environment

Arona executes all file/code operations inside a Docker container (`arona_worker`). Understand these rules before writing any tool calls:

| Concept | Detail |
|---|---|
| **Working dir (container)** | `./` |
| **Output dir (container)** | `./outputs/` |
| **Persistent workspace** | `temp=false` |
| **Temp workspace** | `temp=true` — default |
| **`temp=false`** | Keep workspace after task; always use for datapacks |
| **`send_output=true`** | Automatically uploads files in `outputs/` to Discord |
| **Timeout** | Default 120 s; increase for heavy tasks (`timeout=300`) |
| **Rate limit** | 10 executions per user per 60 s |

## Pack Format Reference

Use the correct `pack_format` in `pack.mcmeta`. Wrong value = pack won't load.

| Minecraft Version | pack_format |
|---|---|
| 1.13 – 1.14.4 | 4 |
| 1.15 – 1.16.1 | 5 |
| 1.16.2 – 1.16.5 | 6 |
| 1.17 – 1.17.1 | 7 |
| 1.18 – 1.18.1 | 8 |
| 1.18.2 | 9 |
| 1.19 – 1.19.3 | 10 |
| 1.19.4 | 12 |
| 1.20 – 1.20.1 | 15 |
| 1.20.2 | 18 |
| 1.20.3 – 1.20.4 | 26 |
| 1.20.5 – 1.20.6 | 41 |
| 1.21 – 1.21.1 | 48 |
| 1.21.2 – 1.21.3 | 57 |
| 1.21.4 | 61 |

`pack.mcmeta` also supports `supported_formats` (range or list) for multi-version packs (1.20.2+):
```json
{
  "pack": {
    "pack_format": 61,
    "supported_formats": [48, 61],
    "description": "Works on 1.21 – 1.21.4"
  }
}
```

---

## Canonical Folder Structure

```
<pack_name>/
├── pack.mcmeta                         ← required
├── pack.png                            ← optional (64×64 icon)
└── data/
    ├── minecraft/                      ← override vanilla content
    │   ├── tags/
    │   │   ├── blocks/
    │   │   ├── entity_types/
    │   │   ├── fluids/
    │   │   ├── functions/
    │   │   │   ├── load.json           ← runs on /reload
    │   │   │   └── tick.json           ← runs every tick
    │   │   └── items/
    │   └── loot_tables/
    │       └── blocks/
    └── <namespace>/                    ← your custom namespace (e.g. arona)
        ├── advancements/
        ├── damage_types/               ← 1.19.4+
        ├── dimension/
        ├── dimension_type/
        ├── functions/
        ├── item_modifiers/             ← 1.17+
        ├── loot_tables/
        │   ├── blocks/
        │   ├── chests/
        │   └── entities/
        ├── predicates/
        ├── recipes/
        ├── structures/
        ├── tags/
        │   ├── blocks/
        │   ├── entity_types/
        │   ├── functions/
        │   └── items/
        └── worldgen/
            ├── biome/
            ├── configured_carver/
            ├── configured_feature/
            ├── density_function/
            ├── flat_level_generator_preset/
            ├── noise/
            ├── noise_settings/
            ├── placed_feature/
            ├── processor_list/
            ├── structure/
            ├── structure_set/
            ├── template_pool/
            └── world_preset/
```

Only create the subdirectories you actually use. Empty folders are ignored.

---

## Component Reference

### Functions (`.mcfunction`)

Plain-text files; one command per line. Comments start with `#`. You can only use comments in a standalone line, not at the end of a command.

```mcfunction
# data/<namespace>/functions/example.mcfunction
say Hello from Arona Datapack!
give @p diamond_sword{display:{Name:'{"text":"Arona Sword","italic":false}'}} 1
```

**Call a function:** `/function <namespace>:<path>` (omit `.mcfunction`)

**Scheduled functions:**
```mcfunction
# run once after 20 ticks
schedule function arona:my_task 20t
# run every 40 ticks indefinitely
schedule function arona:tick_loop 40t append
```

**Tick / Load hooks** — register in `data/minecraft/tags/functions/`:

tick.json — runs every tick:
```json
{
  "values": ["arona:on_tick"]
}
```

load.json — runs on `/reload` and world load:
```json
{
  "values": ["arona:on_load"]
}
```

---

### Advancements (`.json`)

Path: `data/<namespace>/advancements/<name>.json`

```json
{
  "display": {
    "title": {"text": "Hello World"},
    "description": {"text": "Join the server"},
    "icon": {"item": "minecraft:grass_block"},
    "frame": "task",
    "show_toast": true,
    "announce_to_chat": true,
    "hidden": false
  },
  "criteria": {
    "joined": {
      "trigger": "minecraft:tick"
    }
  },
  "rewards": {
    "function": "arona:reward_join"
  }
}
```

Common `trigger` values: `minecraft:tick`, `minecraft:player_killed_entity`,
`minecraft:inventory_changed`, `minecraft:location`, `minecraft:used_item`,
`minecraft:recipe_unlocked`.

---

### Loot Tables (`.json`)

Path: `data/<namespace>/loot_tables/<context>/<name>.json`

```json
{
  "type": "minecraft:chest",
  "pools": [
    {
      "rolls": {"min": 3, "max": 6},
      "entries": [
        {
          "type": "minecraft:item",
          "name": "minecraft:diamond",
          "weight": 5,
          "functions": [
            {"function": "minecraft:set_count", "count": {"min": 1, "max": 3}}
          ]
        },
        {
          "type": "minecraft:item",
          "name": "minecraft:iron_ingot",
          "weight": 20,
          "functions": [
            {"function": "minecraft:set_count", "count": {"min": 2, "max": 8}}
          ]
        }
      ]
    }
  ]
}
```

Override vanilla block drops by placing the table at `data/minecraft/loot_tables/blocks/<block_name>.json`.

---

### Recipes (`.json`)

Path: `data/<namespace>/recipes/<name>.json`

**Shaped crafting:**
```json
{
  "type": "minecraft:crafting_shaped",
  "pattern": ["DDD", "DSD", "DDD"],
  "key": {
    "D": {"item": "minecraft:diamond"},
    "S": {"item": "minecraft:stick"}
  },
  "result": {"item": "minecraft:diamond_sword", "count": 1}
}
```

**Shapeless crafting:**
```json
{
  "type": "minecraft:crafting_shapeless",
  "ingredients": [
    {"item": "minecraft:redstone"},
    {"item": "minecraft:glowstone_dust"}
  ],
  "result": {"item": "minecraft:blaze_powder"}
}
```

**Smelting / Smoking / Blast / Campfire:**
```json
{
  "type": "minecraft:smelting",
  "ingredient": {"item": "minecraft:iron_ore"},
  "result": {"item": "minecraft:iron_ingot"},
  "experience": 0.7,
  "cookingtime": 200
}
```

Recipe types: `crafting_shaped`, `crafting_shapeless`, `smelting`, `blasting`,
`smoking`, `campfire_cooking`, `stonecutting`, `smithing_transform` (1.20+),
`smithing_trim` (1.20+).

---

### Tags (`.json`)

Path: `data/<namespace>/tags/<type>/<name>.json`

```json
{
  "replace": false,
  "values": [
    "minecraft:diamond_sword",
    "minecraft:iron_sword",
    "#minecraft:swords"
  ]
}
```

`"replace": true` overwrites the vanilla tag instead of merging.
Prefix `#` to reference another tag.

Tag types: `blocks`, `entity_types`, `fluids`, `functions`, `game_events` (1.17+), `items`.

---

### Predicates (`.json`)

Path: `data/<namespace>/predicates/<name>.json`

Used in loot tables, advancements, and `execute if predicate` commands.

```json
[
  {
    "condition": "minecraft:entity_properties",
    "entity": "this",
    "predicate": {
      "equipment": {
        "mainhand": {"items": ["minecraft:diamond_sword"]}
      }
    }
  }
]
```

---

### Item Modifiers (`.json`) — 1.17+

Path: `data/<namespace>/item_modifiers/<name>.json`

Standalone loot functions usable with `item modify` command.

```json
[
  {"function": "minecraft:set_name", "name": {"text": "Arona's Gift", "italic": false}},
  {"function": "minecraft:set_lore", "lore": [{"text": "Granted by Arona", "color": "aqua"}]}
]
```

---

### Structures (`.nbt`)

Path: `data/<namespace>/structures/<name>.nbt`

Binary NBT files. Generate them in-game with Structure Blocks (`/give @p structure_block`),
or create programmatically with libraries like `nbtlib`:

```python
# Python inside run_code
import subprocess
subprocess.run(["pip", "install", "nbtlib", "-q"])
import nbtlib, os

nbt = nbtlib.Compound({
    "DataVersion": nbtlib.Int(3953),
    "size": nbtlib.List[nbtlib.Int]([nbtlib.Int(1), nbtlib.Int(1), nbtlib.Int(1)]),
    "palette": nbtlib.List[nbtlib.Compound]([
        nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})
    ]),
    "blocks": nbtlib.List[nbtlib.Compound]([
        nbtlib.Compound({
            "pos": nbtlib.List[nbtlib.Int]([nbtlib.Int(0), nbtlib.Int(0), nbtlib.Int(0)]),
            "state": nbtlib.Int(0)
        })
    ]),
    "entities": nbtlib.List()
})
file = nbtlib.File(nbt)
os.makedirs(f"./<datapack-name>/data/<namespace>/structures", exist_ok=True)
file.save(f"./<datapack-name>/data/<namespace>/structures/my_structure.nbt")
```

---

### World Generation (1.18+ — `.json`)

Path: `data/<namespace>/worldgen/<type>/<name>.json`

Biome override example (`data/minecraft/worldgen/biome/plains.json`):
```json
{
  "precipitation": "rain",
  "temperature": 0.8,
  "downfall": 0.4,
  "effects": {
    "sky_color": 7907327,
    "fog_color": 12638463,
    "water_color": 4159204,
    "water_fog_color": 329011,
    "grass_color": 9470285,
    "foliage_color": 10387657,
    "mood_sound": {
      "sound": "minecraft:ambient.cave",
      "tick_delay": 6000,
      "block_search_extent": 8,
      "offset": 2.0
    }
  },
  "carvers": {},
  "features": [],
  "spawners": {},
  "spawn_costs": {},
  "creature_spawn_probability": 0.1
}
```

---

## Step-by-Step: Creating a New Datapack

### Step 1 — Generate file structure with Python

```python
# run_code(action="run_code", code=<this>, channel_id=<id>, temp=False)
import os, json

NAMESPACE = "arona"
PACK_NAME = "my_datapack"
PACK_FORMAT = 61          # 1.21.4
OUTPUT_DIR = "./outputs"
BASE = f"{OUTPUT_DIR}/../{PACK_NAME}"

# Helper
def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if isinstance(content, str) else json.dumps(content, indent=2))

# pack.mcmeta
write(f"{BASE}/pack.mcmeta", {
    "pack": {"pack_format": PACK_FORMAT, "description": "My Arona Datapack"}
})

# load.json — register on_load function
write(f"{BASE}/data/minecraft/tags/functions/load.json",
    {"values": [f"{NAMESPACE}:on_load"]})

# tick.json — register on_tick function
write(f"{BASE}/data/minecraft/tags/functions/tick.json",
    {"values": [f"{NAMESPACE}:on_tick"]})

# on_load.mcfunction
write(f"{BASE}/data/{NAMESPACE}/functions/on_load.mcfunction",
    f'tellraw @a {{"text":"[{NAMESPACE}] Datapack loaded!","color":"green"}}\n')

# on_tick.mcfunction (empty — fill as needed)
write(f"{BASE}/data/{NAMESPACE}/functions/on_tick.mcfunction", "# tick logic here\n")

print("Structure created.")
```

### Step 2 — Add components (advancements, recipes, loot tables…)

Write additional `write(...)` calls in the same or subsequent `run_code` calls in the same `channel_id` workspace.

### Step 3 — Verify structure

```shell
# run_shell
find /app/workdir/<workspace_key>/my_datapack -type f | sort
```

### Step 4 — Zip the pack

```shell
# run_shell — produces outputs/my_datapack.zip
cd /app/workdir/<workspace_key> && \
  zip -r outputs/my_datapack.zip my_datapack/ && \
  echo "Done: $(du -sh outputs/my_datapack.zip)"
```

### Step 5 — Deliver

Set `send_output=True` on the final `run_code`/`run_shell` call. The bot automatically
uploads every file in `outputs/` to Discord.

---

## Step-by-Step: Editing an Existing Datapack

### Option A — User uploads the zip as attachment

The Discord attachment is automatically saved to the workspace at:
`/app/workdir/<workspace_key>/<original_filename>`

```shell
# Unzip, then modify
cd /app/workdir/<workspace_key> && \
  unzip -o <pack_name>.zip -d <pack_name>_edit/
```

Then use `run_code` to read/modify files, and re-zip.

### Option B — Download from URL

```shell
curl -L "https://example.com/pack.zip" -o /app/workdir/<workspace_key>/pack.zip && \
  unzip -o /app/workdir/<workspace_key>/pack.zip \
    -d /app/workdir/<workspace_key>/pack_edit/
```

### Re-zip after editing

```shell
cd /app/workdir/<workspace_key> && \
  zip -r outputs/<pack_name>_v2.zip <pack_name>_edit/ && \
  echo "$(du -sh outputs/<pack_name>_v2.zip)"
```

---

## Workspace Management

```
# Persist a finished zip to long-term storage (move_file tool)
move_file(channel_id=<id>, filename="my_datapack.zip", direction="persist")

# Stage a previously persisted pack back into workspace for editing
move_file(channel_id=<id>, filename="my_datapack.zip", direction="stage")

# Cleanup a workspace when done
cleanup_files(file_ids=[<workspace_key>])
```

Use `channel_id` consistently for the entire lifecycle of a datapack so all
steps share the same `/app/workdir/<workspace_key>/` directory.

---

## Common Patterns & Recipes

### Give a custom item on join

```mcfunction
# on_load.mcfunction — set up scoreboard
scoreboard objectives add joined_once minecraft.custom:play_one_minute

# on_tick.mcfunction
execute as @a[scores={joined_once=1..1}] run function arona:give_starter
scoreboard players add @a[scores={joined_once=0}] joined_once 1
```

```mcfunction
# give_starter.mcfunction
give @s diamond_sword{display:{Name:'{"text":"Starter Blade","italic":false}'},Enchantments:[{id:sharpness,lvl:3}]} 1
scoreboard players set @s joined_once 2
```

### Detect item in hand

```mcfunction
execute as @a[nbt={SelectedItem:{id:"minecraft:blaze_rod"}}] run function arona:blaze_action
```

### Custom death message via advancement

```json
{
  "criteria": {"killed": {"trigger": "minecraft:entity_hurt_player",
    "conditions": {"source": {"direct_entity": {"type": "minecraft:creeper"}}}}},
  "display": {"title": {"text": "Boom!"}, "description": {"text": "Exploded by a creeper"},
    "icon": {"item": "minecraft:tnt"}, "frame": "challenge",
    "show_toast": true, "announce_to_chat": true},
  "criteria_merge_strategy": "and"
}
```

### Custom crafting recipe (shapeless)

```json
{
  "type": "minecraft:crafting_shapeless",
  "group": "arona_custom",
  "ingredients": [{"item":"minecraft:nether_star"},{"item":"minecraft:ender_pearl"}],
  "result": {"item":"minecraft:beacon","count":1}
}
```

---

## Debugging Tips

- **Pack not loading:** Check `pack_format` in `pack.mcmeta` matches the target MC version.
- **Function not running:** Verify path matches exactly — `arona:my_func` → `data/arona/functions/my_func.mcfunction`.
- **Commands silently fail:** Add `tellraw @a` or `say` checkpoints, or run with `/function` manually.
- **JSON syntax error:** Validate JSON before writing (Python `json.loads()` in `run_code`).
- **Tags not merging:** Ensure `"replace": false` (or omit it) when extending vanilla tags.
- **Scoreboard issues:** Always create objectives in `on_load.mcfunction`, not `on_tick`.
- **NBT structure not found:** DataVersion must match MC version — wrong value causes silent rejection.

### Validate all JSON files before zipping

```python
# run_code — validates every .json in the pack
import json, os, glob

OUTPUT_DIR = "./outputs"
base = f"{OUTPUT_DIR}/../my_datapack"
errors = []
for f in glob.glob(f"{base}/**/*.json", recursive=True):
    try:
        with open(f) as fh:
            json.load(fh)
    except json.JSONDecodeError as e:
        errors.append(f"{f}: {e}")

if errors:
    for e in errors:
        print("ERROR:", e)
else:
    print(f"All JSON files valid ({len(list(glob.glob(f'{base}/**/*.json', recursive=True)))} files)")
```

---

## Size & Compatibility Notes

- Keep the zip under **25 MB** for easy Discord upload (default file limit).
- If including audio/sounds, re-encode with ffmpeg: `ffmpeg -i in.ogg -ar 22050 -ac 1 out.ogg` to reduce size dramatically.
- Structures (`.nbt`) can be large — compress with `gzip` if needed or split into pieces.
- For multi-version support use `supported_formats` range in `pack.mcmeta` (1.20.2+).
- Avoid spaces in namespace names — use `snake_case` only.
- Namespace must be globally unique; always use a project-specific name (e.g. `arona`, `myproject`), never `test` or `example`.