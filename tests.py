import os
import tempfile
import unittest
from types import SimpleNamespace

import discord

from utils.presence_tracker import PresenceTracker, PresenceTrackerDatabase


def make_member(user_id: int, activities: list, guild_id: int = 42):
    return SimpleNamespace(
        id=user_id,
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


class PresenceTrackerTests(unittest.IsolatedAsyncioTestCase):
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
            self.assertFalse(os.path.exists(db_path))

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

            self.assertFalse(os.path.exists(db_path))
            self.assertEqual(len(tracker.completed_sessions), 1)
            self.assertIsNone(tracker.completed_sessions[0]["presence_id"])
            self.assertFalse(tracker.completed_sessions[0]["persisted"])


if __name__ == "__main__":
    unittest.main()
