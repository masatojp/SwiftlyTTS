import io
import math
import types
import unittest
import wave

from lib.voice_monitor import format_status, sent_packets, status_problems, voice_status, wav_format, wav_peak_dbfs


def wav(samples, rate=24000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples))
    return buf.getvalue()


class FakeVC:
    def __init__(self, connected=True, latency=0.05, dave_version=1, dave_ready=True):
        self._connected = connected
        self.latency = latency
        self.mode = "aead_xchacha20_poly1305_rtpsize"
        self._connection = types.SimpleNamespace(dave_protocol_version=dave_version, can_encrypt=dave_ready)
        self.channel = types.SimpleNamespace(id=2, name="雑談", members=[
            types.SimpleNamespace(bot=False), types.SimpleNamespace(bot=True)])

    def is_connected(self):
        return self._connected


def guild(mute=False, suppress=False, self_mute=False):
    return types.SimpleNamespace(id=1, me=types.SimpleNamespace(
        voice=types.SimpleNamespace(mute=mute, suppress=suppress, self_mute=self_mute)))


class LevelTest(unittest.TestCase):
    def test_peak(self):
        self.assertAlmostEqual(wav_peak_dbfs(wav([0, 16384, -100])), 20 * math.log10(0.5), places=3)
        self.assertEqual(wav_peak_dbfs(wav([0, 0, 0])), float("-inf"))
        self.assertEqual(wav_peak_dbfs(wav([])), float("-inf"))
        self.assertIsNone(wav_peak_dbfs(b"not a wav"))

    def test_format(self):
        self.assertEqual(wav_format(wav([0], rate=48000, channels=1)), "48000Hz/1ch/16bit")
        self.assertEqual(wav_format(b"x"), "不明")

    def test_sent_packets_wraps(self):
        self.assertEqual(sent_packets(10, 60), 50)
        self.assertEqual(sent_packets(65530, 4), 10)
        self.assertIsNone(sent_packets(None, 3))


class StatusTest(unittest.TestCase):
    def test_healthy(self):
        s = voice_status(FakeVC(), guild())
        self.assertEqual(s["listeners"], 1)
        self.assertEqual(s["latency_ms"], 50)
        self.assertEqual(status_problems(s), [])
        self.assertIn("DAVE=v1(準備完了)", format_status(s))

    def test_problems(self):
        s = voice_status(FakeVC(dave_ready=False, latency=0.9), guild(mute=True, suppress=True))
        problems = status_problems(s)
        self.assertTrue(any("サーバーミュート" in p for p in problems))
        self.assertTrue(any("suppress" in p for p in problems))
        self.assertTrue(any("DAVE" in p for p in problems))
        self.assertTrue(any("遅延" in p for p in problems))

    def test_infinite_latency_and_disconnected(self):
        s = voice_status(FakeVC(connected=False, latency=float("inf")), guild())
        self.assertIsNone(s["latency_ms"])
        self.assertIn("VCに接続していない", status_problems(s))

    def test_dave_off_is_not_problem(self):
        s = voice_status(FakeVC(dave_version=0, dave_ready=False), guild())
        self.assertEqual(status_problems(s), [])
        self.assertIn("DAVE=なし", format_status(s))


try:
    from cogs.voice.monitor import VoiceMonitorCog
except ImportError:  # discord.py 未導入
    VoiceMonitorCog = None


@unittest.skipIf(VoiceMonitorCog is None, "discord.py が未インストール")
class MonitorCogTest(unittest.TestCase):
    def test_logs_changes_and_problems_once(self):
        vc = FakeVC()
        vc.guild = guild()
        bot = types.SimpleNamespace(config={}, voice_clients=[vc])
        cog = VoiceMonitorCog(bot)
        with self.assertLogs("cogs.voice.monitor", level="INFO") as logs:
            cog.check()   # 監視開始
            cog.check()   # 変化なし: 出さない
            vc.guild.me.voice.mute = True
            cog.check()   # 状態変化 + 問題
            cog.check()   # 同じ問題: 繰り返さない
            vc.guild.me.voice.mute = False
            cog.check()   # 状態変化 + 解消
            bot.voice_clients = []
            cog.check()   # 切断
        out = "\n".join(logs.output)
        self.assertEqual(out.count("VC監視開始"), 1)
        self.assertEqual(out.count("問題: Botがサーバーミュート"), 1)
        self.assertEqual(out.count("問題が解消しました"), 1)
        self.assertEqual(out.count("VCから切断されました"), 1)
        self.assertEqual(len(logs.output), 6)


if __name__ == "__main__":
    unittest.main()
