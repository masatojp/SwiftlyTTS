"""VoiceReadCog の読み上げ経路(キュー → 前処理 → 合成 → 再生)の結線テスト。

Discord・DB・VOICEVOX はフェイクに置き換える。
"""

import asyncio
import io
import threading
import types
import unittest
import wave
from collections import defaultdict

try:
    import discord
    from cogs.voice.basic import VoiceReadCog
    from cogs.voice.dictionary import DictionaryCog
    from lib.text_pipeline import MessageContext
    from lib.tts_player import TTSItem
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


def wav48k(n_frames=960 * 3):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x01\x00" * n_frames)
    return buf.getvalue()


class FakeVoiceClient:
    """discord.VoiceClient の代わり。別スレッドで音源を読み切ってから after を呼ぶ。"""

    def __init__(self, frame_sleep=0.002):
        self.sources = []
        self.frame_sleep = frame_sleep
        self._playing = threading.Event()
        self._stop = threading.Event()

    def is_connected(self):
        return True

    def is_playing(self):
        return self._playing.is_set()

    def stop(self):
        self._stop.set()

    def play(self, source, after=None):
        self.sources.append(source)
        self._playing.set()
        self._stop.clear()

        def run():
            frames = 0
            while not self._stop.is_set():
                data = source.read()
                if not data:
                    break
                frames += 1
                self._stop.wait(self.frame_sleep)
            source.frames_read = frames
            self._playing.clear()
            if after:
                after(None)

        threading.Thread(target=run, daemon=True).start()


class FakeDB:
    def __init__(self):
        self.guild_rows = {}
        self.user_rows = {}

    async def get_server_voice_speed(self, guild_id):
        return 1.3

    async def get_all_global_dictionary(self):
        return []

    async def get_all_dictionary(self, guild_id):
        return self.guild_rows.get(guild_id, [])

    async def get_all_user_dictionary(self, user_id):
        return self.user_rows.get(user_id, [])


class FakeVoicelib:
    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay

    async def synthesize_wav(self, text, speaker_id, speed=1.0, *, priority=1, output_format="discord", use_cache=True):
        self.calls.append((text, speaker_id, speed, priority))
        await asyncio.sleep(self.delay)
        return wav48k()


@unittest.skipUnless(HAS_DISCORD, "discord.py が未インストール")
class VoiceCogTest(unittest.IsolatedAsyncioTestCase):
    GUILD_ID = 100

    async def asyncSetUp(self):
        loop = asyncio.get_running_loop()
        self.voice_client = FakeVoiceClient()
        self.guild = types.SimpleNamespace(id=self.GUILD_ID, shard_id=0, voice_client=self.voice_client)
        cogs = {}
        self.bot = types.SimpleNamespace(
            loop=loop,
            config={"tts": {"max_length": 150}},
            tts_counter=0,
            error_counter=0,
            shard_tts_counters=defaultdict(int),
            shard_error_counters=defaultdict(int),
            get_guild=lambda gid: self.guild if gid == self.GUILD_ID else None,
            get_cog=lambda name: cogs.get(name),
        )
        self.cog = VoiceReadCog(self.bot)
        self.cog.db = FakeDB()
        self.voicelib = FakeVoicelib()
        self.cog.voicelib = self.voicelib
        self.dict_cog = DictionaryCog(self.bot)
        self.dict_cog.db = self.cog.db
        cogs["DictionaryCog"] = self.dict_cog

    async def asyncTearDown(self):
        for task in self.cog.queue_tasks.values():
            task.cancel()
        await asyncio.gather(*self.cog.queue_tasks.values(), return_exceptions=True)

    async def test_message_is_preprocessed_split_and_played_as_pcm(self):
        self.cog.db.guild_rows = {self.GUILD_ID: [{"key": "鯖", "value": "さば"}]}
        item = TTSItem(
            "<@1>さん、鯖に来て。ありがとうwwwwww", 3, user_name="masato", author_id=1,
            ctx=MessageContext(users={1: "まさと"}), done=asyncio.get_running_loop().create_future(),
        )
        self.cog.enqueue_tts(self.GUILD_ID, item)
        self.cog.ensure_queue_task(self.GUILD_ID)
        self.assertTrue(await asyncio.wait_for(item.done, 3))
        texts = [c[0] for c in self.voicelib.calls]
        self.assertEqual(texts, ["まさとさん、サバに来て。", "ありがとうwww"])
        self.assertTrue(all(c[2] == 1.3 for c in self.voicelib.calls))  # サーバーの速度設定
        # ffmpeg を使わず PCMAudio で再生している
        self.assertEqual(len(self.voice_client.sources), 2)
        self.assertTrue(all(isinstance(s, discord.PCMAudio) for s in self.voice_client.sources))
        self.assertEqual(self.voice_client.sources[0].frames_read, 3)  # 20ms x 3 フレーム
        self.assertEqual(self.bot.tts_counter, 1)

    async def test_stop_command_clears_queue(self):
        self.voice_client.frame_sleep = 0.05
        loop = asyncio.get_running_loop()
        items = [TTSItem(f"メッセージ{i}", 3, done=loop.create_future()) for i in range(3)]
        for it in items:
            self.cog.enqueue_tts(self.GUILD_ID, it)
        self.cog.ensure_queue_task(self.GUILD_ID)
        await asyncio.sleep(0.05)
        self.cog.stop_tts(self.guild)
        results = await asyncio.wait_for(asyncio.gather(*(it.done for it in items)), 3)
        self.assertEqual(results[1:], [False, False])
        self.assertLessEqual(len(self.voice_client.sources), 1)

    async def test_queue_overflow_setting(self):
        self.bot.config["tts"].update({"queue_max_length": 2, "queue_overflow": "drop_oldest"})
        for i in range(4):
            self.cog.enqueue_tts(self.GUILD_ID, TTSItem(f"m{i}", 3))
        player = self.cog.get_player(self.GUILD_ID)
        self.assertEqual([i.text for i in player._pending], ["m2", "m3"])

    async def test_zundamon_reads_username_when_enabled(self):
        self.bot.config["zundamon_read_username_enabled"] = True
        item = TTSItem("こんにちは", 3, user_name="ﾏｻﾄ", done=asyncio.get_running_loop().create_future())
        self.cog.enqueue_tts(self.GUILD_ID, item)
        self.cog.ensure_queue_task(self.GUILD_ID)
        await asyncio.wait_for(item.done, 3)
        self.assertEqual(self.voicelib.calls[0][0], "マサト、こんにちは")

    async def test_system_message_skips_dictionary(self):
        self.cog.db.guild_rows = {self.GUILD_ID: [{"key": "接続", "value": "せつぞくぅ"}]}
        item = TTSItem("接続しました。", 3, apply_dictionary=False, done=asyncio.get_running_loop().create_future())
        self.cog.enqueue_tts(self.GUILD_ID, item)
        self.cog.ensure_queue_task(self.GUILD_ID)
        await asyncio.wait_for(item.done, 3)
        self.assertEqual(self.voicelib.calls[0][0], "接続しました。")


if __name__ == "__main__":
    unittest.main()
