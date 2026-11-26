import discord
from discord.ext import commands
from discord import app_commands
from lib.postgres import PostgresDB  # PostgresDBをインポート
from discord.ui import View, Button
import asyncio
import collections
import logging
from typing import Optional

from lib.dictionary_replacer import Replacer, build_replacer, merge_dictionaries
from lib import text_pipeline
from lib.text_pipeline import MessageContext, PipelineConfig


class DictionaryCog(commands.Cog):
    """読み上げ辞書(共通・サーバー・ユーザー)の管理と、読み上げ前処理の入口。

    辞書はメモリに保持し、メッセージごとに DB を参照しない。
    - 生の辞書行: 初回使用時に DB から読み込み、追加・削除・Web からの通知で破棄する
    - 置換器: (ギルド, ユーザー) ごとにコンパイル済みのものを LRU で保持する
    - 通知漏れに備え、tts.dictionary_refresh_interval 秒ごとに全体を読み直す
    """

    REPLACER_CACHE_SIZE = 512

    def __init__(self, bot):
        self.bot = bot
        self.db = PostgresDB()  # データベースインスタンスを初期化
        self.voice_cog = None  # VoiceReadCogの参照
        self.logger = logging.getLogger(__name__)
        self.global_dict_cache = []
        self._global_loaded = False
        self.server_dict_cache = {}  # guild_id: list of dict rows
        self.user_dict_cache = {}  # user_id: list of dict rows
        # 辞書が変わるたびに増える世代番号(読み込み中に変更された古い結果を捨てるため)
        self._generation = 0
        self._replacers: "collections.OrderedDict[tuple, Replacer]" = collections.OrderedDict()
        self.cache_task = None

    # --- 設定 ------------------------------------------------------------------

    @property
    def tts_config(self) -> dict:
        config = getattr(self.bot, "config", None) or {}
        return config.get("tts") or {}

    def pipeline_config(self) -> PipelineConfig:
        return PipelineConfig.from_mapping(self.tts_config)

    # --- ライフサイクル -----------------------------------------------------------

    async def cog_load(self):
        await self.db.initialize()  # データベース接続を初期化
        self.voice_cog = self.bot.get_cog("VoiceReadCog")  # VoiceReadCogを取得
        self.cache_task = self.bot.loop.create_task(self.cache_updater())

    async def cog_unload(self):
        if self.cache_task:
            self.cache_task.cancel()
            try:
                await self.cache_task
            except asyncio.CancelledError:
                pass
        await self.db.close()  # データベース接続を閉じる

    async def cache_updater(self):
        """共通辞書を読み込み、以降は一定間隔で全辞書を読み直す(通知漏れ対策)。"""
        first = True
        while True:
            try:
                await self._load_global()
                if not first:
                    self.server_dict_cache.clear()
                    self.user_dict_cache.clear()
                self._invalidate_replacers(lambda key: True)
                first = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"辞書キャッシュ更新エラー: {e}")
            try:
                interval = float(self.tts_config.get("dictionary_refresh_interval", 600))
            except (TypeError, ValueError):
                interval = 600.0
            await asyncio.sleep(max(interval, 10.0))

    async def _load_global(self) -> None:
        self.global_dict_cache = list(await self.db.get_all_global_dictionary())
        self._global_loaded = True

    # --- キャッシュの破棄(DB 更新時・Web からの通知時に呼ぶ) ---------------------------

    def _invalidate_replacers(self, predicate) -> None:
        self._generation += 1
        for key in [k for k in self._replacers if predicate(k)]:
            del self._replacers[key]

    def invalidate_guild(self, guild_id: int) -> None:
        self.server_dict_cache.pop(guild_id, None)
        self._invalidate_replacers(lambda key: key[0] == guild_id)

    def invalidate_user(self, user_id: int) -> None:
        self.user_dict_cache.pop(user_id, None)
        self._invalidate_replacers(lambda key: key[1] == user_id)

    def invalidate_global(self) -> None:
        self.server_dict_cache.clear()
        self.user_dict_cache.clear()
        self._invalidate_replacers(lambda key: True)

    # --- 辞書行の取得(メモリ優先) ---------------------------------------------------

    async def get_server_dict(self, guild_id):
        rows = self.server_dict_cache.get(guild_id)
        if rows is not None:
            return rows
        generation = self._generation
        rows = list(await self.db.get_all_dictionary(guild_id))
        if generation == self._generation:
            self.server_dict_cache[guild_id] = rows
        return rows

    async def get_user_dict(self, user_id):
        rows = self.user_dict_cache.get(user_id)
        if rows is not None:
            return rows
        generation = self._generation
        rows = list(await self.db.get_all_user_dictionary(user_id))
        if generation == self._generation:
            self.user_dict_cache[user_id] = rows
        return rows

    async def get_replacer(self, guild_id: Optional[int], user_id: Optional[int] = None) -> Replacer:
        """ギルド・ユーザーに対応するコンパイル済み置換器を返す(優先度: ユーザー > サーバー > 共通)。"""
        user_rows = await self.get_user_dict(user_id) if user_id is not None else []
        # ユーザー辞書が空なら、ギルド単位の置換器を共有する
        key = (guild_id, user_id if user_rows else None)
        replacer = self._replacers.get(key)
        if replacer is not None:
            self._replacers.move_to_end(key)
            return replacer
        generation = self._generation
        if not self._global_loaded:
            await self._load_global()
        guild_rows = await self.get_server_dict(guild_id) if guild_id is not None else []
        mapping = merge_dictionaries(
            ((r["key"], r["value"]) for r in self.global_dict_cache),
            ((r["key"], r["value"]) for r in guild_rows),
            ((r["key"], r["value"]) for r in user_rows),
        )
        try:
            threshold = int(self.tts_config.get("dictionary_ahocorasick_threshold", 2000))
        except (TypeError, ValueError):
            threshold = 2000
        replacer = build_replacer(mapping, threshold)
        if generation == self._generation:
            self._replacers[key] = replacer
            while len(self._replacers) > self.REPLACER_CACHE_SIZE:
                self._replacers.popitem(last=False)
        return replacer

    # --- 前処理の入口 ---------------------------------------------------------------

    async def preprocess_segments(self, text: str, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                                  ctx: Optional[MessageContext] = None, config: Optional[PipelineConfig] = None):
        replacer = await self.get_replacer(guild_id, user_id)
        return text_pipeline.preprocess_segments(
            text, replacer=replacer, ctx=ctx, config=config or self.pipeline_config()
        )

    async def preprocess(self, text: str, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                         ctx: Optional[MessageContext] = None, config: Optional[PipelineConfig] = None) -> str:
        """辞書適用を含む前処理を行い、1つの文字列を返す。"""
        segments = await self.preprocess_segments(text, guild_id, user_id, ctx, config)
        return "".join(t for t, _ in segments)

    async def is_banned(self, user_id: int) -> bool:
        """ユーザーがBANされているか確認"""
        if self.voice_cog:
            return await self.voice_cog.is_banned(user_id)
        return False

    # 辞書コマンドグループ
    dictionary_group = app_commands.Group(name="dictionary", description="読み上げ辞書の管理")

    @dictionary_group.command(name="add", description="読み上げ辞書を設定 (サーバーまたはユーザー)")
    @app_commands.describe(user_dict="ユーザー辞書を使用するかどうか")
    async def dictionary_add(self, interaction: discord.Interaction, key: str, value: str, user_dict: bool = False):
        if await self.is_banned(interaction.user.id):
            await interaction.response.send_message("あなたはbotからBANされています。", ephemeral=True)
            return
        try:
            if user_dict:
                await self.db.upsert_user_dictionary(interaction.user.id, key, value)
                # キャッシュを即時反映
                self.invalidate_user(interaction.user.id)
                embed = discord.Embed(
                    title="ユーザー辞書更新",
                    description=f"ユーザー辞書に追加しました: **{key}** -> **{value}**",
                    color=discord.Color.green()
                )
            else:
                author_id = interaction.user.id  # 登録者のユーザーIDを取得
                guild_id = interaction.guild.id
                await self.db.upsert_dictionary(guild_id, key, value, author_id)
                # キャッシュを即時反映
                self.invalidate_guild(guild_id)
                embed = discord.Embed(
                    title="辞書更新",
                    description=f"辞書に追加しました: **{key}** -> **{value}**",
                    color=discord.Color.green()
                )
            await interaction.response.send_message(embed=embed, ephemeral=False)
        except Exception as e:
            embed = discord.Embed(
                title="エラー",
                description="エラーが発生しました。詳細は管理者にお問い合わせください。",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @dictionary_group.command(name="remove", description="読み上げ辞書を削除 (サーバーまたはユーザー)")
    @app_commands.describe(user_dict="ユーザー辞書を使用するかどうか")
    async def dictionary_remove(self, interaction: discord.Interaction, key: str, user_dict: bool = False):
        if await self.is_banned(interaction.user.id):
            await interaction.response.send_message("あなたはbotからBANされています。", ephemeral=True)
            return
        try:
            if user_dict:
                result = await self.db.remove_user_dictionary(interaction.user.id, key)
                # キャッシュを即時反映
                self.invalidate_user(interaction.user.id)
                title = "ユーザー辞書削除"
            else:
                guild_id = interaction.guild.id
                result = await self.db.remove_dictionary(guild_id, key)
                # キャッシュを即時反映
                self.invalidate_guild(guild_id)
                title = "辞書削除"
            if result == "DELETE 1":
                embed = discord.Embed(
                    title=title,
                    description=f"辞書から削除しました: **{key}**",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="エラー",
                    description=f"指定されたキーが見つかりません: **{key}**",
                    color=discord.Color.red()
                )
            await interaction.response.send_message(embed=embed, ephemeral=False)
        except Exception as e:
            embed = discord.Embed(
                title="エラー",
                description="エラーが発生しました。詳細は管理者にお問い合わせください。",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @dictionary_group.command(name="search", description="読み上げ辞書を検索 (サーバーまたはユーザー)")
    @app_commands.describe(user_dict="ユーザー辞書を使用するかどうか")
    async def dictionary_search(self, interaction: discord.Interaction, key: str, user_dict: bool = False):
        if await self.is_banned(interaction.user.id):
            await interaction.response.send_message("あなたはbotからBANされています。", ephemeral=True)
            return
        try:
            if user_dict:
                row = await self.db.get_user_dictionary_entry(interaction.user.id, key)
                title = "ユーザー辞書検索結果"
            else:
                guild_id = interaction.guild.id
                row = await self.db.get_dictionary_entry(guild_id, key)
                title = "辞書検索結果"
            if row:
                description = f"**{key}** -> **{row['value']}**"
                embed = discord.Embed(
                    title=title,
                    description=description,
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="エラー",
                    description=f"指定されたキーが見つかりません: **{key}**",
                    color=discord.Color.red()
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = discord.Embed(
                title="エラー",
                description="エラーが発生しました。詳細は管理者にお問い合わせください。",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @dictionary_group.command(name="list", description="読み上げ辞書一覧を表示 (サーバーまたはユーザー)")
    @app_commands.describe(user_dict="ユーザー辞書を使用するかどうか")
    async def dictionary_list(self, interaction: discord.Interaction, user_dict: bool = False):
        if await self.is_banned(interaction.user.id):
            await interaction.response.send_message("あなたはbotからBANされています。", ephemeral=True)
            return
        try:
            if user_dict:
                rows = await self.get_user_dict(interaction.user.id)
                title = "📖 ユーザー辞書一覧"
                empty_description = "あなたのユーザー辞書にはまだ辞書が登録されていません。\n`/dictionary add user_dict:True` コマンドで新しい単語を追加できます！"
            else:
                guild_id = interaction.guild.id
                rows = await self.get_server_dict(guild_id)
                title = "📖 サーバー辞書一覧"
                empty_description = "このサーバーにはまだ辞書が登録されていません。\n`/dictionary add` コマンドで新しい単語を追加できます！"
            if not rows:
                embed = discord.Embed(
                    title=title,
                    description=empty_description,
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # ページネーション設定
            PAGE_SIZE = 20
            pages = [rows[i:i+PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]

            def make_embed(page_idx):
                embed = discord.Embed(
                    title=title,
                    description=f"ページ {page_idx+1}/{len(pages)}\n",
                    color=discord.Color.green()
                )
                for i, row in enumerate(pages[page_idx], start=1 + page_idx * PAGE_SIZE):
                    embed.add_field(
                        name=f"{i}. `{row['key']}` → `{row['value']}`",
                        value="",
                        inline=False
                    )
                return embed

            class PaginationView(View):
                def __init__(self):
                    super().__init__(timeout=120)
                    self.page = 0
                    self.prev_button = Button(label="◀ 前へ", style=discord.ButtonStyle.secondary)
                    self.next_button = Button(label="次へ ▶", style=discord.ButtonStyle.secondary)
                    self.prev_button.callback = self.prev
                    self.next_button.callback = self.next
                    self.add_item(self.prev_button)
                    self.add_item(self.next_button)

                async def update(self, interaction):
                    embed = make_embed(self.page)
                    await interaction.response.edit_message(embed=embed, view=self)

                async def prev(self, interaction: discord.Interaction):
                    if self.page > 0:
                        self.page -= 1
                        await self.update(interaction)
                    else:
                        await interaction.response.defer()

                async def next(self, interaction: discord.Interaction):
                    if self.page < len(pages) - 1:
                        self.page += 1
                        await self.update(interaction)
                    else:
                        await interaction.response.defer()

            view = PaginationView() if len(pages) > 1 else None
            if view:
                await interaction.response.send_message(embed=make_embed(0), view=view, ephemeral=True)
            else:
                await interaction.response.send_message(embed=make_embed(0), ephemeral=True)
        except Exception as e:
            self.logger.exception(f"辞書一覧の表示に失敗: {e}")
            embed = discord.Embed(
                title="エラー",
                description="エラーが発生しました。詳細は管理者にお問い合わせください。",
                color=discord.Color.red()
            )
            # どちらでも送信できるように両方例外処理
            try:
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=embed, view=None)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception as inner_e:
                self.logger.error(f"エラー通知の送信に失敗: {inner_e}")


async def setup(bot):
    await bot.add_cog(DictionaryCog(bot))
