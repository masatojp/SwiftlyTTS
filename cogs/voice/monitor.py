"""VC の接続状況を定期的に調べてログに出す。

- 接続・切断・チャンネル移動・DAVE(E2EE)の状態・サーバーミュートが変わったとき: INFO
- 音が届かない原因になりうる状態になったとき: WARNING(解消したら INFO)

間隔は config.yml の tts.voice_monitor_interval(秒、既定 30、0 で無効)。
"""

import asyncio
import logging

from discord.ext import commands

from lib.voice_monitor import format_status, status_problems, voice_status

# 変化を記録する項目(遅延や聞き手の人数は変わりやすいので含めない)
TRACKED_KEYS = ("connected", "channel", "dave_version", "dave_ready", "server_mute", "suppress", "self_mute", "mode")


class VoiceMonitorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self._last: dict = {}
        self._last_problems: dict = {}
        self._task = None

    def interval(self) -> float:
        config = getattr(self.bot, "config", None) or {}
        try:
            return float((config.get("tts") or {}).get("voice_monitor_interval", 30))
        except (TypeError, ValueError):
            return 30.0

    async def cog_load(self):
        self._task = asyncio.create_task(self._loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            interval = self.interval()
            if interval <= 0:
                await asyncio.sleep(60)
                continue
            try:
                self.check()
            except Exception:
                self.logger.exception("VC監視でエラー")
            await asyncio.sleep(interval)

    def check(self) -> None:
        seen = set()
        for vc in list(getattr(self.bot, "voice_clients", [])):
            guild = getattr(vc, "guild", None)
            if guild is None:
                continue
            seen.add(guild.id)
            status = voice_status(vc, guild)
            tracked = {k: status.get(k) for k in TRACKED_KEYS}
            if self._last.get(guild.id) != tracked:
                verb = "VC状態" if guild.id in self._last else "VC監視開始"
                self.logger.info(f"[VC監視] guild={guild.id} {verb}: {format_status(status)}")
                self._last[guild.id] = tracked
            problems = status_problems(status)
            previous = self._last_problems.get(guild.id, [])
            if problems and problems != previous:
                self.logger.warning(f"[VC監視] guild={guild.id} 問題: {' / '.join(problems)} | {format_status(status)}")
            elif previous and not problems:
                self.logger.info(f"[VC監視] guild={guild.id} 問題が解消しました | {format_status(status)}")
            self._last_problems[guild.id] = problems
        for guild_id in set(self._last) - seen:
            self.logger.info(f"[VC監視] guild={guild_id} VCから切断されました")
            self._last.pop(guild_id, None)
            self._last_problems.pop(guild_id, None)


async def setup(bot):
    await bot.add_cog(VoiceMonitorCog(bot))
