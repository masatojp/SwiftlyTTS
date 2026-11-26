import unittest

from lib.dictionary_replacer import RegexReplacer, build_replacer, merge_dictionaries
from lib.text_pipeline import (
    MessageContext,
    PipelineConfig,
    compress_repeats,
    convert_markup,
    limit_length,
    preprocess,
    preprocess_segments,
    preprocess_to_sentences,
    split_sentences,
)


def replacer(*pairs):
    return build_replacer(merge_dictionaries(pairs))


class NormalizeTest(unittest.TestCase):
    def test_nfkc(self):
        self.assertEqual(preprocess("ＡＢＣ１２３ﾃｽﾄ"), "ABC123テスト")

    def test_dictionary_key_matches_after_nfkc(self):
        # 本文が全角でも、半角キーの辞書に一致する
        self.assertEqual(preprocess("ＶＣ来て", replacer=replacer(("VC", "ブイシー"))), "ブイシー来て")


class MarkupTest(unittest.TestCase):
    cfg = PipelineConfig()

    def test_user_mention(self):
        ctx = MessageContext(users={123: "まさと"})
        self.assertEqual(convert_markup("<@123> と <@!123>", ctx, self.cfg), "まさと と まさと")

    def test_unknown_mention_fallback(self):
        self.assertEqual(convert_markup("<@999>", None, self.cfg), "ユーザー")

    def test_role_and_channel(self):
        ctx = MessageContext(roles={1: "管理者"}, channels={2: "雑談"})
        self.assertEqual(convert_markup("<@&1>は<#2>へ", ctx, self.cfg), "管理者は雑談へ")

    def test_custom_emoji_name(self):
        self.assertEqual(convert_markup("いいね<:thumbs_up:12345><a:party:678>", None, self.cfg), "いいねthumbs upparty")

    def test_custom_emoji_skip(self):
        cfg = PipelineConfig(custom_emoji="skip")
        self.assertEqual(convert_markup("いいね<:good:12345>", None, cfg), "いいね")

    def test_url(self):
        self.assertEqual(
            convert_markup("見て https://example.com/a?b=c です", None, self.cfg),
            "見て URL省略 です",
        )

    def test_url_text_configurable(self):
        cfg = PipelineConfig(url_text="リンク")
        self.assertEqual(convert_markup("http://x.y", None, cfg), "リンク")

    def test_display_name_goes_through_dictionary(self):
        ctx = MessageContext(users={1: "masato"})
        self.assertEqual(preprocess("<@1>おはよう", replacer=replacer(("masato", "まさと")), ctx=ctx), "マサトおはよう")

    def test_display_name_is_normalized(self):
        ctx = MessageContext(users={1: "ＭＡＳＡＴＯ"})
        self.assertEqual(convert_markup("<@1>", ctx, self.cfg), "MASATO")


class ProtectionTest(unittest.TestCase):
    def test_dictionary_output_is_not_compressed(self):
        r = replacer(("ww", "ワーーーーイ"))
        self.assertEqual(preprocess("ww ああああああ", replacer=r), "ワーーーーイ あああ")

    def test_dictionary_output_not_passed_to_word_converter(self):
        calls = []

        def conv(text):
            calls.append(text)
            return text.upper()

        segs = preprocess_segments("abc xyz", replacer=replacer(("xyz", "xyz-reading")), word_converter=conv)
        self.assertEqual(segs, [("ABC ", False), ("xyz-reading", True)])
        self.assertEqual(calls, ["abc "])

    def test_dictionary_output_not_rereplaced(self):
        r = replacer(("A", "B"), ("B", "C"))
        self.assertEqual(preprocess("AB", replacer=r), "BC")

    def test_dictionary_longest_match_in_pipeline(self):
        r = replacer(("東京", "とーきょー"), ("東京タワー", "とーきょーたわー"))
        self.assertEqual(preprocess("東京タワーと東京", replacer=r), "トーキョータワーとトーキョー")

    def test_url_inside_is_not_dictionary_replaced(self):
        # URL は辞書より先に置換されるので、URL内の文字列に辞書が当たらない
        r = replacer(("example", "エグザンプル"))
        self.assertEqual(preprocess("https://example.com", replacer=r), "URL省略")


class CompressTest(unittest.TestCase):
    def test_compress(self):
        self.assertEqual(compress_repeats("あああああ", 3), "あああ")
        self.assertEqual(compress_repeats("wwwwwwww", 3), "www")
        self.assertEqual(compress_repeats("ああ", 3), "ああ")
        self.assertEqual(compress_repeats("!!!!!\n\n\n\n", 2), "!!\n\n")

    def test_disabled(self):
        self.assertEqual(compress_repeats("あああああ", 0), "あああああ")


class LimitTest(unittest.TestCase):
    def test_under_limit(self):
        self.assertEqual(preprocess("あいう", config=PipelineConfig(max_length=3)), "あいう")

    def test_over_limit(self):
        self.assertEqual(preprocess("あいうえお", config=PipelineConfig(max_length=3)), "あいう以下略")

    def test_omission_text_configurable(self):
        cfg = PipelineConfig(max_length=2, omission_text="省略")
        self.assertEqual(preprocess("あいう", config=cfg), "あい省略")

    def test_protected_segment_not_cut(self):
        segs = [("あい", False), ("トーキョー", True), ("うえ", False)]
        self.assertEqual(limit_length(segs, 4, "以下略"), [("あい", False), ("以下略", True)])

    def test_limit_counts_after_dictionary(self):
        r = replacer(("x", "エックス"))
        self.assertEqual(preprocess("xx", replacer=r, config=PipelineConfig(max_length=5)), "エックス以下略")

    def test_unlimited(self):
        self.assertEqual(preprocess("あ" * 10, config=PipelineConfig(max_length=0, repeat_max=0)), "あ" * 10)


class SentenceSplitTest(unittest.TestCase):
    def test_split_on_punctuation(self):
        self.assertEqual(
            split_sentences("こんにちは。元気?うん!ありがとう"),
            ["こんにちは。", "元気?", "うん!", "ありがとう"],
        )

    def test_split_on_newline_and_drop_unreadable(self):
        self.assertEqual(split_sentences("一行目\n\n二行目\n...\n"), ["一行目", "二行目"])

    def test_consecutive_marks_stay_together(self):
        self.assertEqual(split_sentences("えっ!?本当。。"), ["えっ!?", "本当。。"])

    def test_long_sentence_split_at_comma(self):
        text = "あ" * 30 + "、" + "い" * 30
        self.assertEqual(split_sentences(text, soft_max=40), ["あ" * 30 + "、", "い" * 30])

    def test_long_sentence_without_break_kept(self):
        text = "あ" * 60
        self.assertEqual(split_sentences(text, soft_max=40), [text])

    def test_pipeline_to_sentences(self):
        cfg = PipelineConfig(max_length=12)
        self.assertEqual(preprocess_to_sentences("おはよう。今日はいい天気ですね", config=cfg), ["おはよう。", "今日はいい天気以下略"])


class ConfigTest(unittest.TestCase):
    def test_from_mapping(self):
        cfg = PipelineConfig.from_mapping({"max_length": "120", "custom_emoji": "skip", "unknown": 1})
        self.assertEqual(cfg.max_length, 120)
        self.assertEqual(cfg.custom_emoji, "skip")

    def test_from_none(self):
        self.assertEqual(PipelineConfig.from_mapping(None), PipelineConfig())

    def test_invalid_value_falls_back(self):
        self.assertEqual(PipelineConfig.from_mapping({"max_length": "abc"}).max_length, 150)


if __name__ == "__main__":
    unittest.main()
