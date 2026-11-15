# Arona Project Index for Commit Messages

Use this index as brief context when describing a diff. The diff is the source of truth: mention only changes it supports, and use this map to identify a file's role and related areas. This is not a general development guide.

## Core runtime

- `main.py` — Discord events, commands, conversation flow, and Gemini tool dispatch.
- `config.py` — Runtime settings, model options, IDs, and data paths.
- `utils/tool_schemas.py` — Gemini tool declarations; `utils/tool_groups.py` — lazy-loaded tool groups. Handlers are dispatched from `main.py`.
- `server.js` — Express/Socket.IO web panel and bot process control; `public/` — its browser UI.
- `cf_worker.js` — Optional Cloudflare proxy for Gemini requests.

## Feature areas

- `utils/` — Shared services and integrations: memory/history (`memory.py`, `msg_bank.py`, `vector_database.py`, `channel_memory.py`, `guild_memory.py`), Discord UI (`discord_ui*.py`), scheduling/tasks (`scheduler.py`, `todo.py`), and integrations (`github.py`, `youtube.py`, `wiki.py`, `schale_db.py`).
- `affection/` — Mood and per-user relationship state: `manager.py`, `mood.py`, `bond.py`, `special.py`.
- `arona/` — Persona and audio: `prompt.py`, `tts/`, `voicechanger.py`, `voice_engine/`; `activity/` — Arona activity integration.
- `games/chess.py` — Discord chess feature using assets under `games/assets/`.
- `console/` — Runtime logging and operator commands.
- `activity/` — Activity web clients, overlays, assets, and supporting scripts; `activity/signal` is a submodule.
- `docker/` — Isolated execution container and network controls.

## Data, tooling, and supporting files

- `database/` — Runtime state, skills, and student data; distinguish changes to data from application logic.
- `attachment.py` — Discord attachment preprocessing; `clean_context.py`, `debug.py` — maintenance/debug helpers.
- `bond_editor.py` — Bond-data editor; `migrate_msgbank.py` — message/vector-data migration.
- `generate_msg.py`, `notify_discord.py`, `sync.bat` — Commit-message generation and sync/notification workflow.
- `requirements.txt`, `package.json` / `package-lock.json` — Python and Node dependencies.
- `start.bat`, `ServerUI.java` — Windows desktop launcher/controller.
- `fonts/`, `soundfonts/`, `fluidsynth/` — Audio and font resources.
