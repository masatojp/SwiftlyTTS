"""VOICEVOX Engine クライアント。

- aiohttp.ClientSession を使い回す(Bot のイベントループに紐付ける)
- 合成(audio_query + synthesis)の同時実行数を全体で制限する(優先度付き)
- 合成済み wav を (話者, 速度, テキスト, 出力形式) をキーに LRU キャッシュする
- 4xx は再試行しない。5xx・通信エラーは非同期に待ってから再試行する
"""

from __future__ import annotations

import array
import asyncio
import collections
import heapq
import io
import itertools
import logging
import os
import random
import sys
import time
import wave
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from prometheus_client import Gauge

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

load_dotenv()

logger = logging.getLogger(__name__)

# 1分の音声生成にかかる処理時間(秒)
VOICE_GENERATION_TIME_PER_MINUTE = Gauge(
    'voice_generation_seconds_per_minute',
    '1分の音声生成にかかる平均処理時間（秒）'
)

# Discord 再生用の出力形式(48kHz・モノラル。再生時にステレオへ複製する)
DISCORD_SAMPLING_RATE = 48000

FORMAT_DEFAULT = "default"   # Engine の既定(24kHz モノラル)
FORMAT_DISCORD = "discord"   # 48kHz モノラル(ffmpeg なしで Discord に送れる)


class SynthesisError(RuntimeError):
    pass


class PriorityLimiter:
    """優先度付きの同時実行数制限(値が小さいほど優先)。

    読み上げ待ちの1文目を、再生中に行う先読み合成より先に処理するために使う。
    """

    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._active = 0
        self._waiters: list = []
        self._seq = itertools.count()

    @property
    def waiting(self) -> int:
        return sum(1 for _, _, f in self._waiters if not f.done())

    async def acquire(self, priority: int = 1) -> None:
        if self._active < self.limit and not self.waiting:
            self._active += 1
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._seq), fut))
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # 枠を譲られた直後にキャンセルされた場合は返却する
                self.release()
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done():
                fut.set_result(None)  # 枠をそのまま引き継ぐ
                return
        self._active -= 1


class AudioCache:
    """合成済み wav の LRU キャッシュ(バイト数と件数で上限)。"""

    def __init__(self, max_bytes: int, max_entries: int):
        self.max_bytes = max(0, int(max_bytes))
        self.max_entries = max(0, int(max_entries))
        self._data: "collections.OrderedDict[tuple, bytes]" = collections.OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0 and self.max_entries > 0

    def __len__(self) -> int:
        return len(self._data)

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def get(self, key) -> Optional[bytes]:
        value = self._data.get(key)
        if value is None:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key, value: bytes) -> None:
        if not self.enabled or len(value) > self.max_bytes:
            return
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= len(old)
        self._data[key] = value
        self._bytes += len(value)
        while self._data and (self._bytes > self.max_bytes or len(self._data) > self.max_entries):
            _, evicted = self._data.popitem(last=False)
            self._bytes -= len(evicted)

    def clear(self) -> None:
        self._data.clear()
        self._bytes = 0


