# Arona

Arona is an AI Discord bot built in Python, featuring intelligent conversation, voice interaction, TTS, memory management, an affection/bond system, integrations with GitHub, YouTube, weather, chess, and a remote web control panel.

> This project is still in an experimental/development stage. A portion of the codebase was generated automatically and may not be fully optimized, so please use it with caution. (i'm just lazy)

## Key Features

- AI Discord bot with text and voice interaction
- TTS and voice changer support (including experimental integration with Applio / GPT-SoVITS / RVC)
- Affection, mood, and bond system for the Arona character
- Memory management per channel / guild / user
- Integrations with Gemini, GitHub, YouTube, weather, and reverse image search
- Remote web control panel to start/stop/restart the bot, view logs, and monitor status
- Docker support for isolated execution environments

## System Requirements

- Python 3.10+ (3.11 recommended)
- Node.js
- Java Runtime/JDK (required for the Java UI launched by start.bat)
- Docker (optional for some features, but recommended for sandbox/runner setups)
- ffmpeg available on PATH

## Installation

1. Clone the repository:

```bash
git clone https://github.com/idoldange/Arona.git
cd Arona
```

2. Create a virtual environment (recommended):

```bash
python -m venv .venv
.venv\Scripts\activate
```

3. Install Python and Node dependencies:

```bash
pip install -r requirements.txt
npm install
```

4. Copy the environment file:

```bash
copy .env.example .env
```

Then edit the .env file with real values:

- DISCORD_TOKEN
- GEMINI_API_KEY
- SERP_API_KEY
- KLIPY_API_KEY (optional)
- GIPHY_API_KEY (optional)
- GITHUB_TOKEN
- GITHUB_ISSUES_TOKEN (optional, for issue-related features)
- WEATHER_API_KEY (optional)

5. Configure the bot:

- Open config.py and adjust:
  - ADMINS
  - IGNORED_CHANNELS
  - model / timeout / retry settings
  - voice model paths if using the voice changer

6. Install supporting services if you plan to use voice/TTS:

- Applio
- GPT-SoVITS
- or the corresponding RVC models

## Running the Project

### Run the bot

```bash
python main.py
```

### Run the web control panel

```bash
node server.js
```

Then open your browser at:

```text
http://localhost:3000
```

### Run via Windows batch script

```bat
start.bat
```

This script launches the Java UI and provides quick access to the control tools.

## Main Directory Structure

- main.py: main bot entry point
- config.py: bot configuration
- server.js: remote web control panel
- public/: web interface assets
- console/: console and command handler
- utils/: utility modules (memory, scheduler, GitHub, todo, vector database, etc.)
- arona/: prompt, voice, and TTS modules
- affection/: affection, bond, and mood systems
- database/: local persistent data
- docker/: Docker configuration for isolated execution

## Important Notes

- The .env file contains sensitive information and should not be committed to GitHub.
- The pass.txt file is used for logging into the web panel.
- If you use Docker, check the docker/ directory to understand how the worker/container is launched.
- Some modules, such as the voice changer or TTS stack, may require manual configuration.

## Troubleshooting

- If the bot does not start, check:
  - whether the Discord token is valid
  - whether GEMINI_API_KEY is in the correct format
  - whether ffmpeg is available on PATH
  - whether all dependencies were installed correctly
- If the web panel does not open, check:
  - whether Node.js is installed correctly
  - whether pass.txt exists
  - whether port 3000 is already in use

## Additional References

- Repository: https://github.com/idoldange/Arona
- Project page: https://github.com/idoldange/arona-ai