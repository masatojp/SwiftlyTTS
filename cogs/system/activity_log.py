"""コマンドの実行を操作ログ(logs/activity-YYYY-MM-DD.log)に記録する。"""

from discord.ext import commands

from lib.app_logging import log_activity


def _options(data) -> str:
    """スラッシュコマンドの引数を「name=value」の形にする(サブコマンドの中も含む)。"""
    parts = []
    for opt in (data or {}).get("options", []) or []:
        if opt.get("type") in (1, 2):  # サブコマンド(グループ): 名前は qualified_name に含まれるので中身だけ
            sub = _options(opt)
            if sub:
                parts.append(sub)
        else:
            parts.append(f"{opt.get('name', '?')}={opt.get('value', '')}")
    return " ".join(parts)


class ActivityLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction, command):
        name = f"/{command.qualified_name}"
        if command.qualified_name == "read":
            # 読み上げた本文は本文欄に書く(logging.activity_log.include_text: false なら記録しない)
            options = (interaction.data or {}).get("options", []) or []
            text = next((o.get("value") for o in options if o.get("name") == "text"), "")
            log_activity("read", user=interaction.user, guild=interaction.guild, channel=interaction.channel,
                         detail=name, text=str(text))
            return
        args = _options(interaction.data)
        log_activity("command", user=interaction.user, guild=interaction.guild, channel=interaction.channel,
                     detail=name + (f" {args}" if args else ""))

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        log_activity("command", user=ctx.author, guild=ctx.guild, channel=ctx.channel,
                     detail=f"{ctx.prefix}{ctx.command.qualified_name}")


async def setup(bot):
    await bot.add_cog(ActivityLogCog(bot))
