"""VOICEVOX Engine が返す音声に中身があるかを、サンプリングレート別に確かめる。

Bot のコンテナ内で実行する(.env の VOICEVOX_URL の Engine を使う):

    docker compose exec bot python scripts/check_audio.py
    docker compose exec bot python scripts/check_audio.py --text "テストです" --speaker 3

Engine の既定のまま / 48kHz を要求した場合 のそれぞれで、形式・長さ・最大振幅を表示する。
最大振幅が -50dBFS 未満なら無音。あわせて Discord 関連ライブラリのバージョンも表示する。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aiohttp  # noqa: E402

from lib.voice_monitor import SILENCE_DBFS, wav_format, wav_peak_dbfs  # noqa: E402
from lib.VOICEVOXlib import _wav_duration, wav_to_discord_pcm  # noqa: E402


async def synth(session, url, text, speaker, rate):
    async with session.post(f"{url}/audio_query", params={"text": text, "speaker": speaker}) as r:
        r.raise_for_status()
        query = await r.json()
    if rate:
        query["outputSamplingRate"] = rate
        query["outputStereo"] = False
    async with session.post(f"{url}/synthesis", params={"speaker": speaker}, json=query) as r:
        r.raise_for_status()
        return await r.read()


def describe(wav: bytes) -> str:
    peak = wav_peak_dbfs(wav)
    verdict = "?" if peak is None else ("無音!" if peak < SILENCE_DBFS else "音あり")
    peak_text = "?" if peak is None else f"{peak:.1f}dBFS"
    return f"{wav_format(wav)} 長さ={_wav_duration(wav):.2f}s 最大振幅={peak_text} → {verdict}"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("VOICEVOX_URL", "http://localhost:50021").split(",")[0].strip())
    ap.add_argument("--speaker", type=int, default=3)
    ap.add_argument("--text", default="音声のテストです。")
    args = ap.parse_args()

    for name in ("discord", "davey", "nacl"):
        try:
            mod = __import__(name)
            print(f"{name}: {getattr(mod, '__version__', '?')}")
        except ImportError:
            print(f"{name}: 未インストール")
    try:
        import discord.opus
        discord.opus._load_default()
        print(f"libopus: {'読み込み可' if discord.opus.is_loaded() else '読み込めない'}")
    except Exception as e:
        print(f"libopus: 読み込めない ({e})")

    print(f"Engine: {args.url} / speaker={args.speaker} / text={args.text}")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        async with session.get(f"{args.url}/version") as r:
            print(f"Engine version: {await r.text()}")
        for label, rate in (("Engine の既定", 0), ("48kHz を要求", 48000)):
            try:
                wav = await synth(session, args.url, args.text, args.speaker, rate)
            except Exception as e:
                print(f"[{label}] 失敗: {e}")
                continue
            print(f"[{label}] {describe(wav)}")
            if rate:
                pcm = wav_to_discord_pcm(wav)
                print(f"[{label}] ffmpeg なしの再生: {'可' if pcm is not None else '不可(ffmpeg で変換される)'}")


if __name__ == "__main__":
    asyncio.run(main())
