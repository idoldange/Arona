# ============================================================
# config.py
# ============================================================


# ── Discord ──────────────────────────────────────────────────

ADMINS           = [1048484561966866462]     # User IDs with admin access
IGNORE           = []                        # Ignore messages mentioning these role IDs
INFLIGHT_DELAY   = 10.0                      # seconds to consider a follow-up message as part of the same request

# ── Gemini API ───────────────────────────────────────────────

DEFAULT_MODEL       = "gemini-3.6-flash"
FALLBACK_MODEL      = "gemini-3.5-flash-lite"# 503
RATE_LIMIT_MODEL    = "gemini-3.5-flash-lite"# 429
RATE_LIMIT_MODEL_   = "gemini-3.8-flash"     # 429 on RATE_LIMIT_MODEL
LITE_MODEL          = "gemini-3.5-flash-lite"
LIVE_MODEL          = "gemini-3.1-flash-live-preview"
DEFAULT_TEMPERATURE = 1.0
MAX_RETRIES         = 1                      # rounds
# Hard cap on the number of HTTP requests a single ask_gemini() call may send to the
# Gemini API, counted across every key and every round. Distinct from MAX_RETRIES,
# which only counts rounds — with N keys in the pool one round alone fans out into up
# to N requests. -1 (default) = unlimited, so the normal chat path keeps its full
# key-rotation behaviour; only callers that want a tighter budget pass max_attempts.
MAX_ATTEMPTS        = -1                     # total API requests per ask_gemini call, -1 = unlimited
# Request budget for the background personalization pass (utils/impression.py). Kept
# separate from MAX_ATTEMPTS so the impression update can be given a tighter or looser
# cap than a normal reply without touching the main chat path.
IMPRESSION_MAX_ATTEMPTS = 3                  # API requests allowed per impression update
DEFAULT_TIMEOUT     = 600                    # seconds
ENABLE_FUNCTIONS    = True

# Reference audio for voice call for gemini to mimic the "vibe"
VOICE_CALL_REF_AUD  = "./arona/voice_engine/ref/tts.wav"  
# Transcript of the reference audio for gemini to mimic the "vibe"
VOICE_CALL_REF_TEXT = "せんせい、おつかれさまです！シロコせんぱいたちからのほうこくをまとめましたよ。えへへ、きょうもいちにち、いっしょにがんばりましょうね！じゅんびはいいですか、せんせい？"

# Quotas and limits for free-tier users
FREE_TIER_DAILY_LIMIT   = 30                 # messages/day per user without own key(just use your own free key bro)
GLOBAL_DAILY_SOFT_LIMIT = 0                  # total messages/day across all free-tier users, 0 = disabled

# Retry DEFAULT_MODEL this many times on 503 before falling back to FALLBACK_MODEL
# (instead of switching models on the very first 503)
DEFAULT_MODEL_503_RETRIES = 3                # 503s on DEFAULT_MODEL before switching to FALLBACK_MODEL

# Send decoy requests to break 503 loops
UNSTICK_ON_503        = True                 # enable/disable the whole mechanism
UNSTICK_503_THRESHOLD = 6                    # consecutive 503s (across rounds/keys) before firing

# Give up and report an error after this many consecutive 503s (across rounds/keys),
# regardless of the unstick decoy mechanism. 0 disables the abort.
MAX_CONSECUTIVE_503_ROUNDS = 6
UNSTICK_MODEL       = FALLBACK_MODEL         # decoy uses the main model to improve chances of recovery

MAX_FUNCTION_TURNS  = 100
THINKING_MSG_DELAY  = 20                     # seconds before sending "thinking deeper" message
INCLUDE_THOUGHT     = False

# Route Gemini API requests through a Cloudflare Worker acting as a reverse proxy
USE_CF_WORKER_PROXY = False

SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT",         "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",  "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT",  "threshold": "BLOCK_NONE"},
]


# ── Logging Config ───────────────────────────────────────────

LOG_DIR = ".\\logs"
LOG_PER_FILE = 100 
MAX_LOG_FILES = 10


# ── Cache ────────────────────────────────────────────────────

# Web crawl
CACHE_MAX_SIZE = 100
CACHE_TTL      = 3600          # 1 hour in seconds

# Thought signatures
THOUGHT_SIG_EXPIRE_HOURS = 720 # 30 days

# Gif fetching
GIF_CACHE_TTL_SECONDS = 36000  # 10 hours
GIF_CACHE_MAX_ITEMS = 100

# ── Reverse Image Search ─────────────────────────────────────

SEARCH_URL = "https://serpapi.com/search.json"


# ── Scheduler ────────────────────────────────────────────────

MAX_SCHEULED_TASK_RETRIES = 5  # Max retries for a failed scheduled task before giving up


# ── Chess Engine (local, no Gemini calls) ────────────────────

