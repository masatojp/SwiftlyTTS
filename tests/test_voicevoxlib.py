import asyncio
import io
import unittest
import wave

from aiohttp import web

from lib.VOICEVOXlib import (
    FORMAT_DISCORD,
    AudioCache,
    PriorityLimiter,
    SynthesisError,
    VOICEVOXLib,
    wav_to_discord_pcm,
)


def make_wav(rate=48000, channels=1, frames=b"\x01\x00\x02\x00"):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


class PriorityLimiterTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_and_priority_order(self):
        limiter = PriorityLimiter(1)
        order = []
        await limiter.acquire()  # 枠を埋めておく

        async def worker(name, prio):
            await limiter.acquire(prio)
            order.append(name)
            limiter.release()

        tasks = [asyncio.create_task(worker("prefetch1", 1)), asyncio.create_task(worker("prefetch2", 1))]
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(worker("first", 0)))
        await asyncio.sleep(0)
        limiter.release()
        await asyncio.gather(*tasks)
        self.assertEqual(order, ["first", "prefetch1", "prefetch2"])

    async def test_cancelled_waiter_does_not_leak_slot(self):
        limiter = PriorityLimiter(1)
        await limiter.acquire()
        waiter = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        limiter.release()
        await asyncio.wait_for(limiter.acquire(), 1)  # 枠が戻っていること
        limiter.release()
        self.assertEqual(limiter._active, 0)


class AudioCacheTest(unittest.TestCase):
    def test_lru_by_bytes(self):
        cache = AudioCache(max_bytes=10, max_entries=100)
        cache.put("a", b"12345")
        cache.put("b", b"12345")
        cache.get("a")  # a を最近使用に
        cache.put("c", b"12345")  # b が追い出される
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), b"12345")
        self.assertLessEqual(cache.size_bytes, 10)

    def test_lru_by_entries(self):
        cache = AudioCache(max_bytes=1000, max_entries=2)
        for k in "abc":
            cache.put(k, b"x")
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("a"))

    def test_disabled(self):
        cache = AudioCache(max_bytes=0, max_entries=10)
        cache.put("a", b"x")
        self.assertIsNone(cache.get("a"))


class PcmConversionTest(unittest.TestCase):
    def test_mono_to_stereo(self):
        pcm = wav_to_discord_pcm(make_wav(frames=b"\x01\x00\x02\x00"))
        self.assertEqual(pcm, b"\x01\x00\x01\x00\x02\x00\x02\x00")

    def test_stereo_passthrough(self):
        frames = b"\x01\x00\x02\x00"
        self.assertEqual(wav_to_discord_pcm(make_wav(channels=2, frames=frames)), frames)

    def test_other_rate_returns_none(self):
        self.assertIsNone(wav_to_discord_pcm(make_wav(rate=24000)))


class FakeEngine:
    """audio_query / synthesis だけを持つ偽 Engine。"""

    def __init__(self, synthesis_statuses=()):
        self.statuses = list(synthesis_statuses)
        self.queries = []
        self.synth_bodies = []

    def app(self):
        app = web.Application()
        app.router.add_post("/audio_query", self.audio_query)
        app.router.add_post("/synthesis", self.synthesis)
        return app

    async def audio_query(self, request):
        self.queries.append(dict(request.query))
        return web.json_response({"speedScale": 1.0, "outputSamplingRate": 24000, "outputStereo": False})

    async def synthesis(self, request):
        self.synth_bodies.append(await request.json())
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return web.Response(status=status)
        return web.Response(body=make_wav(), content_type="audio/wav")


class VOICEVOXLibTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, engine):
        runner = web.AppRunner(engine.app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.addAsyncCleanup(runner.cleanup)
        lib = VOICEVOXLib(f"http://127.0.0.1:{port}", concurrency=1, cache_mb=1, cache_entries=10)
        lib.backup_urls = []
        self.addAsyncCleanup(lib.close)
        return lib

    async def test_synthesize_and_cache(self):
        engine = FakeEngine()
        lib = await self.start(engine)
        wav1 = await lib.synthesize_wav("こんにちは", 3, 1.2)
        wav2 = await lib.synthesize_wav("こんにちは", 3, 1.2)
        self.assertEqual(wav1, wav2)
        self.assertEqual(len(engine.queries), 1)  # 2回目はキャッシュ
        body = engine.synth_bodies[0]
        self.assertEqual(body["speedScale"], 1.2)
        self.assertEqual(body["outputSamplingRate"], 48000)
        self.assertNotIn("is_kana", engine.queries[0])

    async def test_cache_key_includes_speed_and_speaker(self):
        engine = FakeEngine()
        lib = await self.start(engine)
        await lib.synthesize_wav("あ", 3, 1.0)
        await lib.synthesize_wav("あ", 3, 1.5)
        await lib.synthesize_wav("あ", 1, 1.0)
        self.assertEqual(len(engine.queries), 3)

    async def test_retry_on_5xx(self):
        engine = FakeEngine(synthesis_statuses=[500, 503])
        lib = await self.start(engine)
        await lib.synthesize_wav("あ", 3, output_format=FORMAT_DISCORD)
        self.assertEqual(len(engine.synth_bodies), 3)

    async def test_no_retry_on_4xx(self):
        engine = FakeEngine(synthesis_statuses=[422])
        lib = await self.start(engine)
        with self.assertRaises(SynthesisError):
            await lib.synthesize_wav("あ", 3)
        self.assertEqual(len(engine.synth_bodies), 1)

    async def test_compat_synthesize_bytes(self):
        engine = FakeEngine()
        lib = await self.start(engine)
        url, wav = await lib.synthesize_bytes("あ", 3)
        self.assertTrue(url.startswith("http://127.0.0.1"))
        self.assertTrue(wav.startswith(b"RIFF"))
        # Web のサンプル用は Engine 既定の形式のまま
        self.assertEqual(engine.synth_bodies[0]["outputSamplingRate"], 24000)


if __name__ == "__main__":
    unittest.main()
