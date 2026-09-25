import chess
import chess.engine
import asyncio
import shutil
import base64
import io
import os
import json
import re
import time
from PIL import Image, ImageDraw, ImageFont
from typing import Union, Tuple, Optional
from console import console
from config import (
    CHESS_ENGINE_PATH,
    CHESS_ENGINE_DEFAULT_ELO,
    CHESS_ENGINE_MIN_ELO,
    CHESS_ENGINE_MAX_ELO,
    CHESS_ENGINE_MOVE_TIME,
    CHESS_ENGINE_THINK_TIME,
)

class DiscordChessManager:
    def __init__(self):
        self.COLOR_LIGHT = "#EBECD0"
        self.COLOR_DARK = "#779556"
        self.COLOR_HIGHLIGHT = (247, 247, 105, 180)
        # Interactive-board selection colors (from-square fill / legal-target dot-ring)
        self.COLOR_SELECT = (247, 247, 105, 180)
        self.COLOR_TARGET = (20, 20, 20, 140)
        
        # Map chess pieces to texture file names
        self.pieces_map = {
            'R': 'white-rook', 'N': 'white-knight', 'B': 'white-bishop', 
            'Q': 'white-queen', 'K': 'white-king', 'P': 'white-pawn',
            'r': 'black-rook', 'n': 'black-knight', 'b': 'black-bishop', 
            'q': 'black-queen', 'k': 'black-king', 'p': 'black-pawn'
        }
        
        # Get assets path
        self.assets_path = os.path.join(os.path.dirname(__file__), "assets", "chess-pieces")
        self.piece_images = {}
        self._load_piece_images()

        # Performance caches: resized piece sprites + pre-rendered board squares.
        self._piece_cache = {}
        self._board_base_cache = {}
        self._coord_font = None

        # Board geometry: pure 8x8 grid; tiny coordinate labels drawn inside the