# Override with env var CHESS_ENGINE_PATH if set. Otherwise auto-detected at
# runtime from games/assets/engine/ or system PATH (any UCI-compatible engine
# works — Stockfish, Lc0, etc).
CHESS_ENGINE_PATH        = None
CHESS_ENGINE_DEFAULT_ELO = 1500
CHESS_ENGINE_MIN_ELO     = 400
CHESS_ENGINE_MAX_ELO     = 3190
CHESS_ENGINE_MOVE_TIME   = 0.5   # seconds to let the user see their move before Arona starts thinking
CHESS_ENGINE_THINK_TIME  = 60.0  # safety ceiling (seconds) per engine search; actual time scales with position complexity


# ── Docker ───────────────────────────────────────────────────

DOCKER_DESKTOP_PATH = "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"


# ── Affection System ─────────────────────────────────────────

# Tick timing
TICK_INTERVAL     = 10.0       # seconds per tick
DB_FLUSH_EVERY_N  = 6          # flush to DB every N ticks (~60s)
TEMP_READ_EVERY_N = 5          # read CPU temp every N ticks

# Idle / sleep thresholds (seconds)
DROWSY_AFTER         = 1000.0  # no longer penalises, kept for reference
SLEEP_AFTER          = 3600.0  # 1h idle → Arona considered sleeping
WAKE_DISPLAY_WINDOW  = 30      # seconds to show "just woke" state
SLEEP_REGEN_PER_TICK = 1.0
HAPPY_CAP            = 40.0    # mood cap while sleeping/regenerating

# "Shocked" overlay state duration
SHOCKED_DURATION_SECONDS = 60.0

# Mood drift & decay
DRIFT_STD      = 0.75
DECAY_PER_TICK = 0.25

# CPU temperature thresholds → mood delta per tick
TEMP_FREEZE = 38.0
TEMP_FREEZE_DELTA = -0.6       # too cold
TEMP_COOL   = 42.0    
TEMP_COOL_DELTA   =  0.0       # slightly cool, neutral
TEMP_SWEET  = 52.0    
TEMP_SWEET_DELTA  =  0.3       # comfortable range
TEMP_WARM   = 68.0    
TEMP_WARM_DELTA   = -0.5       # warm, slightly uncomfortable
TEMP_HOT    = 78.0    
TEMP_HOT_DELTA    = -1.0       # hot
TEMP_OVER   = 87.0    
TEMP_OVER_DELTA   = -3.0       # overheating, at this point we should probably clean up the fans or something

# Mood levels: (min, max, label, description)
MOOD_LEVELS = [
    (-100, -60, "angered",   "Arona is genuinely angry. She's curt, cold, and does not hide her frustration."),
    ( -60, -20, "sad",       "Arona is sad and a little withdrawn. She still helps, but her energy is low."),
    ( -20,  15, "default",   "Arona is in her usual composed, professional state."),
    (  15,  50, "happy",     "Arona is happy — warm, expressive, and enjoying the conversation."),
    (  50,  80, "motivated", "Arona is energized and eager. She's proactive, enthusiastic, and on the ball."),
    (  80, 101, "delighted", "Arona is absolutely delighted. Bubbly, playful, and very engaged."),
]

# Bond ranks: (min_bond, max_bond, rank_name, exp_multiplier)
RANKS = [
    (  0,  10, "0-10"  , 1.00),  # Not in the system yet
    ( 10,  25, "10-25" , 0.75),  # Channel just opened
    ( 25,  45, "25-45" , 0.55),  # Recognized, trusted enough
    ( 45,  60, "45-60" , 0.38),  # The title means something now
    ( 60,  75, "60-75" , 0.25),  # Sharing the mission
    ( 75,  90, "75-90" , 0.14),  # Deep in the inner world
    ( 90, 100, "90-100", 0.07),  # Arona's own, not just a user
    (100, 101, "Max"   , 0.00),  # Cap — Arona's entire world
]

BASE_EXP_MIN = 0.4
BASE_EXP_MAX = 4.4


# ── Database ─────────────────────────────────────────────────

import os
_BASE              = os.path.dirname(os.path.abspath(__file__))
DB_DIR             = os.path.join(_BASE,  "database")
SAVEDINFO_DB_PATH  = os.path.join(DB_DIR, "saved_information.db")  # User's saved info and impressions
VECTOR_DB_PATH     = os.path.join(DB_DIR, "vector_db")             # Persistent storage for ChromaDB to optimize SQLite performance
AFFECTION_DB_PATH  = os.path.join(DB_DIR, "affection.db")          # Mood and bond data
BYOK_DB_PATH       = os.path.join(DB_DIR, "apikeys.db")            # User's own API keys and usage data


# ── Vector Database ──────────────────────────────────────────

EMBEDDING_MODEL = "BAAI/bge-m3"                                    # BGE-M3 model for embeddings
INFERENCE_DEVICE = "cpu"                                           # Device for model inference (CPU/GPU)


