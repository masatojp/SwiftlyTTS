"""読み上げ開始までの待ち時間を、変更前の方式と現在の方式で比較する。

Discord には接続せず、VOICEVOX Engine に実際に合成させて計測する(再生は実時間で模擬)。

    python scripts/bench_latency.py --url http://localhost:50021

- before: 全文を1回で合成 → wav をファイルに保存 → ffmpeg を起動して最初の音声フレームが出るまで
          (変更前の process_queue と同じ流れ。AI 読み仮名変換・キューのポーリング待ちは含まない)
- after : 前処理 → 文分割 → 1文目の合成が終わり次第再生開始、再生中に次の文を合成
          (lib/tts_player.py と lib/VOICEVOXlib.py をそのまま使う)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aiohttp  # noqa: E402

from lib.text_pipeline import PipelineConfig, preprocess_to_sentences  # noqa: E402
from lib.tts_player import Chunk, GuildTTSPlayer, TTSItem  # noqa: E402
from lib.VOICEVOXlib import FORMAT_DISCORD, VOICEVOXLib, _wav_duration  # noqa: E402

DEFAULT_TEXT = (
    "今日はとても天気が良いので、みんなで公園に行って遊びました。"
    "そのあと、駅前の喫茶店でコーヒーを飲みながら、来週の予定を話し合いました。"
    "帰り道に雨が降ってきて、少し濡れてしまいました。"
)


async def bench_before(url: str, text: str, speaker: int) -> dict:
    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{url}/audio_query", params={"text": text, "speaker": speaker}) as r:
            r.raise_for_status()
            query = await r.json()
        async with session.post(f"{url}/synthesis", params={"speaker": speaker}, json=query) as r:
            r.raise_for_status()
            wav = await r.read()
    t_synth = time.perf_counter() - t0
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        if shutil.which("ffmpeg"):
            # discord.FFmpegPCMAudio と同じ変換で、最初の 20ms フレームが出るまで
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-i", path, "-f", "s16le", "-ar", "48000", "-ac", "2", "-loglevel", "warning", "pipe:1",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.stdout.readexactly(3840), 30)
            t_first = time.perf_counter() - t0
            proc.kill()
            await proc.wait()
        else:
            t_first = None
    finally:
        os.remove(path)
    return {"first_audio": t_first, "synth": t_synth, "audio_sec": _wav_duration(wav)}


async def bench_after(url: str, text: str, speaker: int, concurrency: int) -> dict:
    lib = VOICEVOXLib(url, concurrency=concurrency, cache_mb=0)  # キャッシュは無効にして計測
    lib.backup_urls = []
    sentences = preprocess_to_sentences(text, config=PipelineConfig(max_length=0))
    result = {"sentences": len(sentences), "gaps": []}
    last_end = None
    t0 = time.perf_counter()

    async def prepare(item):
        return [Chunk(s, speaker) for s in sentences]

    async def synthesize(chunk, priority):
        return await lib.synthesize_wav(chunk.text, chunk.speaker_id, priority=priority,
                                        output_format=FORMAT_DISCORD, use_cache=False)

    async def play(wav):
        nonlocal last_end
        now = time.perf_counter()
        if last_end is None:
            result["first_audio"] = now - t0
        else:
            result["gaps"].append(now - last_end)
        await asyncio.sleep(_wav_duration(wav))  # 実時間で再生したとみなす
        last_end = time.perf_counter()

    player = GuildTTSPlayer(prepare, synthesize, play)
    task = asyncio.create_task(player.run())
    item = TTSItem(text, speaker, done=asyncio.get_running_loop().create_future())
    player.enqueue(item)
    await item.done
    result["total"] = time.perf_counter() - t0
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await lib.close()
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("VOICEVOX_URL", "http://localhost:50021").split(",")[0])
    ap.add_argument("--speaker", type=int, default=3)
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=1, help="tts.synthesis_concurrency に相当")
    args = ap.parse_args()

    print(f"Engine: {args.url} / speaker={args.speaker} / {len(args.text)}文字")
    async with aiohttp.ClientSession() as s:  # モデルを読み込ませておく(初回ロードを計測に含めない)
        async with s.post(f"{args.url}/audio_query", params={"text": "あ", "speaker": args.speaker}) as r:
            q = await r.json()
        async with s.post(f"{args.url}/synthesis", params={"speaker": args.speaker}, json=q) as r:
            await r.read()
        async with s.get(f"{args.url}/version") as r:
            print(f"Engine version: {await r.text()}")

    for i in range(args.repeat):
        before = await bench_before(args.url, args.text, args.speaker)
        after = await bench_after(args.url, args.text, args.speaker, args.concurrency)
        gaps = after["gaps"]
        first_before = f"{before['first_audio']:.2f}s" if before["first_audio"] is not None else "(ffmpegなし)"
        print(f"[{i + 1}] before: 再生開始まで {first_before} (うち合成 {before['synth']:.2f}s, 音声長 {before['audio_sec']:.1f}s)")
        print(f"    after : 再生開始まで {after['first_audio']:.2f}s ({after['sentences']}文に分割), "
              f"全文の再生完了まで {after['total']:.2f}s, 文間の無音待ち 合計 {sum(gaps):.2f}s / 最大 {max(gaps, default=0):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