# square corners (rank numbers down the left edge, file letters along the
# bottom edge) so no extra strips/margins are needed.
        self.SQ = 50
        self.GRID = self.SQ * 8          # 400
        self.OFF_X = 0
        self.OFF_Y = 0
        self.OFF_R = 0
        self.OFF_B = 0
        self.TOTAL_W = self.GRID
        self.TOTAL_H = self.GRID
        
        # Games storage - in-memory cache
        self.games = {}
        # Channels where the human player resigned (game is over; engine/human
        # wins by forfeit). True = engine wins, since the human always resigns.
        self.resigned = {}
        
        # File path for persistent storage
        self.games_file = os.path.join(os.path.dirname(__file__), "chess_games.json")
        
        # Load games from file on startup
        self.load_games()

        # ── Local chess engine (no Gemini calls) ──────────────
        # Per-channel engine sessions: {channel_id: {"elo": int}}
        # Presence of a channel_id in this dict means "!arona chess" engine
        # mode is active there — moves get answered by the local engine
        # instead of going through Gemini function calling.
        self.engine_path = self._resolve_engine_path()
        self.engine_sessions = {}
        self.engine_sessions_file = os.path.join(os.path.dirname(__file__), "chess_engine_sessions.json")
        # Serializes engine access per channel so two moves in the same
        # channel can't spawn overlapping engine processes.
        self._engine_locks = {}
        # When the user's last move was applied (per channel). CHESS_ENGINE_MOVE_TIME
        # is counted from this moment as a short pause so the user gets to see
        # their own move on the board before Arona begins searching.
        self._last_user_move_time = {}
        self.load_engine_sessions()

        # PvP (human vs human) sessions: per-channel {white, black} user id pairing.
        # Presence of a channel_id here means that board is a PvP game -- mutually
        # exclusive with engine_sessions for the same channel (starting one clears the other).
        self.pvp_sessions = {}
        self.pvp_sessions_file = os.path.join(os.path.dirname(__file__), "chess_pvp_sessions.json")
        self.load_pvp_sessions()

    def _load_piece_images(self):
        """Load all piece images into memory."""
        for piece_symbol, piece_name in self.pieces_map.items():
            try:
                img_path = os.path.join(self.assets_path, f"{piece_name}.png")
                if os.path.exists(img_path):
                    self.piece_images[piece_symbol] = Image.open(img_path).convert("RGBA")
            except Exception as e:
                console.log(f"Error loading {piece_name}: {e}", "ERROR")

    def load_games(self):
        """Load all games from JSON file into memory."""
        try:
            if os.path.exists(self.games_file):
                with open(self.games_file, 'r') as f:
                    data = json.load(f)
                    for channel_id_str, fen in data.items():
                        try:
                            channel_id = int(channel_id_str)
                            board = chess.Board(fen)
                            self.games[channel_id] = board
                        except Exception as e:
                            console.log(f"Error loading game for channel {channel_id_str}: {e}", "ERROR")
                console.log(f"Loaded {len(self.games)} chess games from file", "INFO")
        except Exception as e:
            print(f"Error loading games from file: {e}")

    def save_games(self):
        """Save all current games to JSON file."""
        try:
            data = {str(channel_id): board.fen() for channel_id, board in self.games.items()}
            os.makedirs(os.path.dirname(self.games_file), exist_ok=True)
            with open(self.games_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving games to file: {e}")

    # ── Local engine: session persistence ──────────────────────

    def _resolve_engine_path(self):
        """
        Find a UCI-compatible chess engine binary. Any engine works
        (Stockfish, Lc0, etc) as long as it speaks the UCI protocol.
        Lookup order:
          1. config.CHESS_ENGINE_PATH / env var CHESS_ENGINE_PATH or STOCKFISH_PATH
          2. games/assets/engine/ (bundled binary, if you drop one there)
          3. system PATH
        """
        candidates = []
        env_path = os.environ.get("CHESS_ENGINE_PATH") or os.environ.get("STOCKFISH_PATH")
        if CHESS_ENGINE_PATH:
            candidates.append(CHESS_ENGINE_PATH)
        if env_path:
            candidates.append(env_path)
        for c in candidates:
            if c and os.path.isfile(c):
                return c

        bundled_dir = os.path.join(os.path.dirname(__file__), "assets", "engine")
        if os.path.isdir(bundled_dir):
            for fname in os.listdir(bundled_dir):
                lower = fname.lower()
                if lower.endswith(".exe") or "stockfish" in lower or "engine" in lower:
                    full = os.path.join(bundled_dir, fname)
                    if os.path.isfile(full) and os.access(full, os.X_OK) or lower.endswith(".exe"):
                        return full

        for name in ("stockfish", "stockfish.exe", "stockfish-windows-x86-64-avx2.exe",
                     "stockfish-windows-x86-64.exe", "lc0", "lc0.exe"):
            found = shutil.which(name)
            if found:
                return found

        return None

    def has_engine(self) -> bool:
        return bool(self.engine_path)

    def load_engine_sessions(self):
        """Load per-channel engine session (elo) state from disk."""
        try:
            if os.path.exists(self.engine_sessions_file):
                with open(self.engine_sessions_file, 'r') as f:
                    data = json.load(f)
                self.engine_sessions = {int(k): v for k, v in data.items()}
                console.log(f"Loaded {len(self.engine_sessions)} chess engine sessions from file", "INFO")
        except Exception as e:
            console.log(f"Error loading chess engine sessions: {e}", "ERROR")

    def save_engine_sessions(self):
        try:
            data = {str(k): v for k, v in self.engine_sessions.items()}
            os.makedirs(os.path.dirname(self.engine_sessions_file), exist_ok=True)
            with open(self.engine_sessions_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            console.log(f"Error saving chess engine sessions: {e}", "ERROR")

    def is_engine_game(self, channel_id) -> bool:
        return channel_id in self.engine_sessions

    def get_engine_elo(self, channel_id) -> Optional[int]:
        session = self.engine_sessions.get(channel_id)
        return session.get("elo") if session else None

    def get_user_color(self, channel_id) -> str:
        """Side the human plays in this channel's engine game ('white' or 'black')."""
        session = self.engine_sessions.get(channel_id)
        if session and session.get("user_color") == "black":
            return "black"
        return "white"

    def get_board_perspective(self, channel_id) -> bool:
        """
        True when the board should be drawn from Black's point of view
        (Black pieces at the bottom).
        - PvP: flip to the side to move, so each player sees their own pieces down.
        - Engine game: flip to the human's color.
        """
        if self.is_pvp_game(channel_id):
            return self._get_game(channel_id).turn == chess.BLACK
        if self.is_engine_game(channel_id):
            return self.get_user_color(channel_id) == "black"
        return False

    def load_pvp_sessions(self):
        try:
            if os.path.exists(self.pvp_sessions_file):
                with open(self.pvp_sessions_file, 'r') as f:
                    data = json.load(f)
                self.pvp_sessions = {int(k): v for k, v in data.items()}
                console.log(f"Loaded {len(self.pvp_sessions)} chess PvP sessions from file", "INFO")
        except Exception as e:
            console.log(f"Error loading chess PvP sessions: {e}", "ERROR")

    def save_pvp_sessions(self):
        try:
            data = {str(k): v for k, v in self.pvp_sessions.items()}
            os.makedirs(os.path.dirname(self.pvp_sessions_file), exist_ok=True)
            with open(self.pvp_sessions_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            console.log(f"Error saving chess PvP sessions: {e}", "ERROR")

    def is_pvp_game(self, channel_id) -> bool:
        return channel_id in self.pvp_sessions

    def get_pvp_players(self, channel_id):
        """(white_user_id, black_user_id) for this channel's PvP game, or (None, None)."""
        session = self.pvp_sessions.get(channel_id, {})
        return session.get("white"), session.get("black")

    def is_pvp_participant(self, channel_id, user_id) -> bool:
        white_id, black_id = self.get_pvp_players(channel_id)
        return user_id in (white_id, black_id)

    def get_pvp_turn_user_id(self, channel_id):
        """Discord user id whose turn it is right now, for a PvP game (or None)."""
        if not self.is_pvp_game(channel_id):
            return None
        board = self._get_game(channel_id)
        white_id, black_id = self.get_pvp_players(channel_id)
        return white_id if board.turn == chess.WHITE else black_id

    def start_pvp_game(self, channel_id, white_id: int, black_id: int) -> Tuple[bool, str]:
        """Start a fresh PvP game between two Discord users. Clears engine mode for this channel."""
        if channel_id in self.engine_sessions:
            del self.engine_sessions[channel_id]
            self.save_engine_sessions()
        self.games[channel_id] = chess.Board()
        self.resigned[channel_id] = False
        self.pvp_sessions[channel_id] = {"white": white_id, "black": black_id}
        self.save_games()
        self.save_pvp_sessions()
        return True, (
            f"PvP game started! <@{white_id}> plays as **White**, <@{black_id}> plays as **Black**.\n"
            f"Use the board below to make your move, or `!arona chess move <move>` when it's your turn."
        )

    def stop_pvp_game(self, channel_id) -> Tuple[bool, str]:
        """Turn off PvP mode for this channel. Board state is kept."""
        if channel_id not in self.pvp_sessions:
            return False, "No PvP game is running in this channel."
        del self.pvp_sessions[channel_id]
        self.save_pvp_sessions()
        return True, "PvP game stopped. The board position is still saved."

    def _clamp_elo(self, elo) -> int:
        try:
            elo = int(elo)
        except (TypeError, ValueError):
            return CHESS_ENGINE_DEFAULT_ELO
        return max(CHESS_ENGINE_MIN_ELO, min(CHESS_ENGINE_MAX_ELO, elo))

    def start_engine_game(self, channel_id, elo=None, user_color: str = "white") -> Tuple[bool, str]:
        """Start a fresh local-engine game in this channel. Player can be White or Black."""
        if not self.has_engine():
            return False, (
                "No local chess engine found on the server. Set `CHESS_ENGINE_PATH` "
                "(or `STOCKFISH_PATH`) in `.env`, or drop a UCI engine binary into "
                "`games/assets/engine/`."
            )
        if channel_id in self.pvp_sessions:
            del self.pvp_sessions[channel_id]
            self.save_pvp_sessions()
        user_color = "black" if str(user_color).lower().startswith("b") else "white"
        engine_color = "Black" if user_color == "white" else "White"
        final_elo = self._clamp_elo(elo) if elo is not None else CHESS_ENGINE_DEFAULT_ELO
        self.games[channel_id] = chess.Board()
        self.resigned[channel_id] = False
        self.engine_sessions[channel_id] = {"elo": final_elo, "user_color": user_color}
        self.save_games()
        self.save_engine_sessions()
        return True, (
            f"Game started! You're **{'White' if user_color == 'white' else 'Black'}**, "
            f"Arona is **{engine_color}** at ~**{final_elo} ELO**.\n"
            f"Use the dropdowns on the board below to move, or type `!arona chess move <move>` (UCI or SAN, e.g. `e2e4` or `Nf3`)."
        )

    def stop_engine_game(self, channel_id) -> Tuple[bool, str]:
        """Turn off local-engine mode for this channel. Board state is kept."""
        if channel_id not in self.engine_sessions:
            return False, "There's no active game in this channel."
        del self.engine_sessions[channel_id]
        self.save_engine_sessions()
        return True, "Game stopped. The board position is still saved."

    def restart_engine_game(self, channel_id, elo=None, user_color: str = None) -> Tuple[bool, str]:
        """Reset the board and (re)start engine mode, keeping the previous elo/color unless a new one is given."""
        if not self.has_engine():
            return False, (
                "No local chess engine found on the server. Set `CHESS_ENGINE_PATH` "
                "(or `STOCKFISH_PATH`) in `.env`, or drop a UCI engine binary into "
                "`games/assets/engine/`."
            )
        if channel_id in self.pvp_sessions:
            del self.pvp_sessions[channel_id]
            self.save_pvp_sessions()
        previous_elo = self.engine_sessions.get(channel_id, {}).get("elo", CHESS_ENGINE_DEFAULT_ELO)
        previous_color = self.get_user_color(channel_id)
        if user_color is not None and str(user_color).lower().startswith("b"):
            previous_color = "black"
        elif user_color is not None:
            previous_color = "white"
        final_elo = self._clamp_elo(elo) if elo is not None else previous_elo
        self.games[channel_id] = chess.Board()
        self.resigned[channel_id] = False
        self.engine_sessions[channel_id] = {"elo": final_elo, "user_color": previous_color}
        self.save_games()
        self.save_engine_sessions()
        return True, (
            f"Chess game restarted! You're **{'White' if previous_color == 'white' else 'Black'}**, "
            f"Arona is **{'Black' if previous_color == 'white' else 'White'}** at ~**{final_elo} ELO**."
        )

    def resign_game(self, channel_id, resigning_side: str = None) -> Tuple[bool, str]:
        """
        End the game immediately by resignation. For engine games (resigning_side
        left None) the human always resigns and Arona wins. For PvP games pass
        resigning_side='white' or 'black' so the winner can be named.
        Unlike stop_engine_game/stop_pvp_game, this properly terminates the game
        so the interactive board stops offering moves.
        """
        if not self.is_engine_game(channel_id) and not self.is_pvp_game(channel_id):
            return False, "There's no active game running in this channel to resign from."
        if self.is_game_over(channel_id):
            return False, "The game is already over."
        self.resigned[channel_id] = resigning_side or True
        self.save_games()
        if self.is_pvp_game(channel_id) and resigning_side in ("white", "black"):
            white_id, black_id = self.get_pvp_players(channel_id)
            winner_id = black_id if resigning_side == "white" else white_id
            return True, f"<@{winner_id}> wins by resignation!"
        return True, "You resigned — Arona wins!"

    def is_game_over(self, channel_id) -> bool:
        """Board-level game over, or the human already resigned."""
        board = self._get_game(channel_id)
        return board.is_game_over() or self.resigned.get(channel_id, False)

    def _get_engine_lock(self, channel_id) -> asyncio.Lock:
        lock = self._engine_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._engine_locks[channel_id] = lock
        return lock

    def play_user_move(self, channel_id, move_str: str) -> Tuple[bool, str, Optional[chess.Move]]:
        """
        Apply a single move from the human player (White) to the board.
        Used by the local-engine flow (!arona chess move ...) — separate from
        move(), which is the Gemini-tool-facing entry point.
        """
        board = self._get_game(channel_id)
        if board.is_game_over():
            return False, "The game is already over. Use `!arona chess restart` to play again.", None

        clean_move = move_str.strip()
        if not clean_move:
            return False, "No move provided.", None

        if self._is_promotion_move(board, clean_move.replace("-", "")) and len(clean_move.replace("-", "")) < 5:
            return False, "Pawn promotion detected. Specify the piece, e.g. `a7a8q` (Q/R/B/N).", None

        try:
            move = self._parse_move(board, clean_move)
        except ValueError:
            return False, f"`{clean_move}`: invalid move format (tried UCI, SAN, and extended notation).", None

        if move not in board.legal_moves:
            reason = self._describe_illegal_move(board, move)
            extra = f" {reason}" if reason else ""
            return False, f"`{clean_move}` is illegal.{extra}", None

        board.push(move)
        self.save_games()
        self._last_user_move_time[channel_id] = time.monotonic()

        status = f"You played: {clean_move}"
        if board.is_checkmate():
            status += " — Checkmate! You win!"
        elif board.is_stalemate():
            status += " — Stalemate (Draw)."
        elif board.is_insufficient_material():
            status += " — Draw (insufficient material)."
        elif board.is_check():
            status += " — Check!"
        return True, status, move

    def _engine_start_wait(self, channel_id) -> float:
        """
        Seconds left before the engine should start searching:
        CHESS_ENGINE_MOVE_TIME counted from when the user's move was applied,
        so the user gets a chance to actually see their own move on the board
        before Arona even begins thinking. The engine is then free to search
        as long as it wants (only a generous safety ceiling applies, config
        CHESS_ENGINE_THINK_TIME).
        """
        stored = self._last_user_move_time.get(channel_id)
        if stored is None:
            return 0.0
        elapsed = max(0.0, time.monotonic() - stored)
        return max(0.0, float(CHESS_ENGINE_MOVE_TIME) - elapsed)

    def _dynamic_think_time(self, board) -> float:
        """
        Pick how long (seconds) the engine should search this position.
        Simple/quiet positions get a short budget so replies feel instant;
        messy, material-rich positions get more time. Always bounded by
        CHESS_ENGINE_THINK_TIME so a tricky move never hangs the channel.
        """
        legal_count = sum(1 for _ in board.legal_moves)
        material = sum(piece.piece_type for piece in board.piece_map().values())
        target = 0.15 + legal_count * 0.01 + material * 0.004
        if board.is_check():
            target += 0.5
        if board.is_game_over():
            target = 0.1
        return max(0.1, min(target, float(CHESS_ENGINE_THINK_TIME)))

    async def engine_play_move(self, channel_id) -> Tuple[bool, str, Optional[chess.Move]]:
        """
        Ask the local engine to compute and push a move for Black in this
        channel's board. Spawns and quits the engine process per call —
        simpler and safer than keeping long-lived engine processes around
        for a Discord bot with many channels.
        """
        if not self.has_engine():
            return False, "No local chess engine available.", None

        board = self._get_game(channel_id)
        if board.is_game_over():
            return False, "Game is already over.", None

        elo = self.get_engine_elo(channel_id) or CHESS_ENGINE_DEFAULT_ELO
        lock = self._get_engine_lock(channel_id)

        # Pause so the user actually sees their move on the board before Arona
        # starts searching (the remaining CHESS_ENGINE_MOVE_TIME window).
        start_wait = self._engine_start_wait(channel_id)
        if start_wait > 0:
            await asyncio.sleep(start_wait)

        async with lock:
            try:
                transport, engine = await chess.engine.popen_uci(self.engine_path)
            except Exception as e:
                console.log(f"Failed to start chess engine at {self.engine_path}: {e}", "ERROR")
                return False, f"Failed to start the chess engine: {e}", None

            try:
                options = {}
                if "UCI_LimitStrength" in engine.options:
                    options["UCI_LimitStrength"] = True
                if "UCI_Elo" in engine.options:
                    opt = engine.options["UCI_Elo"]
                    lo = getattr(opt, "min", None) or CHESS_ENGINE_MIN_ELO
                    hi = getattr(opt, "max", None) or CHESS_ENGINE_MAX_ELO
                    options["UCI_Elo"] = max(lo, min(hi, elo))
                if options:
                    await engine.configure(options)

                # Search for a complexity-scaled amount of time: simple moves play out
                # almost instantly, tricky positions get more thinking room, and
                # CHESS_ENGINE_THINK_TIME is a safety ceiling.
                think = self._dynamic_think_time(board)
                result = await engine.play(board, chess.engine.Limit(time=think))
                move = result.move
                if move is None:
                    return False, "Engine returned no move (game likely over).", None

                board.push(move)
                self.save_games()

                status = f"Arona played: {move.uci()}"
                if board.is_checkmate():
                    status += " — Checkmate! Arona wins."
                elif board.is_stalemate():
                    status += " — Stalemate (Draw)."
                elif board.is_insufficient_material():
                    status += " — Draw (insufficient material)."
                elif board.is_check():
                    status += " — Check!"
                return True, status, move
            except Exception as e:
                console.log(f"Arona move error: {e}", "ERROR")
                return False, f"Arona failed to produce a move: {e}", None
            finally:
                try:
                    await engine.quit()
                except Exception:
                    pass

    def _get_game(self, channel_id):
        """Get or create a chess game for a specific channel."""
        if channel_id not in self.games:
            self.games[channel_id] = chess.Board()
        return self.games[channel_id]

    def get_movable_squares(self, channel_id) -> list:
        """
        Square names (e.g. 'e2') that have at least one legal move for the side to
        move right now. Used to build the "from" picker in the interactive button
        board — capped to 25 since that's Discord's max Select option count (in
        practice a side never has more than 16 pieces, so this never actually bites).
        """
        board = self._get_game(channel_id)
        squares = set()
        for move in board.legal_moves:
            squares.add(chess.square_name(move.from_square))
        return sorted(squares, key=lambda s: (s[1], s[0]))[:25]

    def get_legal_targets(self, channel_id, from_square: str) -> list:
        """
        [(to_square_name, is_promotion), ...] for every legal move starting at
        from_square. Promotion variants (e.g. a7a8q/r/b/n) collapse into one
        (to_square, True) entry — the actual piece is chosen in a follow-up step.
        """
        board = self._get_game(channel_id)
        try:
            from_sq = chess.parse_square(from_square)
        except ValueError:
            return []
        seen = {}
        for move in board.legal_moves:
            if move.from_square != from_sq:
                continue
            to_name = chess.square_name(move.to_square)
            seen[to_name] = seen.get(to_name, False) or (move.promotion is not None)
        return sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0]))

    def reset_game(self, channel_id):
        """Reset the chess board for a specific channel."""
        self.games[channel_id] = chess.Board()
        self.resigned[channel_id] = False
        self.save_games()
        return f"Board in channel <#{channel_id}> has been reset! White moves first."
    
    def _parse_move(self, board, move_str: str):
        """
        Try to parse a move string in multiple formats:
        - UCI:              e2e4, a7a8q
        - SAN:             Nf6, dxe5, O-O, O-O-O, e4, Nbd7
        - Long algebraic:  Ng8f6, Ng8xf6, e2-e4, e2xe4
        - ICCF numeric:    5254 (file+rank, 1-indexed)
        - Descriptive:     N-KB3 (loose, best-effort)
        Returns a chess.Move or raises ValueError.
        """
        s = move_str.strip()
        candidates = []

        def add(c):
            if c and c not in candidates:
                candidates.append(c)

        add(s)

        # Strip capture 'x' -> UCI candidate (d5xe5 -> d5e5, Ng8xf6 -> Ng8f6)
        if 'x' in s.lower():
            add(re.sub(r'[xX]', '', s))

        # Strip dash separator (e2-e4 -> e2e4, e2-e4-q -> e2e4q)
        if '-' in s and not s.upper().startswith('O'):
            add(s.replace('-', ''))

        # Long algebraic with piece prefix: Ng8f6, Bg5f4, Rh1e1 -> g8f6, g5f4, h1e1
        m = re.match(r'^[NBRQK]([a-h][1-8])x?([a-h][1-8])([qrbnQRBN]?)$', s)
        if m:
            add(m.group(1).lower() + m.group(2).lower() + m.group(3).lower())

        # ICCF numeric: 5254 -> e2e4 (file 1-8 maps to a-h, rank as-is)
        m = re.match(r'^([1-8])([1-8])([1-8])([1-8])([1-5]?)$', s)
        if m:
            file_map = {1:'a',2:'b',3:'c',4:'d',5:'e',6:'f',7:'g',8:'h'}
            uci = (file_map[int(m.group(1))] + m.group(2) +
                   file_map[int(m.group(3))] + m.group(4) + m.group(5))
            promo_map = {'1':'q','2':'r','3':'b','4':'n','5':'(invalid)'}
            if m.group(5) in promo_map and promo_map[m.group(5)] != '(invalid)':
                uci = uci[:-1] + promo_map[m.group(5)]
            add(uci)

        # Try all UCI candidates first — pure parsing, legality checked by caller
        for candidate in candidates:
            try:
                return chess.Move.from_uci(candidate.lower())
            except ValueError:
                pass

        # SAN fallback covers: Nf6, dxe5, O-O, Nbd7, e4+, Qxf7#, etc.
        for candidate in candidates:
            try:
                return board.parse_san(candidate)
            except (ValueError, chess.InvalidMoveError, chess.AmbiguousMoveError,
                    chess.IllegalMoveError):
                pass

        raise ValueError(f"Cannot parse move: {move_str}")

    def _is_promotion_move(self, board, uci_move: str) -> bool:
        """Check if a move would result in pawn promotion (reaches last rank)."""
        if len(uci_move) < 4:
            return False
        try:
            from_sq = chess.parse_square(uci_move[:2])
            to_sq = chess.parse_square(uci_move[2:4])
            piece = board.piece_at(from_sq)
            if piece and piece.piece_type == chess.PAWN:
                to_rank = chess.square_rank(to_sq)
                if (piece.color == chess.WHITE and to_rank == 7) or (piece.color == chess.BLACK and to_rank == 0):
                    return True
        except:
            pass
        return False

    def _describe_illegal_move(self, board, move):
        piece = board.piece_at(move.from_square)
        if piece is None:
            return f"No piece on {chess.square_name(move.from_square)}."

        if piece.color != board.turn:
            side = 'White' if board.turn == chess.WHITE else 'Black'
            piece_side = 'White' if piece.color == chess.WHITE else 'Black'
            return f"It's {side}'s turn, but the piece on {chess.square_name(move.from_square)} is {piece_side}."

        if piece.piece_type == chess.PAWN:
            from_file = chess.square_file(move.from_square)
            to_file = chess.square_file(move.to_square)
            from_rank = chess.square_rank(move.from_square)
            to_rank = chess.square_rank(move.to_square)
            file_diff = abs(from_file - to_file)
            rank_diff = to_rank - from_rank if piece.color == chess.WHITE else from_rank - to_rank

            if file_diff == 1 and rank_diff == 0:
                return f"Illegal pawn move from {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}: pawns cannot move sideways."
            if file_diff == 0 and rank_diff <= 0:
                return f"Illegal pawn move from {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}: pawns must move forward."
            if file_diff == 0 and rank_diff > 2:
                return f"Illegal pawn move from {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}: pawns can move one square forward, or two from the starting rank."
            if file_diff == 1 and rank_diff > 1:
                return f"Illegal pawn capture from {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}: pawn captures only one square diagonally."

        return None

    def get_promotion_message(self, channel_id) -> str:
        """Generate a Discord message asking user to choose promotion piece."""
        board = self._get_game(channel_id)
        turn = "White" if board.turn == chess.WHITE else "Black"
        return f"{turn}'s pawn reached the last rank! Please choose a piece to promote to.";

    def move(self, channel_id, uci_move: str):
        """
        Execute one or two moves for a specific channel.
        Accepts single move or two moves separated by space/comma (user move, then bot move).
        Returns (Success, Message, LastMoveObject)
        """
        board = self._get_game(channel_id)
        try:
            moves_input = uci_move.replace("-", "").strip()
            moves_list = [m.strip() for m in moves_input.replace(",", " ").split() if m.strip()]
            
            if not moves_list:
                return False, "No moves provided.", None
            
            last_move = None
            status_messages = []
            
            for i, clean_move in enumerate(moves_list[:2]):
                if self._is_promotion_move(board, clean_move):
                    if len(clean_move) < 5:
                        return False, f"Move {i+1}: Pawn promotion detected. Specify piece: e.g., a7a8q (Q/R/B/N).", None
                
                try:
                    move = self._parse_move(board, clean_move)
                except ValueError:
                    return False, f"Move {i+1} `{clean_move}`: Invalid move format (tried UCI, SAN, and extended notation).", None
                
                if move not in board.legal_moves:
                    reason = self._describe_illegal_move(board, move)
                    extra = f" {reason}" if reason else ""
                    return False, f"Move {i+1} `{clean_move}` is illegal.{extra}", None
                
                board.push(move)
                last_move = move
                
                player = "User" if i == 0 else "Arona"
                status = f"{player}: {clean_move}"
                
                if board.is_checkmate():
                    status += " - Checkmate! Game over.\n Reseting game!"
                    DiscordChessManager().reset_game(channel_id)
                elif board.is_check():
                    status += " - Check!"
                elif board.is_stalemate():
                    status += " - Stalemate (Draw).\n Reseting game!"
                    DiscordChessManager().reset_game(channel_id)
                
                if player == "User" and not self._is_promotion_move(board, clean_move) and not board.is_checkmate() and not board.is_stalemate():
                    status += "\nArona's turn now! Call this function again to make your move."
                status_messages.append(status)
            
            combined_status = " | ".join(status_messages)
            self.save_games()
            return True, combined_status, last_move
        except Exception as e:
            return False, f"Error executing moves: {e}", None

    def _get_coord_font(self, size: int = 10):
        """A small TrueType font for the coordinate labels (falls back to default)."""
        if self._coord_font is not None:
            return self._coord_font
        for path in (
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ):
            try:
                if os.path.exists(path):
                    self._coord_font = ImageFont.truetype(path, size)
                    break
            except Exception:
                continue
        if self._coord_font is None:
            self._coord_font = ImageFont.load_default()
        return self._coord_font

    def _get_board_base(self, black_view: bool = False):
        """Cached pre-rendered empty board: 64 colored squares + mini coordinate labels.

        black_view=True builds the board from Black's point of view (Black pieces
        at the bottom, a-file on the right) with the rank/file labels redrawn
        right-side up — not just a 180-degree rotation, which would leave the
        text upside down.
        """
        key = "base_black" if black_view else "base"
        base = self._board_base_cache.get(key)
        if base is None:
            sq = self.SQ
            base = Image.new("RGB", (self.TOTAL_W, self.TOTAL_H), self.COLOR_LIGHT)
            draw = ImageDraw.Draw(base)

            for r in range(8):
                for c in range(8):
                    color = self.COLOR_LIGHT if (r + c) % 2 == 0 else self.COLOR_DARK
                    x0 = c * sq
                    y0 = r * sq
                    draw.rectangle([x0, y0, x0 + sq, y0 + sq], fill=color)

            font = self._get_coord_font()
            for r in range(8):
                # Left-column rank label: 8..1 top-down in White view, 1..8 in Black view.
                rank_label = str(8 - r) if not black_view else str(r + 1)
                square_color = self.COLOR_LIGHT if r % 2 == 0 else self.COLOR_DARK
                draw.text(
                    (2, r * sq + 1), rank_label, font=font,
                    fill="#1F1F1F" if square_color == self.COLOR_LIGHT else "#EBECD0",
                )
            for c in range(8):
                # Bottom-row file label: a..h left-to-right in White view, h..a in Black view.
                file_label = chr(ord('a') + c) if not black_view else chr(ord('a') + (7 - c))
                # The label sits on the bottom row; compute that square's color:
                # light if (7 - rank + file) % 2 == 0, with rank=0 and file=c in White
                # view, rank=7 and file=7-c in Black view.
                if not black_view:
                    square_color = self.COLOR_LIGHT if (7 + c) % 2 == 0 else self.COLOR_DARK
                else:
                    square_color = self.COLOR_LIGHT if (7 - c) % 2 == 0 else self.COLOR_DARK
                draw.text(
                    (c * sq + 2, 7 * sq + sq - 11), file_label, font=font,
                    fill="#1F1F1F" if square_color == self.COLOR_LIGHT else "#EBECD0",
                )

            self._board_base_cache[key] = base
        return base

    def _get_piece_sprite(self, symbol: str, size: int):
        """Cached resized piece sprite (avoids re-resizing LANCZOS on every render)."""
        key = (symbol, size)
        sprite = self._piece_cache.get(key)
        if sprite is None:
            piece_img = self.piece_images.get(symbol)
            if piece_img is None:
                return None
            sprite = piece_img.resize((size, size), Image.Resampling.LANCZOS)
            self._piece_cache[key] = sprite
        return sprite

    def render_board_image(self, channel_id, highlight_from: str = None, highlight_targets: list = None,
                           black_view: Optional[bool] = None):
        """
        Render the board to a PIL image. Highlights:
        - highlight_from: square tinted as the currently-selected piece.
        - highlight_targets: dot on empty squares, ring around capture squares.
        When a piece is selected the last-move highlight is skipped so the two
        never clash on the board.
        black_view=None auto-flips to the side-to-move (PvP) or the human's
        color (engine game); pass True/False to force a specific perspective.
        """
        board = self._get_game(channel_id)
        if black_view is None:
            black_view = self.get_board_perspective(channel_id)
        sq = self.SQ
        piece_size = int(sq * 0.85)

        img = self._get_board_base(black_view).copy()
        draw = ImageDraw.Draw(img, "RGBA")

        def gx(c):
            return self.OFF_X + c * sq

        def gy(r):
            return self.OFF_Y + r * sq

        def rc(sqi):
            """(col, row) on the image for a board square, honoring perspective."""
            c, r = chess.square_file(sqi), 7 - chess.square_rank(sqi)
            if black_view:
                c, r = 7 - c, 7 - r
            return c, r

        if not highlight_from and len(board.move_stack) > 0:
            last_move = board.peek()
            for sqi in [last_move.from_square, last_move.to_square]:
                c, r = rc(sqi)
                draw.rectangle([gx(c), gy(r), gx(c) + sq, gy(r) + sq], fill=self.COLOR_HIGHLIGHT)

        if highlight_from:
            try:
                sqi = chess.parse_square(highlight_from)
                c, r = rc(sqi)
                draw.rectangle([gx(c), gy(r), gx(c) + sq, gy(r) + sq], fill=self.COLOR_SELECT)
            except ValueError:
                pass

        if highlight_targets:
            for name in highlight_targets:
                try:
                    sqi = chess.parse_square(name)
                except ValueError:
                    continue
                c, r = rc(sqi)
                if board.piece_at(sqi):
                    draw.ellipse(
                        [gx(c) + 4, gy(r) + 4, gx(c) + sq - 4, gy(r) + sq - 4],
                        outline=self.COLOR_TARGET, width=4
                    )
                else:
                    cx, cy = gx(c) + sq // 2, gy(r) + sq // 2
                    radius = sq // 6
                    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=self.COLOR_TARGET)

        for sqi in chess.SQUARES:
            piece = board.piece_at(sqi)
            if piece:
                symbol = piece.symbol()
                c, r = rc(sqi)

                sprite = self._get_piece_sprite(symbol, piece_size)
                if sprite is None:
                    continue

                x_offset = gx(c) + (sq - piece_size) // 2
                y_offset = gy(r) + (sq - piece_size) // 2

                img.paste(sprite, (x_offset, y_offset), sprite)

        return img

    def get_board_image_bytes(self, channel_id, **kwargs) -> bytes:
        """Raw PNG bytes — the fast path for the interactive UI (no base64 round-trip)."""
        buffered = io.BytesIO()
        self.render_board_image(channel_id, **kwargs).save(buffered, format="PNG")
        return buffered.getvalue()

    def get_board_image_base64(self, channel_id, highlight_from: str = None, highlight_targets: list = None,
                           black_view: Optional[bool] = None):
        return base64.b64encode(
            self.get_board_image_bytes(channel_id, highlight_from=highlight_from,
                                       highlight_targets=highlight_targets, black_view=black_view)
        ).decode("utf-8")

    def promote_pawn(self, channel_id, last_move_uci: str, promotion_choice: str) -> Tuple[bool, str]:
        """
        Apply pawn promotion with user's chosen piece.
        promotion_choice: 'q', 'r', 'b', or 'n'
        """
        board = self._get_game(channel_id)
        promotion_map = {'q': chess.QUEEN, 'r': chess.ROOK, 'b': chess.BISHOP, 'n': chess.KNIGHT}
        
        choice_lower = promotion_choice.lower().strip()
        if choice_lower not in promotion_map:
            return False, f"Invalid promotion choice '{promotion_choice}'. Use q/r/b/n."
        
        piece_names = {'q': 'Queen', 'r': 'Rook', 'b': 'Bishop', 'n': 'Knight'}
        promotion_piece = promotion_map[choice_lower]
        
        try:
            uci_move_with_promotion = f"{last_move_uci}{choice_lower}"
            move = board.parse_uci(uci_move_with_promotion)
            
            if move in board.legal_moves:
                board.push(move)
                status = f"Pawn promoted to {piece_names[choice_lower]}!"
                self.save_games()
                return True, status
            return False, "Promotion move is not legal."
        except Exception as e:
            return False, f"Error applying promotion: {e}"
    
    def get_game_status_text(self, channel_id):
        """Get FEN or text description for Gemini to understand board state"""
        board = self._get_game(channel_id)
        return f"Current FEN: {board.fen()}\nIs Check: {board.is_check()}\nTurn: {'White' if board.turn == chess.WHITE else 'Black'}"

    def preload_assets(self):
        """Public method to (re)load piece images into memory. Idempotent."""
        # Re-run the loader to ensure images are in memory at startup
        try:
            self._load_piece_images()
            return True
        except Exception:
            return False
    def get_turn(self, channel_id) -> str:
        board = self._get_game(channel_id)
        if self.is_pvp_game(channel_id):
            white_id, black_id = self.get_pvp_players(channel_id)
            side_id = white_id if board.turn == chess.WHITE else black_id
            color = "White" if board.turn == chess.WHITE else "Black"
            return f"{color} (<@{side_id}>)"
        user_color = self.get_user_color(channel_id)
        if board.turn == (chess.WHITE if user_color == "white" else chess.BLACK):
            return "White (You)" if user_color == "white" else "Black (You)"
        return "Black (Arona AI)" if user_color == "white" else "White (Arona AI)"

chess_manager = DiscordChessManager()
