"""python -m unittest tests.test_activity -v   (chay tu thu muc goc c:/arona)"""
import copy
import os
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase

from arona.activity import chunking, server
from arona.activity.sessions import registry

PPQ, TEMPOS = 480, [[0, 120]]      # 120 bpm -> 1 beat = 0.5 s

USTX = b"""ustx_version: 0.6
resolution: 480
bpm: 120
tracks:
- track_name: Vocal
  singer: X
voice_parts:
- track_no: 0
  position: 0
  notes:
  - {position: 0, duration: 480, tone: 60, lyric: la}
  - {position: 480, duration: 480, tone: 62, lyric: li}
"""


def make_wav(seconds=1.0, rate=44100) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeGuild:
    def __init__(self, limit):
        self.filesize_limit = limit


class FakeChannel:
    def __init__(self, limit=10 << 20, error=None):
        self.guild, self.sent, self.error = FakeGuild(limit), [], error

    async def send(self, **kw):
        if self.error:
            raise self.error
        self.sent.append(kw)
        return type("Msg", (), {"jump_url": "https://discord.com/channels/1/2/3"})()


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, cid):
        return self.channel if cid == 7 else None


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


class ImportsTests(unittest.TestCase):
    def test_ustx_and_midi_become_projects_and_junk_is_skipped(self):
        import io
        import mido
        from arona.activity.imports import build_imports
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.Message("note_on", note=60, velocity=90, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        buf = io.BytesIO()
        mid.save(file=buf)
        out = build_imports([("song.ustx", USTX), ("tune.mid", buf.getvalue()), ("junk.txt", b"hello"), ("bad.ustx", b"tracks: [")])
        self.assertEqual([p["name"] for p in out], ["song", "tune"])
        vocal = out[0]
        self.assertEqual((vocal["ppq"], vocal["tempos"]), (480, [[0, 120.0]]))
        self.assertEqual([(n["pos"], n["dur"], n["tone"], n["lyric"]) for n in vocal["tracks"][0]["notes"]],
                         [(0, 480, 60, "la"), (480, 480, 62, "li")])
        self.assertEqual(out[1]["tracks"][0]["notes"][0]["tone"], 60)


class ServerAuthTests(AioHTTPTestCase):
    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        server.CACHE_DIR = self._tmp.name
        self.calls = 0
        self.synth_result = (b"RIFFfakewav", "0", None)
        self.gate = None                      # asyncio.Event: neu co thi fake_post doi toi khi duoc set
        server._jobs.clear()
        server._job_errors.clear()
        server.WAIT_S, server.CACHE_TTL_S = 5, 3600
        self.channel = FakeChannel()
        orig_bot = server._bot
        server._bot = FakeBot(self.channel)
        self.addCleanup(lambda: setattr(server, "_bot", orig_bot))
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
            if self.gate is not None:
                await self.gate.wait()
            return self.synth_result
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

    async def _ready(self):
        registry.create(42, 1, 111)
        good = {"Authorization": "Bearer good-token-xx", "X-Channel-Id": "42"}
        body = {"track": "t1", "ppq": PPQ, "tempos": TEMPOS, "notes": song()}
        return good, body

    async def test_slow_synth_returns_202_then_cached_wav(self):
        import asyncio
        good, body = await self._ready()
        server.WAIT_S = 0.05
        self.gate = asyncio.Event()
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 202)
        self.assertEqual(r.headers["Retry-After"], str(server.RETRY_AFTER_S))
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)   # retry luc job con chay: khong gen lan 2
        self.assertEqual(r.status, 202)
        self.gate.set()
        await asyncio.sleep(0.1)                              # job nen chay xong, ghi cache
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual((r.status, r.headers["X-Cache"]), (200, "HIT"))
        self.assertEqual(self.calls, 1)

    async def test_failed_job_reports_502_and_can_retry(self):
        import asyncio
        good, body = await self._ready()
        self.synth_result = (None, "0", "gpu exploded")
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 502)
        self.assertIn("gpu exploded", (await r.json())["error"])
        self.synth_result = (b"RIFFfakewav", "0", None)
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.calls, 2)

    async def test_error_while_nobody_waiting_is_reported_on_next_poll(self):
        import asyncio
        good, body = await self._ready()
        server.WAIT_S = 0.05
        self.gate = asyncio.Event()
        self.synth_result = (None, "0", "late failure")
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 202)
        self.gate.set()
        await asyncio.sleep(0.1)
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 502)
        self.assertIn("late failure", (await r.json())["error"])

    async def test_cache_expires_after_ttl(self):
        import time
        good, body = await self._ready()
        r = await self.client.post("/api/vocal/plan", json=body, headers=good)
        h = (await r.json())["chunks"][0]["hash"]
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/vocal/plan", json=body, headers=good)
        self.assertTrue((await r.json())["chunks"][0]["cached"])
        old = time.time() - server.CACHE_TTL_S - 5
        os.utime(server._cache_path(h), (old, old))           # gia lap qua 1 gio
        r = await self.client.post("/api/vocal/plan", json=body, headers=good)
        self.assertFalse((await r.json())["chunks"][0]["cached"])
        r = await self.client.post("/api/vocal/chunk", json=dict(body, index=0), headers=good)
        self.assertEqual(r.headers["X-Cache"], "MISS")
        self.assertEqual(self.calls, 2)

    GOOD = {"Authorization": "Bearer good-token-xx", "X-Channel-Id": "42"}

    async def test_static_files_are_served_and_confined(self):
        web_dir = os.path.join(self._tmp.name, "web")
        os.makedirs(web_dir)
        open(os.path.join(web_dir, "index.html"), "w").write("<h1>hi</h1>")
        open(os.path.join(self._tmp.name, "secret.txt"), "w").write("nope")
        orig = server.STATIC_DIR
        server.STATIC_DIR = web_dir
        self.addCleanup(lambda: setattr(server, "STATIC_DIR", orig))
        r = await self.client.get("/")
        self.assertEqual((r.status, await r.text()), (200, "<h1>hi</h1>"))
        r = await self.client.get("/index.html")
        self.assertEqual(r.status, 200)
        for evil in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
            r = await self.client.get(evil)
            self.assertEqual(r.status, 404, evil)
        r = await self.client.get("/missing.js")
        self.assertEqual(r.status, 404)
        r = await self.client.get("/api/config")                  # route /api khong bi static nuot
        self.assertEqual(r.status, 200)

    def form(self, mode, wav=None, ustx=None, name="my song"):
        from aiohttp import FormData
        f = FormData()
        f.add_field("mode", mode)
        f.add_field("name", name)
        if wav is not None:
            f.add_field("audio", wav, filename="a.wav", content_type="audio/wav")
        if ustx is not None:
            f.add_field("ustx", ustx, filename="p.ustx", content_type="text/plain")
        return f

    async def test_imports_need_session_and_return_projects(self):
        r = await self.client.get("/api/session/imports", headers=self.GOOD)
        self.assertEqual(r.status, 403)
        registry.create(42, 1, 111, text_channel_id=7, imports=[{"name": "p", "ppq": 480, "tempos": [[0, 120]], "tracks": []}])
        r = await self.client.get("/api/session/imports", headers=self.GOOD)
        self.assertEqual((r.status, (await r.json())["projects"][0]["name"]), (200, "p"))

    async def test_export_without_session_403(self):
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual(r.status, 403)
        self.assertEqual(self.channel.sent, [])

    async def test_export_audio_and_ustx_posts_one_message_with_both_files(self):
        registry.create(42, 1, 111, text_channel_id=7)
        r = await self.client.post("/api/export", data=self.form("audio+ustx", make_wav(0.2), USTX), headers=self.GOOD)
        body = await r.json()
        self.assertEqual((r.status, body["files"], body["converted_to_mp3"]), (200, ["my_song.wav", "my_song.ustx"], False))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("<@111>", self.channel.sent[0]["content"])

    async def test_export_ustx_only(self):
        registry.create(42, 1, 111, text_channel_id=7)
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual((r.status, (await r.json())["files"]), (200, ["my_song.ustx"]))

    async def test_big_wav_is_converted_to_mp3(self):
        registry.create(42, 1, 111, text_channel_id=7)
        self.channel.guild.filesize_limit = 60 * 1024          # wav 1s = 88 KB > limit, mp3 nho hon nhieu
        r = await self.client.post("/api/export", data=self.form("audio", make_wav(1.0)), headers=self.GOOD)
        body = await r.json()
        self.assertEqual((r.status, body["files"], body["converted_to_mp3"]), (200, ["my_song.mp3"], True))

    async def test_export_too_big_even_as_mp3_is_413(self):
        registry.create(42, 1, 111, text_channel_id=7)
        self.channel.guild.filesize_limit = 100
        r = await self.client.post("/api/export", data=self.form("audio", make_wav(0.5)), headers=self.GOOD)
        self.assertEqual(r.status, 413)
        self.assertEqual(self.channel.sent, [])

    async def test_export_validation(self):
        registry.create(42, 1, 111, text_channel_id=7)
        for data in (self.form("nonsense", ustx=USTX),
                     self.form("audio", b"not a wav at all"),
                     self.form("audio"),
                     self.form("ustx", ustx=b"hello world"),
                     self.form("audio+ustx", make_wav(0.1))):
            registry.get(42).last_export = 0
            r = await self.client.post("/api/export", data=data, headers=self.GOOD)
            self.assertEqual(r.status, 400, await r.text())
        self.assertEqual(self.channel.sent, [])

    async def test_export_rate_limit_and_missing_text_channel(self):
        registry.create(42, 1, 111, text_channel_id=7)
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual(r.status, 429)
        registry.create(42, 1, 111, text_channel_id=999)       # kenh khong ton tai
        registry.get(42).last_export = 0
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual(r.status, 409)

    async def test_export_forbidden_channel_is_502(self):
        import discord
        registry.create(42, 1, 111, text_channel_id=7)
        self.channel.error = discord.Forbidden(type("R", (), {"status": 403, "reason": "x"})(), "no perms")
        r = await self.client.post("/api/export", data=self.form("ustx", ustx=USTX), headers=self.GOOD)
        self.assertEqual(r.status, 502)

    async def test_token_endpoint_needs_no_session(self):
        r = await self.client.post("/api/token", json={"code": "x"})
        self.assertEqual(r.status, 500)                      # khong co client secret trong test: loi cau hinh, khong phai 403


if __name__ == "__main__":
    unittest.main()