# ── Voice Changer ────────────────────────────────────────────

# Generation config
RVC_ENABLED        = True
RVC_MODEL_PATH     = "rvc/models/pretraineds/custom/arona.pth"          
RVC_F0_UP_KEY      = 3         # int
RVC_POST_PROCESS   = False
RVC_PITCH_SHIFT    = 0         # post-processing pitch shift in semitones, applied after F0 modification
RVC_F0_METHOD      = "rmvpe"   # fcpe | rmvpe | crepe
RVC_INDEX_PATH     = "rvc/models/pretraineds/custom/arona.index"        
RVC_INDEX_RATE     = 0.60      # 0-1, how much of the original timbre to keep vs the target
RVC_PROTECT        = 0.43
RVC_CHUNK_SIZE     = 256       # ms, smaller = lower latency but more CPU+GPU usage
RVC_EMBEDDER_MODEL = "contentvec"

# Server settings for RVC Applio
RVC_APPLIO_HOST    = "127.0.0.1"
RVC_APPLIO_PORT    = 6969
RVC_APPLIO_SR      = 48000


# ── Text-to-Speech (TTS) ─────────────────────────────────────

API_URL = "http://127.0.0.1:9880"
GPT_MODEL_PATH = "GPT_weights_v2ProPlus/arona-new-e50.ckpt"#"GPT_weights_v2Pro/arona-e20.ckpt"
SOVITS_MODEL_PATH = "SoVITS_weights_v2ProPlus/arona-new_e25_s300.pth"#"SoVITS_weights_v2Pro/arona_e25_s175.pth" #just testing 

# Emotion refs: tag `[emotion]` trong text TTS -> chon 1 ref ngau nhien trong list cua emotion do.
# `[default]` = TTS_DEFAULT_EMOTION. Ref dat o ttsapi/output/slicer_opt/ (ref_path tinh tu thu muc ttsapi).
TTS_DEFAULT_EMOTION = "neutral"
TTS_REFS = {
    "neutral": [
        {
            "ref_path": "output/slicer_opt/2.wav_0011208960_0011442560.wav",
            "prompt_text": "さらに、今回の降臨大祭では、降臨大祭特設ホームページが開設されます。",
            "prompt_lang": "ja",
        },
    ],
    "happy": [
        {
            "ref_path": "output/slicer_opt/2.wav_0001079040_0001239040.wav",
            "prompt_text": "先生こんにちは。アロナチャンネル特別版のお時間です。",
            "prompt_lang": "ja",
        },
    ],
    "excited": [
        {
            "ref_path": "output/slicer_opt/2.wav_0018751680_0018960320.wav",
            "prompt_text": "あとはノアさん!ノアさんはどんな活躍を見せてくれるんでしょうか?",
            "prompt_lang": "ja",
        },
    ],
    "warm": [
        {
            "ref_path": "output/slicer_opt/2.wav_0012851200_0012992320.wav",
            "prompt_text": "先生はご自分に合った形で手に入れてくださいね",
            "prompt_lang": "ja",
        },
    ],
    "worried": [
        {
            "ref_path": "output/slicer_opt/2.wav_0015770240_0016010560.wav",
            "prompt_text": "SRT特殊学園は現在少し立ち位置が微妙な学校なので心配していたんですが",
            "prompt_lang": "ja",
        },
    ],
    "shy": [
        {
            "ref_path": "output/slicer_opt/2.wav_0021707840_0021864640.wav",
            "prompt_text": "あ、コホン、他には…そうですね…",
            "prompt_lang": "ja",
        },
    ],
    "playful": [
        {
            "ref_path": "output/slicer_opt/2.wav_0022026240_0022163840.wav",
            "prompt_text": "どうでしょう?上手くないですか?えへへ",
            "prompt_lang": "ja",
        },
    ],
    "longing": [
        {
            "ref_path": "output/slicer_opt/2.wav_0019301440_0019460480.wav",
            "prompt_text": "運動会に参加したいなぁかけっこ",
            "prompt_lang": "ja",
        },
    ],
    "surprised": [
        {
            "ref_path": "output/slicer_opt/2.wav_0002554560_0002720000.wav",
            "prompt_text": "芸術の秋ちょっと待ってくださいね",
            "prompt_lang": "ja",
        },
    ],
    "tsundere": [
        {
            "ref_path": "output/slicer_opt/2.wav_0002278400_0002402240.wav",
            "prompt_text": "ではなくて、ほら、先生",
            "prompt_lang": "ja",
        },
    ],
}

# Fallback neu TTS_REFS thieu emotion
TTS_REF = {
    "ref_path": "output/slicer_opt/2.wav_0020727040_0020837440.wav",
    "prompt_text": "どうですか先生? 頑張れそうですか?",
    "prompt_lang": "ja"
}
