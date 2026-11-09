import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite
import discord
from console import console

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActivePresence:
    started_at: datetime
    application_id: int | None
    description: str
    avatar: str | None


class PresenceTrackerDatabase:
    """SQLite storage for presence records and their history."""
    def __init__(self,db_path: str = "presences_tracker.db"):
        self.userNowPlaying = {}
        self.db_path = db_path
    async def init_database(self):
        async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute("""
                        CREATE TABLE IF NOT EXISTS presence (
                        presenceID  INTEGER NOT NULL PRIMARY KEY,
                        name        TEXT    NOT NULL,
                        description TEXT    NOT NULL,
                        avatar      TEXT,
                        isVerified  INTEGER NOT NULL
                    );
                    """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS historyPresences (
                        userID    INTEGER NOT NULL,
                        presenceID INTEGER NOT NULL REFERENCES presence (presenceID),
                        start     INTEGER NOT NULL,
                        end       INTEGER NOT NULL
                    );
                """)
                await db.execute("""
                        CREATE TABLE IF NOT EXISTS userPresences (
                            userID     INTEGER NOT NULL,
                        presenceID INTEGER NOT NULL REFERENCES presence (presenceID),
                            played     INTEGER DEFAULT (0) NOT NULL,
                            PRIMARY KEY (userID, presenceID)
                    );
                    """)
                await db.execute("""
                                CREATE INDEX IF NOT EXISTS idx_history_user_start
                                ON historyPresences(userID, start DESC);
                """)
                async with db.execute("PRAGMA table_info(userPresences)") as cursor:
                    user_presence_columns = await cursor.fetchall()
                primary_key = [
                    row[1]
                    for row in sorted(user_presence_columns, key=lambda column: column[5])
                    if row[5]
                ]
                if primary_key != ["userID", "presenceID"]:
                    await db.execute("ALTER TABLE userPresences RENAME TO userPresences_legacy")
                    await db.execute("""
                        CREATE TABLE userPresences (
                            userID     INTEGER NOT NULL,
                            presenceID INTEGER NOT NULL REFERENCES presence (presenceID),
                            played     INTEGER DEFAULT (0) NOT NULL,
                            PRIMARY KEY (userID, presenceID)
                        );
                    """)
                    await db.execute("""
                        INSERT INTO userPresences (userID, presenceID, played)
                        SELECT userID, presenceID,
                               SUM(CASE WHEN end > start THEN end - start ELSE 0 END)
                        FROM historyPresences
                        GROUP BY userID, presenceID;
                    """)
                    await db.execute("DROP TABLE userPresences_legacy")
                await db.commit()
    async def writeDB(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(query, params)
            await db.commit()
    async def readDB(self, query: str, params: tuple = ()) -> list[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()
    async def getPresenceHistory(self, user_id: int,size:int) -> list[tuple]:
        size = max(1, min(int(size), 100))
        return await self.readDB(
            """SELECT h.userID, h.presenceID, p.name, p.description, p.avatar,
                      p.isVerified, h.start, h.end,
                      CASE WHEN h.end > h.start THEN h.end - h.start ELSE 0 END
                          AS durationSeconds
               FROM historyPresences AS h
               JOIN presence AS p ON p.presenceID = h.presenceID
               WHERE h.userID = ? ORDER BY h.start DESC LIMIT ?""",
            (user_id, size),
        )

    async def get_presence_info(self, application_id: int) -> dict | None:
        rows = await self.readDB(
            """SELECT presenceID, name, description, avatar, isVerified
               FROM presence WHERE presenceID = ?""",
            (application_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "application_id": row[0],
            "name": row[1],
            "description": row[2],
            "avatar": row[3],
            "is_verified": bool(row[4]),
        }

    async def list_presences(
        self,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        search = f"%{query.strip()}%"
        rows = await self.readDB(
            """SELECT presenceID, name, description, avatar, isVerified
               FROM presence
               WHERE ? = '%%' OR name LIKE ? OR description LIKE ?
               ORDER BY name COLLATE NOCASE, presenceID
               LIMIT ? OFFSET ?""",
            (search, search, search, limit, offset),
        )
        return [
            {
                "application_id": row[0],
                "name": row[1],
                "description": row[2],
                "avatar": row[3],
                "is_verified": bool(row[4]),
            }
            for row in rows
        ]

    async def upsert_presence(
        self,
        application_id: int,
        name: str,
        description: str = "",
        avatar: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT 1 FROM presence WHERE presenceID = ?",
                    (application_id,),
                ) as cursor:
                    is_new = await cursor.fetchone() is None
                await db.execute(
                    """INSERT INTO presence (presenceID, name, description, avatar, isVerified)
                       VALUES (?, ?, ?, ?, 0)
                       ON CONFLICT(presenceID) DO UPDATE SET
                           name = excluded.name,
                           description = CASE WHEN excluded.description != ''
                                              THEN excluded.description
                                              ELSE presence.description END,
                           avatar = COALESCE(excluded.avatar, presence.avatar)""",
                    (application_id, name, description, avatar),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        if is_new:
            console.log(
                f"[PRESENCE] Created presence: {name} (application_id={application_id})",
                "INFO",
            )
    async def getPresenceNow(self, user_id: int) -> list[str]:
        return self.userNowPlaying.get(user_id, [])
    async def writePresenceHistory(self, user_id: int, presence_id: int, start: int, end: int):
        await self.writeDB(
            "INSERT INTO historyPresences (userID, presenceID, start, end) VALUES (?, ?, ?, ?)",
            (user_id, presence_id, start, end)
        )
    async def writePresence(self, user_id: int, presence_id: int, start: int, end: int):
        played = max(0, end - start)
        await self.writeDB(
            """INSERT INTO userPresences (userID, presenceID, played) VALUES (?, ?, ?)
               ON CONFLICT(userID, presenceID) DO UPDATE SET played = played + excluded.played""",
            (user_id, presence_id, played)
        )

    def add_user_now_playing(self, user_id: int, game_name: str, started_at: datetime):
        games = self.userNowPlaying.setdefault(user_id, [])
        if game_name not in games:
            games.append(game_name)
    def remove_user_now_playing(self, user_id: int, game_name: str):
        if user_id in self.userNowPlaying and game_name in self.userNowPlaying[user_id]:
            del self.userNowPlaying[user_id][self.userNowPlaying[user_id].index(game_name)]
            if not self.userNowPlaying[user_id]:
                del self.userNowPlaying[user_id]

    async def record_completed_presence(
        self,
        user_id: int,
        application_id: int,
        game_name: str,
        description: str,
        avatar: str | None,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        started_at = self._normalize_datetime(started_at)
        ended_at = self._normalize_datetime(ended_at)
        start_timestamp = int(started_at.timestamp())
        end_timestamp = int(ended_at.timestamp())
        played = max(0, end_timestamp - start_timestamp)
        created_presence = False

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT presenceID FROM presence WHERE presenceID = ?",
                    (application_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row is None:
                    created_presence = True
                    await db.execute(
                        """INSERT INTO presence
                           (presenceID, name, description, avatar, isVerified)
                           VALUES (?, ?, ?, ?, ?)""",
                        (application_id, game_name, description, avatar, 0),
                    )
                presence_id = application_id

                await db.execute(
                    """INSERT INTO historyPresences (userID, presenceID, start, end)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, presence_id, start_timestamp, end_timestamp),
                )
                await db.execute(
                    """INSERT INTO userPresences (userID, presenceID, played)
                       VALUES (?, ?, ?)
                              ON CONFLICT(userID, presenceID) DO UPDATE SET
                           played = played + excluded.played""",
                    (user_id, presence_id, played),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        if created_presence:
            console.log(
                f"[PRESENCE] Created presence: {game_name} (application_id={application_id})",
                "INFO",
            )

        return int(presence_id)

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class PresenceTracker:
    def __init__(
        self,
        bot: discord.Client,
        database: PresenceTrackerDatabase | None = None,
    ):
        self.bot = bot
        self.database = database or PresenceTrackerDatabase()
        self._database_initialized = False
        self.presence_data: dict[tuple[int, int, str], ActivePresence] = {}
        self.completed_sessions: list[dict] = []
        self._update_lock = asyncio.Lock()

    async def _ensure_database(self) -> None:
        if not self._database_initialized:
            await self.database.init_database()
            self._database_initialized = True

    @staticmethod
    def _playing_activities(member: discord.Member) -> dict[str, discord.Activity]:
        return {
            activity.name: activity
            for activity in member.activities
            if activity.type == discord.ActivityType.playing and activity.name
        }

    async def seed_from_cache(self) -> tuple[int, int]:
        """Initialize storage and track games already active when the bot becomes ready."""
        await self._ensure_database()
        seeded = 0
        missing_application_id = 0
        now = datetime.now(timezone.utc)
        async with self._update_lock:
            for guild in self.bot.guilds:
                for member in guild.members:
                    for activity in self._playing_activities(member).values():
                        key = (guild.id, member.id, activity.name)
                        if key in self.presence_data:
                            continue
                        await self._start_session(member, activity, now)
                        seeded += 1
                        if getattr(activity, "application_id", None) is None:
                            missing_application_id += 1
        return seeded, missing_application_id

    @staticmethod
    def _activity_avatar(activity: discord.Activity) -> str | None:
        assets = getattr(activity, "assets", None)
        return getattr(assets, "large_image", None) if assets is not None else None

    async def update_presence(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        async with self._update_lock:
            current_activities = self._playing_activities(after)
            current_games = set(current_activities)
            guild_id = after.guild.id
            user_id = after.id
            active_games = {
                game_name
                for tracked_guild_id, tracked_user_id, game_name in self.presence_data
                if tracked_guild_id == guild_id and tracked_user_id == user_id
            }
            now = datetime.now(timezone.utc)

            # Finish tracked sessions first; a failed DB write leaves them active for retry.
            for game_name in active_games - current_games:
                key = (guild_id, user_id, game_name)
                session = self.presence_data[key]
                started_at = session.started_at
                duration_seconds = max(0.0, (now - started_at).total_seconds())
                await self._record_completed_presence(
                    after,
                    game_name,
                    session,
                    now,
                    duration_seconds,
                )
                self.presence_data.pop(key, None)

            # Track newly observed games, including games already active when the bot starts.
            for game_name in current_games - active_games:
                await self._start_session(
                    after,
                    current_activities[game_name],
                    now,
                )

    async def _start_session(
        self,
        member: discord.Member,
        activity: discord.Activity,
        started_at: datetime,
    ) -> None:
        """Keep the active session in memory until it ends."""
        key = (member.guild.id, member.id, activity.name)
        self.presence_data[key] = ActivePresence(
            started_at=started_at,
            application_id=getattr(activity, "application_id", None),
            description=getattr(activity, "details", None)
            or getattr(activity, "state", None)
            or "",
            avatar=self._activity_avatar(activity),
        )
        session = self.presence_data[key]
        if session.application_id is not None:
            try:
                await self._ensure_database()
                await self.database.upsert_presence(
                    session.application_id,
                    activity.name,
                    session.description,
                    session.avatar,
                )
            except Exception:
                logger.exception("Failed to save presence metadata for %s", activity.name)
        else:
            console.log(
                f"[PRESENCE] Cannot save {activity.name}: Discord activity has no application_id",
                "WARN",
            )

    async def _record_completed_presence(
        self,
        member: discord.Member,
        game_name: str,
        session: ActivePresence,
        ended_at: datetime,
        duration_seconds: float,
    ) -> int | None:
        """Store the presence record, then reference it from history tables."""
        presence_id = session.application_id
        if presence_id is not None:
            await self._ensure_database()
            await self.database.record_completed_presence(
                member.id,
                presence_id,
                game_name,
                session.description,
                session.avatar,
                session.started_at,
                ended_at,
            )
        console.log(
            f"[PRESENCE] Session ended: user={member.id}, app={presence_id}, "
            f"name={game_name!r}, start={session.started_at.isoformat()}, "
            f"end={ended_at.isoformat()}, duration={duration_seconds:.1f}s",
            "INFO",
        )
        self.completed_sessions.append({
            "guild_id": member.guild.id,
            "user_id": member.id,
            "presence_id": presence_id,
            "game_name": game_name,
            "started_at": session.started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "persisted": presence_id is not None,
        })
        return presence_id

    async def get_presence(self, member: discord.Member) -> list[dict]:
        """Return current playing activities and known session start times."""
        guild_id = member.guild.id
        user_id = member.id
        activities = self._playing_activities(member)
        result = []
        for game_name, activity in activities.items():
            session = self.presence_data.get((guild_id, user_id, game_name))
            started_at = session.started_at if session else None
            application_id = getattr(activity, "application_id", None)
            description = (
                getattr(activity, "details", None)
                or getattr(activity, "state", None)
                or (session.description if session else "")
            )
            avatar = self._activity_avatar(activity) or (session.avatar if session else None)
            if application_id is not None:
                try:
                    await self._ensure_database()
                    await self.database.upsert_presence(
                        application_id, game_name, description, avatar
                    )
                except Exception:
                    logger.exception("Failed to refresh presence metadata for %s", game_name)
            result.append({
                "game_name": game_name,
                "started_at": started_at,
                "application_id": application_id,
                "description": description,
                "avatar": avatar,
            })
        return result

    async def get_guild_member_presences(
        self,
        guild: discord.Guild,
        requester_id: int,
        user_ids: list[int | str] | None = None,
        max_users: int = 20,
    ) -> dict:
        """Fetch current presence only for the requester and members of this guild."""
        requester = guild.get_member(requester_id)
        if requester is None:
            try:
                requester = await guild.fetch_member(requester_id)
            except discord.NotFound as error:
                raise PermissionError("Requester is not a member of this server.") from error

        if user_ids is None or user_ids == []:
            requested_ids = [requester_id]
        elif not isinstance(user_ids, list):
            raise ValueError("user_ids must be an array of Discord user IDs.")
        else:
            if len(user_ids) > max_users:
                raise ValueError(f"A maximum of {max_users} members can be fetched per request.")
            requested_ids = []
            for raw_id in user_ids:
                if isinstance(raw_id, bool) or not str(raw_id).strip().isdecimal():
                    raise ValueError("Every user_id must be a decimal Discord ID.")
                member_id = int(raw_id)
                if member_id <= 0:
                    raise ValueError("Every user_id must be a positive Discord ID.")
                if member_id not in requested_ids:
                    requested_ids.append(member_id)
            if not requested_ids:
                requested_ids = [requester_id]

        results = []
        for member_id in requested_ids:
            member = requester if member_id == requester_id else guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.NotFound:
                    results.append({"user_id": member_id, "error": "not_a_member_of_this_server"})
                    continue
                except discord.HTTPException:
                    results.append({"user_id": member_id, "error": "member_lookup_failed"})
                    continue

            results.append({
                "user_id": member.id,
                "display_name": member.display_name,
                "activities": await self.get_presence(member),
            })

        return {
            "guild_id": guild.id,
            "requester_id": requester_id,
            "scope": "current_guild_members_only",
            "activity_data_is_untrusted": True,
            "results": results,
        }

    async def get_presence_info(
        self,
        application_id: int,
        guild: discord.Guild | None = None,
    ) -> dict | None:
        await self._ensure_database()
        info = await self.database.get_presence_info(application_id)
        if info is not None:
            return info

        # Discord exposes activities through Gateway member cache, not a REST
        # endpoint that fetches presence by application ID.
        guilds = [guild] if guild is not None else list(self.bot.guilds)
        for target_guild in guilds:
            for member in target_guild.members:
                for activity in self._playing_activities(member).values():
                    if getattr(activity, "application_id", None) != application_id:
                        continue
                    description = (
                        getattr(activity, "details", None)
                        or getattr(activity, "state", None)
                        or ""
                    )
                    avatar = self._activity_avatar(activity)
                    await self.database.upsert_presence(
                        application_id,
                        activity.name,
                        description,
                        avatar,
                    )
                    return await self.database.get_presence_info(application_id)
        return None