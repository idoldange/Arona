import os
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import discord

from utils.presence_tracker import PresenceTracker, PresenceTrackerDatabase
from utils.tool_schemas import get_gemini_tools


def make_member(user_id: int, activities: list, guild_id: int = 42):
    return SimpleNamespace(
        id=user_id,
        display_name=f"Member {user_id}",
        guild=SimpleNamespace(id=guild_id),
        activities=activities,
    )


def make_activity(application_id: int | None = 123456):
    return SimpleNamespace(
        type=discord.ActivityType.playing,
        name="Demo Game",
        application_id=application_id,
        details="Playing a demo",
        state=None,
        assets=SimpleNamespace(large_image="demo-cover"),
    )


class FakeGuild:
    def __init__(self, guild_id: int, owner_id: int, members: dict[int, SimpleNamespace]):
        self.id = guild_id
        self.owner_id = owner_id
        self.members = members

    def get_member(self, member_id: int):
        return self.members.get(member_id)

    async def fetch_member(self, member_id: int):
        return self.members.get(member_id)


class PresenceTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        known_patch = patch.object(PresenceTracker, "_user_is_known", return_value=True)
        known_patch.start()
        self.addCleanup(known_patch.stop)

    def test_aggregate_summary_tool_is_absent_in_dms(self):
        dm_message = SimpleNamespace(
            channel=SimpleNamespace(id=1),
            author=SimpleNamespace(voice=None),
            guild=None,
        )
        server_message = SimpleNamespace(
            channel=SimpleNamespace(id=2),
            author=SimpleNamespace(voice=None),
            guild=SimpleNamespace(voice_client=None),
        )

        def has_summary_tool(message):
            declarations = get_gemini_tools(message)[0]["function_declarations"]
            return any(tool.get("name") == "fetch_guild_presence_summary" for tool in declarations)

        self.assertFalse(has_summary_tool(dm_message))
        self.assertTrue(has_summary_tool(server_message))

    async def test_completed_session_uses_discord_application_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "presence.db")
            database = PresenceTrackerDatabase(db_path)
            tracker = PresenceTracker(None, database)
            stopped = make_member(7, [])
            playing = make_member(7, [make_activity()])

            await tracker.update_presence(stopped, playing)

            active = await tracker.get_presence(playing)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["application_id"], 123456)
            self.assertTrue(os.path.exists(db_path))
            self.assertEqual(await database.readDB("SELECT * FROM historyPresences"), [])

            await tracker.update_presence(playing, stopped)

            presence_rows = await database.readDB(
                "SELECT presenceID, name, description, avatar FROM presence"
            )
            history_rows = await database.readDB(
                "SELECT userID, presenceID FROM historyPresences"
            )
            user_presence_rows = await database.readDB(
                "SELECT userID, presenceID, played FROM userPresences"
            )

            self.assertEqual(presence_rows, [(123456, "Demo Game", "Playing a demo", "demo-cover")])
            self.assertEqual(history_rows, [(7, 123456)])
            self.assertEqual(len(user_presence_rows), 1)
            self.assertEqual(user_presence_rows[0][:2], (7, 123456))
            self.assertGreaterEqual(user_presence_rows[0][2], 0)
            self.assertEqual(await tracker.get_presence(stopped), [])
            self.assertTrue(tracker.completed_sessions[0]["persisted"])

    async def test_presence_is_not_saved_for_unknown_users(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "presence.db")
            tracker = PresenceTracker(None, PresenceTrackerDatabase(db_path))
            stopped = make_member(999, [])
            playing = make_member(999, [make_activity()])

            with patch.object(PresenceTracker, "_user_is_known", return_value=False):
                await tracker.update_presence(stopped, playing)
                await tracker.update_presence(playing, stopped)

            self.assertEqual(await tracker.database.readDB("SELECT presenceID FROM presence"), [])
            self.assertEqual(await tracker.database.getPresenceHistory(999, 10), [])
            self.assertEqual(tracker.completed_sessions, [])

    async def test_current_presence_is_limited_to_requester(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            requester = make_member(7, [])
            other_member = make_member(8, [make_activity()])
            guild = FakeGuild(42, 7, {7: requester, 8: other_member})
            tracker = PresenceTracker(
                None,
                PresenceTrackerDatabase(os.path.join(temp_dir, "presence.db")),
            )

            result = await tracker.get_guild_member_presences(guild, 7)

            self.assertEqual(result["scope"], "requester_only")
            self.assertEqual([entry["user_id"] for entry in result["results"]], [7])
            with self.assertRaises(PermissionError):
                await tracker.get_guild_member_presences(guild, 7, [8])

    async def test_guild_summary_is_member_available_and_server_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = PresenceTrackerDatabase(os.path.join(temp_dir, "presence.db"))
            tracker = PresenceTracker(None, database)
            ended_at = discord.utils.utcnow().replace(microsecond=0)
            await database.init_database()
            await database.record_completed_presence(
                7, 123456, "Demo Game", "", None,
                ended_at - timedelta(seconds=120), ended_at, guild_id=42,
            )
            await database.record_completed_presence(
                8, 123456, "Demo Game", "", None,
                ended_at - timedelta(seconds=90), ended_at, guild_id=42,
            )
            await database.record_completed_presence(
                7, 654321, "Other Server Game", "", None,
                ended_at - timedelta(seconds=300), ended_at, guild_id=99,
            )
            playing_member = make_member(8, [make_activity()])
            guild = FakeGuild(42, 7, {7: make_member(7, []), 8: playing_member})

            summary = await tracker.get_guild_presence_summary(guild, 8)

            self.assertEqual(
                summary["currently_playing"],
                [{"game": "Demo Game", "players_now": 1}],
            )
            self.assertEqual(
                summary["games"],
                [{"game": "Demo Game", "playtime_seconds": 210, "sessions": 2}],
            )
            self.assertNotIn("user_id", str(summary))

    async def test_opt_out_deletes_history_and_stops_future_tracking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "presence.db")
            database = PresenceTrackerDatabase(db_path)
            tracker = PresenceTracker(None, database)
            started_at = discord.utils.utcnow()
            await database.init_database()
            await database.record_completed_presence(
                7, 123456, "Demo Game", "", None,
                started_at - timedelta(seconds=60), started_at, guild_id=42,
            )

            await tracker.set_user_tracking_enabled(7, False)

            self.assertEqual(await database.getPresenceHistory(7, 10), [])
            self.assertEqual(await database.readDB(
                "SELECT userID FROM userPresences WHERE userID = ?", (7,)
            ), [])
            self.assertIn(7, await database.get_opted_out_user_ids())

            stopped = make_member(7, [])
            playing = make_member(7, [make_activity()])
            await tracker.update_presence(stopped, playing)
            await tracker.update_presence(playing, stopped)

            self.assertEqual(tracker.completed_sessions, [])
            self.assertEqual(await database.getPresenceHistory(7, 10), [])

            restored_tracker = PresenceTracker(
                None,
                PresenceTrackerDatabase(db_path),
            )
            await restored_tracker._ensure_database()
            self.assertIn(7, restored_tracker.opted_out_users)
            guild = FakeGuild(42, 7, {7: playing})
            summary = await restored_tracker.get_guild_presence_summary(guild, 7)
            self.assertEqual(summary["currently_playing"], [])
            self.assertEqual(summary["games"], [])

    async def test_activity_without_application_id_stays_in_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "presence.db")
            tracker = PresenceTracker(
                None,
                PresenceTrackerDatabase(db_path),
            )
            stopped = make_member(7, [])
            playing = make_member(7, [make_activity(application_id=None)])

            await tracker.update_presence(stopped, playing)
            await tracker.update_presence(playing, stopped)

            self.assertEqual(await tracker.database.readDB("SELECT presenceID FROM presence"), [])
            self.assertEqual(await tracker.database.getPresenceHistory(7, 10), [])
            self.assertEqual(len(tracker.completed_sessions), 1)
            self.assertIsNone(tracker.completed_sessions[0]["presence_id"])
            self.assertFalse(tracker.completed_sessions[0]["persisted"])


if __name__ == "__main__":
    unittest.main()
