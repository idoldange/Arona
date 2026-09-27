"""
Interactive chess board for Discord: click-to-select-piece, click-to-select-
destination, highlighted squares — like chess.com — instead of typing
`!arona chess move <uci>` or going through the Gemini model.

Everything happens via interaction.response.edit_message() on the ORIGINAL
board message, so playing a game never spams new messages into the channel.

Discord hard-caps a Select at 25 options and a View at 5 action rows, so this
uses a two-step picker (from-square select -> to-square select -> optional
promotion-piece select) instead of one big 64-button grid, which wouldn't fit.
"""
import json
import os
from io import BytesIO

import chess
import discord

from console import console
from games.chess import chess_manager

# Persisted board messages: {channel_id: message_id} of the latest interactive
# board per channel. Lets us re-attach fresh ChessBoardViews after a bot restart
# — the View instances themselves only live in memory, so without this the
# dropdowns on old board messages would die with the process.
CHESS_BOARD_MESSAGES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "games", "chess_board_messages.json",
)


def _load_board_messages() -> dict:
    try:
        if os.path.exists(CHESS_BOARD_MESSAGES_FILE):
            with open(CHESS_BOARD_MESSAGES_FILE, "r") as f:
                return json.load(f) or {}
    except Exception as e:
        console.log(f"Error loading chess board messages: {e}", "WARN")
    return {}


def _save_board_messages(data: dict):
    try:
        os.makedirs(os.path.dirname(CHESS_BOARD_MESSAGES_FILE), exist_ok=True)
        with open(CHESS_BOARD_MESSAGES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        console.log(f"Error saving chess board messages: {e}", "WARN")


def remember_board_message(channel_id: int, message_id: int):
    """Track the latest interactive board message for a channel."""
    data = _load_board_messages()
    data[str(channel_id)] = message_id
    _save_board_messages(data)


def forget_board_message(channel_id: int):
    data = _load_board_messages()
    if str(channel_id) in data:
        del data[str(channel_id)]
        _save_board_messages(data)


def get_saved_board_message(channel_id: int) -> int | None:
    raw = _load_board_messages().get(str(channel_id))
    return int(raw) if raw else None


async def restore_board_views(client: discord.Client):
    """
    Re-attach fresh ChessBoardViews to the latest saved board message per
    channel after a bot restart. The View instances are in-memory only, so this
    revives the dropdowns/buttons on boards sent by a previous bot process.
    """
    data = _load_board_messages()
    restored = 0
    for channel_id_str, message_id in data.items():
        channel_id = int(channel_id_str)
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)
            message = await channel.fetch_message(int(message_id))
        except Exception as e:
            console.log(f"[ChessBoard] Could not restore board in {channel_id}: {e}", "WARN")
            forget_board_message(channel_id)
            continue
        mode = "pvp" if chess_manager.is_pvp_game(channel_id) else "engine"
        view = ChessBoardView(channel_id, mode=mode)
        view.message = message
        try:
            await message.edit(view=view)
            restored += 1
        except Exception as e:
            console.log(f"[ChessBoard] Failed to re-attach view in {channel_id}: {e}", "WARN")
            forget_board_message(channel_id)
    if restored:
        console.log(f"[ChessBoard] Restored {restored} interactive board(s) after restart", "INFO")

PIECE_SYMBOLS = {
    'P': '♙', 'N': '♘', 'B': '♗', 'R': '♖', 'Q': '♕', 'K': '♔',
    'p': '♟', 'n': '♞', 'b': '♝', 'r': '♜', 'q': '♛', 'k': '♚',
}

PROMO_LABELS = {
    'q': ("Queen", '♕'), 'r': ("Rook", '♖'), 'b': ("Bishop", '♗'), 'n': ("Knight", '♘'),
}


def _board_file(channel_id, **kwargs) -> discord.File:
    png = chess_manager.get_board_image_bytes(channel_id, **kwargs)
    return discord.File(BytesIO(png), filename="chess_board.png")


