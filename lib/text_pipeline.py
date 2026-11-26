"""読み上げ前処理パイプライン(純粋関数)。

    受信テキスト
     → NFKC正規化
     → メンション/チャンネル/ロール/カスタム絵文字/URL の変換
     → 辞書の置換(1パス・最長一致)   ← 出力は保護され、以降の処理を受けない
     → 保護されていない部分のみ: 英単語の変換(差し込み口)、連続文字の圧縮
     → 文字数制限(超過分は「以下略」)
     → 文分割

読み(漢字の読み・アクセント・英単語の読み)の判定は VOICEVOX Engine に任せる。
このモジュールは discord / DB / HTTP に依存しない。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from lib.dictionary_replacer import NullReplacer, Replacer, Segment


@dataclass(frozen=True)
class PipelineConfig:
    # 読み上げる最大文字数(辞書置換後の文字数で数える)。0以下で無制限
    max_length: int = 150
    # 文字数超過時に末尾へ付ける文言
    omission_text: str = "以下略"
    # URL の置換先
    url_text: str = "URL省略"
    # カスタム絵文字: "name" = 名前を読む / "skip" = 読まない
    custom_emoji: str = "name"
    # 同じ文字がこの回数を超えて連続したら、この回数まで縮める。0以下で無効
    repeat_max: int = 3
    # 1文がこの文字数を超えたら読点などでさらに分割する。0以下で無効
    sentence_soft_max: int = 50

    @classmethod
    def from_mapping(cls, data: Optional[Mapping]) -> "PipelineConfig":
        """config.yml の tts セクションから生成する(未知のキーは無視)。"""
        if not data:
            return cls()
        kwargs = {}
        for name, f in cls.__dataclass_fields__.items():
            if name in data and data[name] is not None:
                try:
                    kwargs[name] = type(f.default)(data[name])
                except (TypeError, ValueError):
                    pass
        return cls(**kwargs)


@dataclass(frozen=True)
class MessageContext:
    """メッセージに含まれるメンションの表示名(Discord から取得済みのもの)。"""

    users: Mapping[int, str] = field(default_factory=dict)
    roles: Mapping[int, str] = field(default_factory=dict)
    channels: Mapping[int, str] = field(default_factory=dict)


# 英単語の変換器(差し込み口)。既定では何もしない(Engine 0.26 が英単語を読めるため)
WordConverter = Callable[[str], str]


def _identity(text: str) -> str:
    return text


# --- 1. 正規化 -----------------------------------------------------------------

def normalize(text: str) -> str:
    """NFKC 正規化(全角英数→半角、半角カナ→全角 など)。"""
    return unicodedata.normalize("NFKC", text)


# --- 2. Discord 記法 / URL -------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+")
_MARKUP_RE = re.compile(
    r"<@!?(?P<user>\d+)>"
    r"|<@&(?P<role>\d+)>"
    r"|<#(?P<channel>\d+)>"
    r"|<a?:(?P<emoji>\w+):\d+>"
)


def convert_markup(text: str, ctx: Optional[MessageContext], config: PipelineConfig) -> str:
    """メンション・チャンネル・ロールを表示名に、カスタム絵文字と URL を置換する。"""
    ctx = ctx or MessageContext()
    text = _URL_RE.sub(config.url_text, text)

    def repl(m: re.Match) -> str:
        if m.group("user"):
            name = ctx.users.get(int(m.group("user")))
            return normalize(name) if name else "ユーザー"
        if m.group("role"):
            name = ctx.roles.get(int(m.group("role")))
            return normalize(name) if name else "ロール"
        if m.group("channel"):
            name = ctx.channels.get(int(m.group("channel")))
            return normalize(name) if name else "チャンネル"
        # カスタム絵文字
        if config.custom_emoji == "skip":
            return ""
        return m.group("emoji").replace("_", " ")

    return _MARKUP_RE.sub(repl, text)


# --- 3〜4. 保護されていない部分の加工 ---------------------------------------------

def compress_repeats(text: str, repeat_max: int) -> str:
    """同じ文字の連続を repeat_max 回までに縮める(「あああああ」→「あああ」)。"""
    if repeat_max <= 0:
        return text
    return re.sub(r"(.)\1{%d,}" % repeat_max, lambda m: m.group(1) * repeat_max, text, flags=re.S)


def process_unprotected(
    segments: list[Segment],
    config: PipelineConfig,
    word_converter: WordConverter = _identity,
) -> list[Segment]:
    """保護されていない区間だけに後段の加工を適用する。"""
    out: list[Segment] = []
    for text, protected in segments:
        if not protected:
            text = word_converter(text)
            text = compress_repeats(text, config.repeat_max)
        if text:
            out.append((text, protected))
    return out


# --- 5. 文字数制限 ------------------------------------------------------------------

def limit_length(segments: list[Segment], max_length: int, omission_text: str) -> list[Segment]:
    """合計文字数を max_length までに制限し、超過したら omission_text を付ける。

    保護された区間(辞書の読み)は途中で切らない。収まらない場合はその区間ごと省く。
    """
    if max_length <= 0:
        return segments
    out: list[Segment] = []
    used = 0
    for text, protected in segments:
        remaining = max_length - used
        if len(text) <= remaining:
            out.append((text, protected))
            used += len(text)
            continue
        if not protected and remaining > 0:
            out.append((text[:remaining], False))
        out.append((omission_text, True))
        return out
    return out


# --- 6. 文分割 ----------------------------------------------------------------------

# 文末: 句点・感嘆符・疑問符(連続も含む)、改行
_SENTENCE_RE = re.compile(r"[^。！？!?\n]*(?:[。！？!?]+|\n+|$)")
# 長い文の中での分割候補(この文字の直後で切る)
_SOFT_BREAK_CHARS = "、,，) 　」』"
# 読める文字(かな・漢字・英数字)を1つでも含むか
_READABLE_RE = re.compile(r"[^\W_]")


def _split_long(sentence: str, soft_max: int) -> list[str]:
    if soft_max <= 0 or len(sentence) <= soft_max:
        return [sentence]
    parts: list[str] = []
    rest = sentence
    while len(rest) > soft_max:
        window = rest[:soft_max]
        cut = max(window.rfind(ch) for ch in _SOFT_BREAK_CHARS)
        if cut < soft_max // 3:
            # 区切りが見つからない(または前すぎる)場合は分割しない
            break
        parts.append(rest[:cut + 1])
        rest = rest[cut + 1:]
    parts.append(rest)
    return parts


def split_sentences(text: str, soft_max: int = 0) -> list[str]:
    """句点・感嘆符・疑問符・改行で文に分ける。読める文字のない断片は除く。"""
    chunks: list[str] = []
    for m in _SENTENCE_RE.finditer(text):
        sentence = m.group(0).strip()
        if not sentence:
            continue
        for part in _split_long(sentence, soft_max):
            part = part.strip()
            if part and _READABLE_RE.search(part):
                chunks.append(part)
    return chunks


# --- まとめ -----------------------------------------------------------------------

def preprocess_segments(
    text: str,
    *,
    replacer: Replacer = NullReplacer(),
    ctx: Optional[MessageContext] = None,
    config: PipelineConfig = PipelineConfig(),
    word_converter: WordConverter = _identity,
) -> list[Segment]:
    """文分割の手前までを行い、[(文字列, 保護フラグ)] を返す。"""
    text = normalize(text)
    text = convert_markup(text, ctx, config)
    segments = replacer.replace(text)
    segments = process_unprotected(segments, config, word_converter)
    return limit_length(segments, config.max_length, config.omission_text)


def preprocess(text: str, **kwargs) -> str:
    """前処理済みの1つの文字列を返す。"""
    return "".join(t for t, _ in preprocess_segments(text, **kwargs))


def preprocess_to_sentences(text: str, **kwargs) -> list[str]:
    """前処理を行い、合成単位(文)のリストを返す。"""
    config = kwargs.get("config", PipelineConfig())
    return split_sentences(preprocess(text, **kwargs), config.sentence_soft_max)
