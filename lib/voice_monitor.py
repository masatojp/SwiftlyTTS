"""VC の接続状況と再生結果を調べる(ログ・監視用)。discord.py の内部属性は getattr で安全に読む。"""

from __future__ import annotations

import array
import io
import math
import sys
import wave
from typing import Any, Optional

# これより小さい最大振幅は「無音」とみなす(dBFS)
SILENCE_DBFS = -50.0
FRAME_SEC = 0.02  # Discord の音声パケット1つ分(20ms)


def wav_peak_dbfs(wav_bytes: bytes) -> Optional[float]:
    """16bit wav の最大振幅を dBFS で返す(無音なら -inf)。読めない形式なら None。"""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            if w.getsampwidth() != 2:
                return None
            frames = w.readframes(w.getnframes())
    except Exception:
        return None
    samples = array.array("h", frames[: len(frames) - len(frames) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return float("-inf")
    peak = max(abs(max(samples)), abs(min(samples)))
    return 20 * math.log10(peak / 32768) if peak else float("-inf")


def wav_format(wav_bytes: bytes) -> str:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            return f"{w.getframerate()}Hz/{w.getnchannels()}ch/{w.getsampwidth() * 8}bit"
    except Exception:
        return "不明"


def voice_status(voice_client: Any, guild: Any = None) -> dict:
    """VC の状態をまとめる。"""
    conn = getattr(voice_client, "_connection", None)
    me_voice = getattr(getattr(guild, "me", None), "voice", None) if guild is not None else None
    channel = getattr(voice_client, "channel", None)
    try:
        latency_ms = round(voice_client.latency * 1000)
    except Exception:
        latency_ms = None
    if latency_ms is not None and not math.isfinite(latency_ms):
        latency_ms = None
    members = getattr(channel, "members", None) or []
    return {
        "connected": bool(voice_client.is_connected()) if voice_client else False,
        "channel": f"{getattr(channel, 'name', '?')}({getattr(channel, 'id', '?')})",
        "listeners": sum(1 for m in members if not getattr(m, "bot", False)),
        "latency_ms": latency_ms,
        "mode": getattr(voice_client, "mode", None),
        "dave_version": getattr(conn, "dave_protocol_version", None),
        "dave_ready": getattr(conn, "can_encrypt", None),
        "server_mute": getattr(me_voice, "mute", None),
        "suppress": getattr(me_voice, "suppress", None),
        "self_mute": getattr(me_voice, "self_mute", None),
    }


def status_problems(status: dict) -> list[str]:
    """音が届かない原因になりうる状態を列挙する。"""
    problems = []
    if not status.get("connected"):
        problems.append("VCに接続していない")
    if status.get("server_mute"):
        problems.append("Botがサーバーミュートされている")
    if status.get("suppress"):
        problems.append("ステージで発言権がない(suppress)")
    if status.get("self_mute"):
        problems.append("Botが自分をミュートしている")
    if status.get("dave_version") and status.get("dave_ready") is False:
        problems.append("DAVE(E2EE)の準備ができていない")
    if status.get("latency_ms") is not None and status["latency_ms"] > 500:
        problems.append(f"音声サーバーとの遅延が大きい({status['latency_ms']}ms)")
    return problems


def format_status(status: dict) -> str:
    dave = status.get("dave_version")
    dave_text = "なし" if not dave else f"v{dave}({'準備完了' if status.get('dave_ready') else '準備中'})"
    latency = status.get("latency_ms")
    return (
        f"connected={status.get('connected')} channel={status.get('channel')} 聞き手={status.get('listeners')}人 "
        f"遅延={'?' if latency is None else f'{latency}ms'} DAVE={dave_text} mode={status.get('mode')} "
        f"サーバーミュート={status.get('server_mute')} suppress={status.get('suppress')}"
    )


def sent_packets(before: Optional[int], after: Optional[int]) -> Optional[int]:
    """VoiceClient.sequence の差分から送信パケット数を求める(65535 で一周する)。"""
    if before is None or after is None:
        return None
    return (after - before) % 65536