class ChessPromoSelect(discord.ui.Select):
    def __init__(self, parent_view: "ChessBoardView", from_sq: str, to_sq: str):
        self.parent_view = parent_view
        self.from_sq = from_sq
        self.to_sq = to_sq
        options = [
            discord.SelectOption(label=name, value=code, emoji=emoji)
            for code, (name, emoji) in PROMO_LABELS.items()
        ]
        super().__init__(placeholder=f"Promote {from_sq}->{to_sq} to...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction):
            return
        await self.parent_view.handle_move(interaction, self.from_sq, self.to_sq, promotion=self.values[0])


class ChessToSelect(discord.ui.Select):
    def __init__(self, parent_view: "ChessBoardView", from_sq: str, targets: list):
        self.parent_view = parent_view
        self.from_sq = from_sq
        options = [
            discord.SelectOption(
                label=f"{from_sq} -> {to_sq}" + (" (promotes)" if is_promo else ""),
                value=to_sq,
            )
            for to_sq, is_promo in targets[:25]
        ]
        super().__init__(placeholder=f"Move {from_sq} to...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction):
            return
        to_sq = self.values[0]
        is_promo = dict(self.parent_view.current_targets).get(to_sq, False)
        if is_promo:
            await self.parent_view.ask_promotion(interaction, self.from_sq, to_sq)
        else:
            await self.parent_view.handle_move(interaction, self.from_sq, to_sq)


class ChessFromSelect(discord.ui.Select):
    def __init__(self, parent_view: "ChessBoardView", squares: list):
        self.parent_view = parent_view
        board = chess_manager._get_game(parent_view.channel_id)
        options = []
        for sq in squares[:25]:
            piece = board.piece_at(chess.parse_square(sq))
            symbol = PIECE_SYMBOLS.get(piece.symbol(), "") if piece else ""
            options.append(discord.SelectOption(label=f"{symbol} {sq}".strip(), value=sq))
        super().__init__(placeholder="Select a piece to move...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction):
            return
        await self.parent_view.show_targets(interaction, self.values[0])


class ChessResignButton(discord.ui.Button):
    def __init__(self, parent_view: "ChessBoardView"):
        super().__init__(label="Resign", style=discord.ButtonStyle.danger, row=4)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction, require_turn=False):
            return
        channel_id = self.parent_view.channel_id
        resigning_side = None
        if self.parent_view.mode == "pvp":
            white_id, black_id = chess_manager.get_pvp_players(channel_id)
            resigning_side = "white" if interaction.user.id == white_id else "black"
        success, msg = chess_manager.resign_game(channel_id, resigning_side=resigning_side)
        board = chess_manager._get_game(channel_id)
        self.parent_view.current_targets = []
        if success:
            self.parent_view.stop()
        self.parent_view._build_from_stage()
        file = _board_file(channel_id)
        await interaction.response.edit_message(content=msg, attachments=[file], view=self.parent_view)


class ChessNewGameButton(discord.ui.Button):
    def __init__(self, parent_view: "ChessBoardView"):
        super().__init__(label="New game", style=discord.ButtonStyle.success, row=4)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction, require_turn=False):
            return
        channel_id = self.parent_view.channel_id
        reply_lines = []
        if self.parent_view.mode == "pvp":
            white_id, black_id = chess_manager.get_pvp_players(channel_id)
            success, msg = chess_manager.start_pvp_game(channel_id, white_id, black_id)
            reply_lines.append(msg)
        else:
            user_color = chess_manager.get_user_color(channel_id)
            success, msg = chess_manager.start_engine_game(channel_id, user_color=user_color)
            reply_lines.append(msg)
            if success and user_color == "black":
                # Arona is White, so Arona opens before the board shows.
                eng_success, eng_msg, _ = await chess_manager.engine_play_move(channel_id)
                reply_lines.append(eng_msg if eng_success else f"Engine move failed: {eng_msg}")
        board = chess_manager._get_game(channel_id)
        self.parent_view.current_targets = []
        self.parent_view._build_from_stage()
        if board.is_game_over():
            self.parent_view.stop()
        file = _board_file(channel_id)
        await interaction.response.edit_message(content="\n".join(reply_lines), attachments=[file], view=self.parent_view)


class ChessRefreshButton(discord.ui.Button):
    def __init__(self, parent_view: "ChessBoardView"):
        super().__init__(label="Reset selection", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not await self.parent_view._check_owner(interaction):
            return
        self.parent_view.current_targets = []
        self.parent_view._build_from_stage()
        file = _board_file(self.parent_view.channel_id)
        await interaction.response.edit_message(attachments=[file], view=self.parent_view)


# One live interactive board per channel. Keeping the newest view over a
# channel means the latest board message stays usable indefinitely (no timeout)
# while older board messages from the same channel get their views stopped.
_channel_views: dict = {}


class ChessBoardView(discord.ui.View):
    """
    Attach to the board image message this manages. Clicking a piece then a
    destination plays the move and (if engine_mode) the local engine's reply,
    all via message edits — never a new message per move.

    timeout=None: the newest board message keeps working for as long as the
    bot is up (discord only expires a component interaction if the message
    itself is later edited without a view or the bot stops).
    """
    def __init__(self, channel_id: int, *, mode: str = "engine", owner_id: int = None, timeout: float = None):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        # "engine" (vs local Stockfish) or "pvp" (vs another Discord user).
        self.mode = mode
        # None = anyone in the channel can play (matches the text-command behavior,
        # which has no author restriction either); set to lock it to whoever started it.
        # Ignored in pvp mode, where the two pvp_sessions player IDs gate moves instead.
        self.owner_id = owner_id
        self.current_targets: list = []
        self.message: discord.Message = None
        previous = _channel_views.get(channel_id)
        if previous is not None and previous is not self:
            previous.stop()
        _channel_views[channel_id] = self
        self._build_from_stage()

    async def _check_owner(self, interaction: discord.Interaction, require_turn: bool = True) -> bool:
        """
        Permission gate for every button/select callback.
        pvp mode: interaction.user must be one of the two pvp_sessions players; when
        require_turn is True (move selects) it must also be that player's turn on the
        board right now. require_turn=False (resign/new game) only checks membership.
        engine/legacy mode: unchanged owner_id check.
        """
        if self.mode == "pvp":
            white_id, black_id = chess_manager.get_pvp_players(self.channel_id)
            if interaction.user.id not in (white_id, black_id):
                await interaction.response.send_message("You are not a player in this game.", ephemeral=True)
                return False
            if require_turn:
                board = chess_manager._get_game(self.channel_id)
                expected_id = white_id if board.turn == chess.WHITE else black_id
                if interaction.user.id != expected_id:
                    await interaction.response.send_message("It's not your turn yet.", ephemeral=True)
                    return False
            return True
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your game to move in.", ephemeral=True)
            return False
        return True

    def _build_from_stage(self):
        self.clear_items()
        if not chess_manager.is_game_over(self.channel_id):
            squares = chess_manager.get_movable_squares(self.channel_id)
            if squares:
                self.add_item(ChessFromSelect(self, squares))
        self.add_item(ChessRefreshButton(self))
        self.add_item(ChessResignButton(self))
        self.add_item(ChessNewGameButton(self))

    async def show_targets(self, interaction: discord.Interaction, from_sq: str):
        targets = chess_manager.get_legal_targets(self.channel_id, from_sq)
        self.current_targets = targets
        self.clear_items()
        squares = chess_manager.get_movable_squares(self.channel_id)
        if squares:
            self.add_item(ChessFromSelect(self, squares))
        if targets:
            self.add_item(ChessToSelect(self, from_sq, targets))
        self.add_item(ChessRefreshButton(self))
        self.add_item(ChessNewGameButton(self))
        file = _board_file(self.channel_id, highlight_from=from_sq, highlight_targets=[t for t, _ in targets])
        await interaction.response.edit_message(attachments=[file], view=self)

    async def ask_promotion(self, interaction: discord.Interaction, from_sq: str, to_sq: str):
        self.clear_items()
        self.add_item(ChessPromoSelect(self, from_sq, to_sq))
        self.add_item(ChessRefreshButton(self))
        self.add_item(ChessNewGameButton(self))
        file = _board_file(self.channel_id, highlight_from=from_sq, highlight_targets=[to_sq])
        await interaction.response.edit_message(attachments=[file], view=self)

    async def handle_move(self, interaction: discord.Interaction, from_sq: str, to_sq: str, promotion: str = None):
        move_str = f"{from_sq}{to_sq}{promotion or ''}"
        success, msg, _ = chess_manager.play_user_move(self.channel_id, move_str)
        lines = [msg] if success else [f"Move failed: {msg}"]

        board = chess_manager._get_game(self.channel_id)
        self.current_targets = []
        self._build_from_stage()
        if chess_manager.is_game_over(self.channel_id):
            self.stop()
        if success and self.mode == "engine" and not chess_manager.is_game_over(self.channel_id):
            # Update the board with the player's move instantly, engine reply
            # lands right after in a follow-up edit so the UI never stalls.
            lines.append("Arona is thinking...")
            file = _board_file(self.channel_id)
            await interaction.response.edit_message(content="\n".join(lines), attachments=[file], view=self)
            eng_success, eng_msg, _ = await chess_manager.engine_play_move(self.channel_id)
            lines.pop()
            lines.append(eng_msg if eng_success else f"Engine move failed: {eng_msg}")
            if chess_manager.is_game_over(self.channel_id):
                self.stop()
            self._build_from_stage()
            file = _board_file(self.channel_id)
            await interaction.edit_original_response(content="\n".join(lines), attachments=[file], view=self)
        else:
            if success and self.mode == "pvp" and not chess_manager.is_game_over(self.channel_id):
                next_id = chess_manager.get_pvp_turn_user_id(self.channel_id)
                lines.append(f"It's <@{next_id}>'s turn.")
            file = _board_file(self.channel_id)
            await interaction.response.edit_message(content="\n".join(lines), attachments=[file], view=self)

    async def on_timeout(self):
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception as e:
            console.log(f"[ChessBoardView] Failed to disable on timeout: {e}", "WARN")


class ChessChallengeView(discord.ui.View):
    """
    Accept/Decline buttons for a PvP challenge (!arona chess challenge @user [white|black]).
    Only the challenged user can respond; on accept it starts a PvP game and
    sends a fresh interactive board. `challenger_color` picks which side the
    challenger takes ("white" default, "black" otherwise); the challenged user
    gets the other side.
    """
    def __init__(self, channel_id: int, challenger_id: int, challenged_id: int,
                 challenger_color: str = "white", timeout: float = 120):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.challenger_color = "black" if str(challenger_color).lower().startswith("b") else "white"
        if self.challenger_color == "white":
            self.white_id, self.black_id = challenger_id, challenged_id
        else:
            self.white_id, self.black_id = challenged_id, challenger_id
        self.message: discord.Message = None
        self.responded = False

    def challenge_text(self) -> str:
        """Announcement line naming who plays White and who plays Black."""
        return (
            f"<@{self.challenged_id}>, <@{self.challenger_id}> challenged you to a chess game! "
            f"<@{self.challenger_id}> plays as **White**, <@{self.challenged_id}> plays as **Black**. "
            f"Click to respond."
        )

    async def _check_challenged(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_challenged(interaction):
            return
        self.responded = True
        self.stop()
        success, msg = chess_manager.start_pvp_game(self.channel_id, self.white_id, self.black_id)
        await interaction.response.edit_message(content=msg, view=None)
        board_view = ChessBoardView(self.channel_id, mode="pvp")
        file = _board_file(self.channel_id)
        board_view.message = await interaction.channel.send(file=file, view=board_view)
        remember_board_message(self.channel_id, board_view.message.id)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_challenged(interaction):
            return
        self.responded = True
        self.stop()
        await interaction.response.edit_message(
            content=f"<@{self.challenged_id}> declined the challenge.", view=None
        )

    async def on_timeout(self):
        if self.responded or self.message is None:
            return
        try:
            await self.message.edit(content="The challenge has expired (no response).", view=None)
        except Exception as e:
            console.log(f"[ChessChallengeView] Failed to edit on timeout: {e}", "WARN")
