"""python -m unittest tests.test_activity -v   (chay tu thu muc goc c:/arona)"""
import copy
import os
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase

from arona.activity import chunking, server
from arona.activity.sessions import registry

PPQ, TEMPOS = 480, [[0, 120]]      # 120 bpm -> 1 beat = 0.5 s


def song():
    """3 cau hat, moi cau 4 note, cach nhau 2 beat (1 s) -> 3 chunk."""
    notes, pos = [], 0
    for phrase in range(3):
        for i in range(4):
            notes.append({"pos": pos, "dur": PPQ, "tone": 60 + i + phrase * 2, "lyric": "la"})
            pos += PPQ
        pos += 2 * PPQ
    return notes


class ChunkingTests(unittest.TestCase):
    def plan(self, notes):
        return chunking.plan_track(notes, PPQ, TEMPOS, {"temperature": 0.5})[0]

    def test_split_at_rests(self):
        cs = self.plan(song())
        self.assertEqual(len(cs), 3)
        self.assertEqual([len(c.notes) for c in cs], [4, 4, 4])
        self.assertAlmostEqual(cs[1].start_sec, 6 * 0.5 + 0.0 + 0.0, places=3)   # 6 beat: 4 note + 2 rest

    def test_edit_one_note_changes_only_its_chunk(self):
        a = self.plan(song())
        edited = song()
        edited[5]["lyric"] = "ka"           # note thu 2 cua chunk 1
        b = self.plan(edited)
        self.assertEqual([x.hash == y.hash for x, y in zip(a, b)], [True, False, True])

    def test_moving_chunk_keeps_hash(self):
        a = self.plan(song())
        moved = song()
        for n in moved[8:]:
            n["pos"] += 8 * PPQ            # doi ca chunk cuoi ra sau
        b = self.plan(moved)
        self.assertEqual(a[2].hash, b[2].hash)
        self.assertNotEqual(a[2].start_sec, b[2].start_sec)

    def test_params_change_hash(self):
        n = song()
        a = chunking.plan_track(n, PPQ, TEMPOS, {"temperature": 0.5})[0]
        b = chunking.plan_track(n, PPQ, TEMPOS, {"temperature": 0.9})[0]
        self.assertNotEqual(a[0].hash, b[0].hash)

    def test_long_phrase_is_split(self):
        notes = [{"pos": i * PPQ, "dur": PPQ, "tone": 60, "lyric": "a"} for i in range(60)]   # 30 s lien tuc
        cs = self.plan(notes)
        self.assertGreater(len(cs), 1)
        self.assertTrue(all(len(c.notes) <= chunking.MAX_CHUNK_NOTES for c in cs))
        self.assertEqual(sum(len(c.notes) for c in cs), 60)

    def test_tempo_change(self):
        t = chunking.normalize_tempos([[0, 120], [PPQ * 4, 60]])
        self.assertAlmostEqual(chunking.tick_to_sec(PPQ * 6, PPQ, t), 4 * 0.5 + 2 * 1.0, places=6)

    def test_ust_starts_at_first_note(self):
        c = self.plan(song())[1]
        ust = chunking.chunk_to_ust(c, PPQ, TEMPOS).decode()
        self.assertTrue(ust.split("\r\n")[6] == "[#0000]" and "Lyric=R" not in ust.split("[#0000]")[1].split("[#0001]")[0])


class ServerAuthTests(AioHTTPTestCase):
    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        server.CACHE_DIR = self._tmp.name
        self.calls = 0
        server._user_cache.clear()
        registry._by_channel.clear()
        orig_user, orig_voice = server._user_id, server._in_voice

        async def fake_user(token):
            return 111 if token.startswith("good") else (222 if token.startswith("other") else None)
        server._user_id = fake_user
        server._in_voice = lambda uid, g, c: uid == 111          # chi user 111 dang o voice
        self.addCleanup(lambda: (setattr(server, "_user_id", orig_user), setattr(server, "_in_voice", orig_voice)))

        from arona.tts import synth as S
        orig_post = S._post_synth

        async def fake_post(body, ctype, params=None, timeout_s=0):
            self.calls += 1
            return b"RIFFfakewav", "0", None
        S._post_synth = fake_post
        self.addCleanup(lambda: setattr(S, "_post_synth", orig_post))
        return server.make_app()

    async def test_no_token_401(self):
        r = await self.client.get("/api/session")
        self.assertEqual(r.status, 401)

    async def test_no_session_403(self):
        r = await self.client.get("/api/session", headers={"Authorization": "Bearer good-token-xx", "X-Channel-Id": "42"})
        self.assertEqual(r.status, 403)

    async def test_missing_channel_403(self):
        r = await self.client.get("/api/session", headers={"Authorization": "Bearer good-token-xx"})
        self.assertEqual(r.status, 403)

    async def test_session_flow_and_chunk_cache(self):
        good = {"Authorization": "Bearer good-token-xx", "X-Channel-Id": "42"}
        r = await self.client.get("/api/session", headers=good)
        self.assertEqual(r.status, 403)                      # chua bam nut -> 403
        registry.create(42, 1, 111)
        r = await self.client.get("/api/session", headers=good)
        self.assertEqual(r.status, 200)
        r = await self.client.get("/api/session", headers={"Authorization": "Bearer other-token-xx", "X-Channel-Id": "42"})
        self.assertEqual(r.status, 403)                      # co session nhung khong o trong voice
        r = await self.client.get("/api/session", headers={"Authorization": "Bearer good-token-xx", "X-Channel-Id": "43"})
        self.assertEqual(r.status, 403)                      # kenh khac khong co session

        body = {"track": "t1", "ppq": PPQ, "tempos": TEMPOS, "notes": song()}
        r = await self.client.post("/api/vocal/plan", json=body, headers=good)
        plan = await r.json()
        self.assertEqual((len(plan["chunks"]), plan["dirty"]), (3, 3))

        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=1, hash=plan["chunks"][1]["hash"]), headers=good)
        self.assertEqual((r.status, r.headers["X-Cache"]), (200, "MISS"))
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=1), headers=good)
        self.assertEqual(r.headers["X-Cache"], "HIT")
        self.assertEqual(self.calls, 1)                      # lan 2 khong goi GPT-SoVITS

        edited = copy.deepcopy(body)
        edited["notes"][0]["lyric"] = "ka"
        r = await self.client.post("/api/vocal/plan", json=edited, headers=good)
        plan2 = await r.json()
        self.assertEqual([c["cached"] for c in plan2["chunks"]], [False, True, False])   # chunk 1 da cache, 0 va 2 can gen

        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0, hash="stale"), headers=good)
        self.assertEqual(r.status, 409)

    async def test_token_endpoint_needs_no_session(self):
        r = await self.client.post("/api/token", json={"code": "x"})
        self.assertEqual(r.status, 500)                      # khong co client secret trong test: loi cau hinh, khong phai 403


if __name__ == "__main__":
    unittest.main()
