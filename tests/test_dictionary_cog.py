"""DictionaryCog のキャッシュ・破棄・優先順位のテスト(DB はフェイク)。"""

import types
import unittest

try:
    from cogs.voice.dictionary import DictionaryCog
    HAS_DISCORD = True
except ImportError:  # discord.py 未インストール環境
    HAS_DISCORD = False


class FakeDB:
    def __init__(self):
        self.global_rows = []
        self.guild_rows = {}
        self.user_rows = {}
        self.calls = []

    async def get_all_global_dictionary(self):
        self.calls.append("global")
        return list(self.global_rows)

    async def get_all_dictionary(self, guild_id):
        self.calls.append(("guild", guild_id))
        return list(self.guild_rows.get(guild_id, []))

    async def get_all_user_dictionary(self, user_id):
        self.calls.append(("user", user_id))
        return list(self.user_rows.get(user_id, []))


def row(key, value):
    return {"key": key, "value": value}


@unittest.skipUnless(HAS_DISCORD, "discord.py が未インストール")
class DictionaryCogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot = types.SimpleNamespace(config={"tts": {"max_length": 0}})
        self.cog = DictionaryCog(bot)
        self.db = FakeDB()
        self.cog.db = self.db

    async def test_priority_user_over_guild_over_global(self):
        self.db.global_rows = [row("w", "わら"), row("草", "くさ"), row("鯖", "さば")]
        self.db.guild_rows = {1: [row("w", "だぶりゅー"), row("草", "そう")]}
        self.db.user_rows = {10: [row("草", "ワラ")]}
        self.assertEqual(await self.cog.preprocess("w草鯖", 1, 10), "ダブリューワラサバ")
        self.assertEqual(await self.cog.preprocess("w草鯖", 1, 11), "ダブリューソウサバ")
        self.assertEqual(await self.cog.preprocess("w草鯖", 2, None), "ワラクササバ")

    async def test_db_is_not_queried_per_message(self):
        self.db.guild_rows = {1: [row("鯖", "さば")]}
        for _ in range(5):
            await self.cog.preprocess("鯖", 1, 10)
        self.assertEqual(self.db.calls.count(("guild", 1)), 1)
        self.assertEqual(self.db.calls.count(("user", 10)), 1)
        self.assertEqual(self.db.calls.count("global"), 1)

    async def test_invalidate_guild_rebuilds(self):
        self.db.guild_rows = {1: [row("鯖", "さば")]}
        self.assertEqual(await self.cog.preprocess("鯖", 1), "サバ")
        self.db.guild_rows = {1: [row("鯖", "サーバー")]}
        self.assertEqual(await self.cog.preprocess("鯖", 1), "サバ")  # まだキャッシュ
        self.cog.invalidate_guild(1)
        self.assertEqual(await self.cog.preprocess("鯖", 1), "サーバー")

    async def test_invalidate_user_rebuilds(self):
        self.db.user_rows = {10: [row("鯖", "さば")]}
        self.assertEqual(await self.cog.preprocess("鯖", 1, 10), "サバ")
        self.db.user_rows = {10: []}
        self.cog.invalidate_user(10)
        self.assertEqual(await self.cog.preprocess("鯖", 1, 10), "鯖")

    async def test_invalidate_guild_does_not_affect_other_guild(self):
        self.db.guild_rows = {1: [row("a", "エー")], 2: [row("a", "アー")]}
        await self.cog.preprocess("a", 1)
        await self.cog.preprocess("a", 2)
        self.cog.invalidate_guild(1)
        self.assertIn((2, None), self.cog._replacers)
        self.assertNotIn((1, None), self.cog._replacers)


if __name__ == "__main__":
    unittest.main()
