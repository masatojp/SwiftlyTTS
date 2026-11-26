"""読み上げ辞書の置換器。

- 長い語を優先する最長一致で、1パスで置換する（置換結果が再置換されることはない）。
- 戻り値は ``[(文字列, 保護フラグ)]``。辞書で置換した部分は ``True`` (保護) になり、
  後段の前処理(連続文字の圧縮など)の対象外になる。
- 実装は差し替え可能。通常は ``re`` の選択(|)を使い、語数が多いときは
  pyahocorasick(任意の依存)が入っていれば Aho-Corasick を使う。

このモジュールは discord や DB に依存しない純粋なロジックのみを持つ。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Mapping, Protocol

Segment = tuple[str, bool]

# ひらがな→カタカナ変換の対象(ぁ-ゖ, ゝゞ)。ゟ(より) は対象外。
_HIRA_START = 0x3041
_HIRA_END = 0x3096
# 「読み」とみなす文字: かな・長音・中点・空白・読点・句点など(漢字や英数字を含まない)
_KANA_ONLY_RE = re.compile(r"^[ぁ-ゖゝゞァ-ヺー-ヾ・ー、。！？!?\s]+$")


def hiragana_to_katakana(text: str) -> str:
    """ひらがなをカタカナに変換する(それ以外の文字はそのまま)。"""
    out = []
    for ch in text:
        code = ord(ch)
        if _HIRA_START <= code <= _HIRA_END or ch in "ゝゞ":
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def normalize_key(key: str) -> str:
    """辞書キーを本文と同じ規則(NFKC)で正規化する。"""
    return unicodedata.normalize("NFKC", key)


def normalize_reading(value: str) -> str:
    """辞書の読みを Engine に送る形へ整える。

    読みが かな だけで書かれている場合はカタカナに統一する。ひらがなのままだと
    前後の助詞と一続きに解析され、読みが崩れることがあるため
    (例: 「まさとと遊ぶ」→ マサ/トト)。
    漢字などを含む値は変換しない(「食べ物」を「食ベ物」にすると誤読するため)。
    """
    value = unicodedata.normalize("NFKC", value)
    if _KANA_ONLY_RE.match(value):
        return hiragana_to_katakana(value)
    return value


class Replacer(Protocol):
    def replace(self, text: str) -> list[Segment]:
        ...


def _append(segments: list[Segment], text: str, protected: bool) -> None:
    if not text:
        return
    if segments and segments[-1][1] == protected and not protected:
        # 未保護の隣接区間は結合しておく(後段の処理を簡単にするため)
        segments[-1] = (segments[-1][0] + text, False)
    else:
        segments.append((text, protected))


class NullReplacer:
    """辞書が空のときの置換器。"""

    def replace(self, text: str) -> list[Segment]:
        return [(text, False)] if text else []


class RegexReplacer:
    """re の選択(|)による置換器。

    Python の re は左から順に選択肢を試すため、キーを長い順に並べれば
    各位置で最長一致になる。
    """

    def __init__(self, mapping: Mapping[str, str]):
        self._mapping = dict(mapping)
        keys = sorted(self._mapping, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(k) for k in keys))

    def replace(self, text: str) -> list[Segment]:
        segments: list[Segment] = []
        pos = 0
        for m in self._pattern.finditer(text):
            _append(segments, text[pos:m.start()], False)
            _append(segments, self._mapping[m.group(0)], True)
            pos = m.end()
        _append(segments, text[pos:], False)
        return segments


class AhoCorasickReplacer:
    """pyahocorasick を使う置換器(語数が多い場合向け)。"""

    def __init__(self, mapping: Mapping[str, str]):
        import ahocorasick  # 任意の依存。未インストールなら ImportError

        self._automaton = ahocorasick.Automaton()
        for key, value in mapping.items():
            self._automaton.add_word(key, (key, value))
        self._automaton.make_automaton()

    def replace(self, text: str) -> list[Segment]:
        segments: list[Segment] = []
        pos = 0
        # iter_long: 左から重ならない最長一致を返す
        for end, (key, value) in self._automaton.iter_long(text):
            start = end - len(key) + 1
            _append(segments, text[pos:start], False)
            _append(segments, value, True)
            pos = end + 1
        _append(segments, text[pos:], False)
        return segments


def merge_dictionaries(*layers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """辞書を優先度の低い順に受け取り、正規化して1つにまとめる。

    同じキーは後の層(優先度が高い)で上書きされる。
    呼び出し例: merge_dictionaries(global_rows, guild_rows, user_rows)
    → ユーザー > ギルド > 共通 の優先順位になる。
    """
    merged: dict[str, str] = {}
    for layer in layers:
        for key, value in layer:
            if key is None or value is None:
                continue
            nkey = normalize_key(str(key))
            if not nkey:
                continue
            merged[nkey] = normalize_reading(str(value))
    return merged


def build_replacer(mapping: Mapping[str, str], ahocorasick_threshold: int = 2000) -> Replacer:
    """語数に応じて置換器を選ぶ。"""
    if not mapping:
        return NullReplacer()
    if ahocorasick_threshold and len(mapping) >= ahocorasick_threshold:
        try:
            return AhoCorasickReplacer(mapping)
        except ImportError:
            pass
    return RegexReplacer(mapping)
