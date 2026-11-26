import unittest

from lib.dictionary_replacer import (
    AhoCorasickReplacer,
    NullReplacer,
    RegexReplacer,
    build_replacer,
    hiragana_to_katakana,
    merge_dictionaries,
    normalize_reading,
)

try:
    import ahocorasick  # noqa: F401
    HAS_AHOCORASICK = True
except ImportError:
    HAS_AHOCORASICK = False


class ReplacerContract:
    """RegexReplacer / AhoCorasickReplacer に共通の振る舞い。"""

    impl = None

    def make(self, mapping):
        return self.impl(mapping)

    def test_longest_match_wins(self):
        r = self.make({"東京": "トーキョー", "東京タワー": "トーキョータワー"})
        self.assertEqual(
            r.replace("東京タワーと東京"),
            [("トーキョータワー", True), ("と", False), ("トーキョー", True)],
        )

    def test_single_pass_no_rereplace(self):
        # 置換結果(B)が、さらに B→C で置換されてはいけない
        r = self.make({"A": "B", "B": "C"})
        self.assertEqual(r.replace("AB"), [("B", True), ("C", True)])

    def test_replacement_containing_other_key(self):
        r = self.make({"ねこ": "ネコチャン", "チャン": "サン"})
        self.assertEqual(r.replace("ねこだ"), [("ネコチャン", True), ("だ", False)])

    def test_no_match(self):
        r = self.make({"東京": "トーキョー"})
        self.assertEqual(r.replace("大阪"), [("大阪", False)])

    def test_regex_metacharacters_are_literal(self):
        r = self.make({"a.b": "エービー", "(笑)": "ワラ"})
        self.assertEqual(r.replace("axb a.b(笑)"), [("axb ", False), ("エービー", True), ("ワラ", True)])

    def test_empty_text(self):
        r = self.make({"東京": "トーキョー"})
        self.assertEqual(r.replace(""), [])


class RegexReplacerTest(ReplacerContract, unittest.TestCase):
    impl = RegexReplacer


@unittest.skipUnless(HAS_AHOCORASICK, "pyahocorasick が未インストール")
class AhoCorasickReplacerTest(ReplacerContract, unittest.TestCase):
    impl = AhoCorasickReplacer


class BuildReplacerTest(unittest.TestCase):
    def test_empty_mapping(self):
        self.assertIsInstance(build_replacer({}), NullReplacer)
        self.assertEqual(build_replacer({}).replace("あ"), [("あ", False)])

    def test_small_mapping_uses_regex(self):
        self.assertIsInstance(build_replacer({"a": "エー"}, ahocorasick_threshold=10), RegexReplacer)

    @unittest.skipUnless(HAS_AHOCORASICK, "pyahocorasick が未インストール")
    def test_large_mapping_uses_ahocorasick(self):
        mapping = {f"語{i}": "ゴ" for i in range(20)}
        self.assertIsInstance(build_replacer(mapping, ahocorasick_threshold=10), AhoCorasickReplacer)


class ReadingNormalizationTest(unittest.TestCase):
    def test_hiragana_to_katakana(self):
        self.assertEqual(hiragana_to_katakana("まさと ゔぁ ゝ"), "マサト ヴァ ヽ")

    def test_kana_only_value_becomes_katakana(self):
        self.assertEqual(normalize_reading("まさと"), "マサト")
        self.assertEqual(normalize_reading("はせべ まさと"), "ハセベ マサト")
        self.assertEqual(normalize_reading("ずんだもーん！"), "ズンダモーン!")

    def test_value_with_kanji_is_kept(self):
        # 「食べ物」を「食ベ物」にするとショクベブツと誤読されるため変換しない
        self.assertEqual(normalize_reading("食べ物"), "食べ物")
        self.assertEqual(normalize_reading("ABCさん"), "ABCさん")

    def test_value_is_nfkc_normalized(self):
        self.assertEqual(normalize_reading("ﾏｻﾄ"), "マサト")


class MergeDictionariesTest(unittest.TestCase):
    def test_priority_user_over_guild_over_global(self):
        merged = merge_dictionaries(
            [("w", "わら"), ("草", "くさ")],   # 共通
            [("w", "ダブリュー")],              # ギルド
            [("草", "ワラ")],                   # ユーザー
        )
        self.assertEqual(merged, {"w": "ダブリュー", "草": "ワラ"})

    def test_keys_are_nfkc_normalized(self):
        merged = merge_dictionaries([("ＡＢＣ", "えーびーしー")])
        self.assertEqual(merged, {"ABC": "エービーシー"})

    def test_empty_key_ignored(self):
        self.assertEqual(merge_dictionaries([("", "あ"), (None, "い")]), {})


if __name__ == "__main__":
    unittest.main()