def wav_to_discord_pcm(wav_bytes: bytes) -> Optional[bytes]:
    """48kHz/16bit の wav を discord.PCMAudio 用(48kHz/16bit/ステレオ)の PCM に変換する。

    形式が違う場合は None を返す(呼び出し側で ffmpeg にフォールバックする)。
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        if w.getframerate() != DISCORD_SAMPLING_RATE or w.getsampwidth() != 2:
            return None
        channels = w.getnchannels()
        frames = w.readframes(w.getnframes())
    if channels == 2:
        return frames
    if channels != 1:
        return None
    mono = array.array("h", frames)
    if sys.byteorder != "little":
        mono.byteswap()
    stereo = array.array("h", bytes(len(frames) * 2))
    stereo[0::2] = mono
    stereo[1::2] = mono
    if sys.byteorder != "little":
        stereo.byteswap()
    return stereo.tobytes()


def _wav_duration(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


class VOICEVOXLib:
    def __init__(self, base_url=None, *, concurrency: int = 1, cache_mb: float = 32,
                 cache_entries: int = 512, timeout: float = 90.0):
        self._base_url_arg = base_url
        self._default_url = "http://localhost:50021"
        self.base_urls = self._load_base_urls()
        self.backup_urls = self._load_backup_urls()
        self.limiter = PriorityLimiter(concurrency)
        self.cache = AudioCache(int(float(cache_mb) * 1024 * 1024), cache_entries)
        self.timeout = aiohttp.ClientTimeout(total=float(timeout))
        self._session: Optional[aiohttp.ClientSession] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.last_used_url: Optional[str] = None
        # プロジェクトルートの tmp ディレクトリ(ファイル出力の互換 API 用)
        try:
            project_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
            self.tmp_dir = os.path.join(project_root, "tmp")
            os.makedirs(self.tmp_dir, exist_ok=True)
        except Exception:
            self.tmp_dir = None

    # --- 設定 ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "VOICEVOXLib":
        tts = (config or {}).get("tts") or {}

        def num(name, default, typ):
            try:
                return typ(tts.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            concurrency=num("synthesis_concurrency", 1, int),
            cache_mb=num("audio_cache_mb", 32, float),
            cache_entries=num("audio_cache_entries", 512, int),
            timeout=num("synthesis_timeout", 90, float),
        )

    def _load_base_urls(self):
        if self._base_url_arg is None:
            env_urls = os.getenv("VOICEVOX_URL", self._default_url) or self._default_url
            return [u.strip() for u in env_urls.split(",") if u.strip()]
        if isinstance(self._base_url_arg, list):
            return self._base_url_arg
        return [self._base_url_arg]

    def _load_backup_urls(self):
        backup_env_urls = os.getenv("VOICEVOX_BACKUP_URL", "")
        return [u.strip() for u in backup_env_urls.split(",") if u.strip()]

    def reload_urls(self) -> None:
        """.env を読み直して接続先を更新する(管理者コマンドの config reload から呼ぶ)。"""
        load_dotenv(override=True)
        self.base_urls = self._load_base_urls()
        self.backup_urls = self._load_backup_urls()

    # --- セッション -----------------------------------------------------------------

    def _shared_session(self) -> Optional[aiohttp.ClientSession]:
        """最初に使ったイベントループ用の共有セッションを返す。別ループからの呼び出しでは None。"""
        loop = asyncio.get_running_loop()
        if self._loop is None or self._loop.is_closed():
            self._loop = loop
        if loop is not self._loop:
            return None
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # --- API -------------------------------------------------------------------

    async def get_speakers(self):
        """Fetch available speakers from the VOICEVOX engine."""
        base_url = random.choice(self.base_urls)
        session = self._shared_session()
        if session is None:
            async with aiohttp.ClientSession(timeout=self.timeout) as tmp:
                async with tmp.get(f"{base_url}/speakers") as response:
                    response.raise_for_status()
                    return await response.json()
        async with session.get(f"{base_url}/speakers") as response:
            response.raise_for_status()
            return await response.json()

    async def _request_wav(self, session, base_url, text, speaker_id, speed, output_format) -> bytes:
        async with session.post(
            f"{base_url}/audio_query", params={"text": text, "speaker": speaker_id}
        ) as res:
            res.raise_for_status()
            audio_query = await res.json()
        if "speedScale" in audio_query:
            audio_query["speedScale"] = speed
        if output_format == FORMAT_DISCORD:
            audio_query["outputSamplingRate"] = DISCORD_SAMPLING_RATE
            audio_query["outputStereo"] = False
        async with session.post(
            f"{base_url}/synthesis", params={"speaker": speaker_id}, json=audio_query
        ) as res:
            res.raise_for_status()
            return await res.read()

    async def _synthesize_uncached(self, session, text, speaker_id, speed, output_format) -> bytes:
        last_error: Optional[BaseException] = None
        urls = list(self.base_urls)
        random.shuffle(urls)  # 複数指定時は負荷分散
        for base_url in urls:
            if os.getenv("DEBUG") == "1":
                print(f"Using VOICEVOX URL: {base_url}")
            for attempt in range(3):
                try:
                    wav = await self._request_wav(session, base_url, text, speaker_id, speed, output_format)
                    self.last_used_url = base_url
                    return wav
                except aiohttp.ClientResponseError as e:
                    last_error = e
                    if 400 <= e.status < 500:
                        # 入力の問題なので、再試行しても結果は変わらない
                        raise SynthesisError(f"VOICEVOX rejected request ({e.status}): {text[:50]}") from e
                    logger.error(f"VOICEVOX synthesis failed for URL {base_url} (attempt {attempt+1}/3): {e}")
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = e
                    logger.error(f"VOICEVOX synthesis failed for URL {base_url} (attempt {attempt+1}/3): {e!r}")
                await asyncio.sleep(0.5)
        for backup_url in self.backup_urls:
            try:
                wav = await self._request_wav(session, backup_url, text, speaker_id, speed, output_format)
                self.last_used_url = backup_url
                if sentry_sdk:
                    sentry_sdk.capture_message(
                        f"VOICEVOX backup server used: {backup_url} for text: {text[:50]}...", level="info"
                    )
                return wav
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"VOICEVOX backup synthesis failed for URL {backup_url}: {e!r}")
                last_error = e
        raise SynthesisError(f"All VOICEVOX URLs failed for synthesis: {text[:50]}... Last error: {last_error!r}")

    async def synthesize_wav(self, text: str, speaker_id: int, speed: float = 1.0, *,
                             priority: int = 1, output_format: str = FORMAT_DISCORD,
                             use_cache: bool = True) -> bytes:
        """テキストを合成して wav のバイト列を返す。

        priority: 0 = 再生待ちの1文目(優先)、1 = 再生中の先読み
        """
        speed = float(speed or 1.0)
        key = (int(speaker_id), round(speed, 3), text, output_format)
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        session = self._shared_session()
        start = time.perf_counter()
        if session is None:
            # Bot 以外のイベントループ(別スレッド)から呼ばれた場合は一時セッションで処理する
            async with aiohttp.ClientSession(timeout=self.timeout) as tmp:
                wav = await self._synthesize_uncached(tmp, text, speaker_id, speed, output_format)
        else:
            await self.limiter.acquire(priority)
            try:
                start = time.perf_counter()
                wav = await self._synthesize_uncached(session, text, speaker_id, speed, output_format)
            finally:
                self.limiter.release()

        elapsed = time.perf_counter() - start
        duration = _wav_duration(wav)
        if duration > 0:
            try:
                VOICE_GENERATION_TIME_PER_MINUTE.set(elapsed * 60.0 / duration)
            except Exception:
                pass
        if use_cache:
            self.cache.put(key, wav)
        return wav

    # --- 互換 API -------------------------------------------------------------------

    async def synthesize_bytes(self, text, speaker_id, speed: float = 1.0) -> tuple[str, bytes]:
        """Engine 既定形式の wav を返す(管理者ベンチ・Web のボイスサンプル用)。"""
        wav = await self.synthesize_wav(text, speaker_id, speed, priority=1,
                                        output_format=FORMAT_DEFAULT, use_cache=False)
        return self.last_used_url or "", wav

    async def synthesize(self, text, speaker_id, output_path, speed: float = 1.0):
        """合成して tmp ディレクトリに wav を保存し、そのパスを返す。"""
        wav = await self.synthesize_wav(text, speaker_id, speed, priority=0, output_format=FORMAT_DEFAULT)
        filename = os.path.basename(output_path)
        path = os.path.join(self.tmp_dir, filename) if self.tmp_dir else os.path.abspath(output_path)
        with open(path, "wb") as f:
            f.write(wav)
        return path


_shared: Optional[VOICEVOXLib] = None


def get_shared_voicelib(config: Optional[dict] = None) -> VOICEVOXLib:
    """プロセス全体で共有するクライアント(同時実行数の制限とキャッシュを共有するため)。"""
    global _shared
    if _shared is None:
        _shared = VOICEVOXLib.from_config(config)
    return _shared
