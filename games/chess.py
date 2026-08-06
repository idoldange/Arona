import chess
import base64
import io
import os
import json
import re
from PIL import Image, ImageDraw, ImageFont
from typing import Union, Tuple, Optional
from console import console

class DiscordChessManager:
    def __init__(self):
        self.COLOR_LIGHT = "#EBECD0"
        self.COLOR_DARK = "#779556"
        self.COLOR_HIGHLIGHT = (247, 247, 105, 180)
        
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
        
        # Games storage - in-memory cache
        self.games = {}
        
        # File path for persistent storage
        self.games_file = os.path.join(os.path.dirname(__file__), "chess_games.json")
        
        # Load games from file on startup
        self.load_games()
    
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

    def _get_game(self, channel_id):
        """Get or create a chess game for a specific channel."""
        if channel_id not in self.games:
            self.games[channel_id] = chess.Board()
        return self.games[channel_id]

    def reset_game(self, channel_id):
        """Reset the chess board for a specific channel."""
        self.games[channel_id] = chess.Board()
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

    def get_board_image_base64(self, channel_id):
        board = self._get_game(channel_id)
        img_size = 400
        sq_size = img_size // 8
        piece_size = int(sq_size * 0.85)
        
        img = Image.new("RGB", (img_size, img_size), self.COLOR_LIGHT)
        draw = ImageDraw.Draw(img, "RGBA")

        for r in range(8):
            for c in range(8):
                color = self.COLOR_LIGHT if (r + c) % 2 == 0 else self.COLOR_DARK
                draw.rectangle([c * sq_size, r * sq_size, (c + 1) * sq_size, (r + 1) * sq_size], fill=color)

        if len(board.move_stack) > 0:
            last_move = board.peek()
            for sq in [last_move.from_square, last_move.to_square]:
                c, r = chess.square_file(sq), 7 - chess.square_rank(sq)
                draw.rectangle([c * sq_size, r * sq_size, (c + 1) * sq_size, (r + 1) * sq_size], fill=self.COLOR_HIGHLIGHT)

        for sq in chess.SQUARES:
            piece = board.piece_at(sq)
            if piece:
                symbol = piece.symbol()
                c, r = chess.square_file(sq), 7 - chess.square_rank(sq)
                
                if symbol in self.piece_images:
                    piece_img = self.piece_images[symbol]
                    resized = piece_img.resize((piece_size, piece_size), Image.Resampling.LANCZOS)
                    
                    x_offset = c * sq_size + (sq_size - piece_size) // 2
                    y_offset = r * sq_size + (sq_size - piece_size) // 2
                    
                    img.paste(resized, (x_offset, y_offset), resized)

        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

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
        return "White (User)" if board.turn == chess.WHITE else "Black (Arona AI)"

chess_manager = DiscordChessManager()