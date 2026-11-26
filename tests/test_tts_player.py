import asyncio
import unittest

from lib.tts_player import (
    OVERFLOW_DROP_NEWEST,
    OVERFLOW_DROP_OLDEST,
    Chunk,
    GuildTTSPlayer,
    TTSItem,
)


class Harness:
    """合成・再生を時間付きで模したもの。イベントの順序を記録する。"""

    def __init__(self, synth_time=0.05, play_time=0.05):
        self.synth_time = synth_time
        self.play_time = play_time
        self.events = []
        self.priorities = []
        self.loop_start = None

    async def prepare(self, item):
        return [Chunk(s, item.speaker_id) for s in item.text.split("|") if s]

    async def synthesize(self, chunk, priority):
        self.priorities.append((chunk.text, priority))
        self.events.append(("synth_start", chunk.text))
        await asyncio.sleep(self.synth_time)
        if chunk.text == "ERR":
            raise RuntimeError("boom")
        self.events.append(("synth_end", chunk.text))
        return chunk.text

    async def play(self, audio):
        self.events.append(("play_start", audio))
        await asyncio.sleep(self.play_time)
        self.events.append(("play_end", audio))

    def played(self):
        return [a for e, a in self.events if e == "play_start"]


class PlayerTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, h, **kwargs):
        player = GuildTTSPlayer(h.prepare, h.synthesize, h.play, **kwargs)
        task = asyncio.create_task(player.run())
        self.addAsyncCleanup(self._stop, task)
        return player, task

    async def _stop(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def item(self, text, **kw):
        loop = asyncio.get_running_loop()
        return TTSItem(text, 3, done=loop.create_future(), **kw)

    async def test_plays_in_order(self):
        h = Harness()
        player, _ = await self.start(h)
        a, b = self.item("a1|a2"), self.item("b1")
        player.enqueue(a)
        player.enqueue(b)
        self.assertTrue(await asyncio.wait_for(a.done, 2))
        self.assertTrue(await asyncio.wait_for(b.done, 2))
        self.assertEqual(h.played(), ["a1", "a2", "b1"])

    async def test_next_sentence_synthesized_during_playback(self):
        h = Harness(synth_time=0.05, play_time=0.2)
        player, _ = await self.start(h)
        a = self.item("s1|s2|s3")
        player.enqueue(a)
        await asyncio.wait_for(a.done, 3)
        ev = h.events
        # s2 の合成は s1 の再生中に始まり、s1 の再生終了前に終わる
        self.assertLess(ev.index(("synth_start", "s2")), ev.index(("play_end", "s1")))
        self.assertLess(ev.index(("synth_end", "s2")), ev.index(("play_end", "s1")))
        # 先読みは1文まで: s3 の合成は s1 の再生終了後
        self.assertGreater(ev.index(("synth_start", "s3")), ev.index(("play_end", "s1")))

    async def test_first_sentence_latency_is_one_synthesis(self):
        h = Harness(synth_time=0.1, play_time=0.01)
        latencies = []
        player, _ = await self.start(h, on_first_audio=lambda item, t: latencies.append(t))
        a = self.item("|".join(f"s{i}" for i in range(5)))
        player.enqueue(a)
        await asyncio.wait_for(a.done, 3)
        # 全文合成(5文分=0.5秒)ではなく、1文分(0.1秒)程度で再生が始まる
        self.assertLess(latencies[0], 0.25)

    async def test_priority_first_chunk_vs_prefetch(self):
        h = Harness(synth_time=0.02, play_time=0.1)
        player, _ = await self.start(h)
        a = self.item("p1|p2")
        player.enqueue(a)
        await asyncio.wait_for(a.done, 2)
        self.assertEqual(h.priorities, [("p1", 0), ("p2", 1)])

    async def test_clear_discards_pending_and_prefetched(self):
        h = Harness(synth_time=0.02, play_time=0.2)
        player, _ = await self.start(h)
        a, b = self.item("a1|a2|a3"), self.item("b1")
        player.enqueue(a)
        player.enqueue(b)
        await asyncio.sleep(0.1)  # a1 再生中、a2 は合成済み
        player.clear()
        self.assertFalse(await asyncio.wait_for(b.done, 1))
        await asyncio.wait_for(a.done, 1)
        await asyncio.sleep(0.3)
        self.assertEqual(h.played(), ["a1"])
        # クリア後も新しいメッセージは読める
        c = self.item("c1")
        player.enqueue(c)
        self.assertTrue(await asyncio.wait_for(c.done, 1))
        self.assertEqual(h.played(), ["a1", "c1"])

    async def test_synthesis_error_skips_chunk(self):
        h = Harness()
        errors = []
        player, _ = await self.start(h, on_error=errors.append)
        a = self.item("x|ERR|y")
        player.enqueue(a)
        self.assertTrue(await asyncio.wait_for(a.done, 2))
        self.assertEqual(h.played(), ["x", "y"])
        self.assertEqual(len(errors), 1)

    async def test_overflow_drop_oldest(self):
        h = Harness()
        player = GuildTTSPlayer(h.prepare, h.synthesize, h.play)  # 再生ループは動かさない
        items = [self.item(f"m{i}") for i in range(4)]
        for it in items:
            player.enqueue(it, max_length=2, overflow=OVERFLOW_DROP_OLDEST)
        self.assertEqual([i.text for i in player._pending], ["m2", "m3"])
        self.assertFalse(items[0].done.result())
        self.assertEqual(player.dropped, 2)

    async def test_overflow_drop_newest(self):
        h = Harness()
        player = GuildTTSPlayer(h.prepare, h.synthesize, h.play)
        items = [self.item(f"m{i}") for i in range(3)]
        results = [player.enqueue(it, max_length=2, overflow=OVERFLOW_DROP_NEWEST) for it in items]
        self.assertEqual(results, [True, True, False])
        self.assertEqual([i.text for i in player._pending], ["m0", "m1"])

    async def test_unlimited_queue(self):
        h = Harness()
        player = GuildTTSPlayer(h.prepare, h.synthesize, h.play)
        for i in range(50):
            player.enqueue(self.item(f"m{i}"), max_length=0)
        self.assertEqual(len(player), 50)

    async def test_empty_item_finishes(self):
        h = Harness()
        player, _ = await self.start(h)
        a = self.item("")
        player.enqueue(a)
        self.assertFalse(await asyncio.wait_for(a.done, 1))

    async def test_second_run_replaces_first(self):
        h = Harness()
        player, first = await self.start(h)
        second = asyncio.create_task(player.run())
        self.addAsyncCleanup(self._stop, second)
        await asyncio.sleep(0.01)
        self.assertTrue(first.cancelled() or first.done())
        a = self.item("z")
        player.enqueue(a)
        self.assertTrue(await asyncio.wait_for(a.done, 1))
        self.assertEqual(h.played(), ["z"])

    async def test_restart_keeps_item_enqueued_between(self):
        # join のやり直し: 旧ループを止めた直後に追加したメッセージが、新ループで読まれる
        h = Harness()
        player, first = await self.start(h)
        await asyncio.sleep(0.01)
        a = self.item("joined")
        second = asyncio.create_task(player.run())
        self.addAsyncCleanup(self._stop, second)
        player.enqueue(a)
        self.assertTrue(await asyncio.wait_for(a.done, 1))
        self.assertEqual(h.played(), ["joined"])

    async def test_cancel_clears_pending(self):
        h = Harness(play_time=0.2)
        player, task = await self.start(h)
        a, b = self.item("a"), self.item("b")
        player.enqueue(a)
        player.enqueue(b)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(await asyncio.wait_for(b.done, 1))
        self.assertEqual(len(player), 0)


if __name__ == "__main__":
    unittest.main()
