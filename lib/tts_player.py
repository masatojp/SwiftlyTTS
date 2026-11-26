"""ギルドごとの読み上げキューと再生ループ(先行合成つき)。

    メッセージ → [前処理・文分割] → 文1 → 合成 → 再生
                                   文2 →      合成(文1の再生中) → 再生
                                   ...

- 1文目の合成が終わったらすぐ再生を始め、再生中に次の文を合成する
  (再生中の1文 + 合成中または合成済みの1文、までに抑える)
- 次のメッセージの1文目も、前のメッセージの再生中に先読みする
- clear() で待機中・合成済みの音声をすべて破棄する(「s」コマンド)
- キューが上限を超えたときは、設定に応じて古いもの/新しいものを捨てる

discord には依存しない(合成・再生の関数を外から渡す)。
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

OVERFLOW_DROP_OLDEST = "drop_oldest"
OVERFLOW_DROP_NEWEST = "drop_newest"


@dataclass
class TTSItem:
    text: str
    speaker_id: int
    user_name: str = ""
    author_id: Optional[int] = None
    ctx: Any = None
    # False の場合は辞書を適用しない(システムメッセージ用)
    apply_dictionary: bool = True
    # 再生完了(または破棄)時に結果が入る。/read のように完了を待ちたい場合に使う
    done: Optional[asyncio.Future] = None
    enqueued_at: float = field(default_factory=time.perf_counter)
    # 1文でも再生を始めたか
    played: bool = False

    def finish(self, played: bool) -> None:
        if self.done is not None and not self.done.done():
            self.done.set_result(played)


@dataclass
class Chunk:
    text: str
    speaker_id: int
    speed: float = 1.0


PrepareFn = Callable[[TTSItem], Awaitable[list]]
SynthesizeFn = Callable[[Chunk, int], Awaitable[Any]]  # (chunk, priority) -> audio
PlayFn = Callable[[Any], Awaitable[None]]


class GuildTTSPlayer:
    # 再生中 + 合成中/合成済み の最大数
    IN_FLIGHT = 2

    def __init__(
        self,
        prepare: PrepareFn,
        synthesize: SynthesizeFn,
        play: PlayFn,
        *,
        logger: Optional[logging.Logger] = None,
        on_played: Optional[Callable[[TTSItem], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
        on_first_audio: Optional[Callable[[TTSItem, float], None]] = None,
    ):
        self._prepare = prepare
        self._synthesize = synthesize
        self._play = play
        self.logger = logger or logging.getLogger(__name__)
        self._on_played = on_played
        self._on_error = on_error
        self._on_first_audio = on_first_audio

        self._pending: "collections.deque[TTSItem]" = collections.deque()
        self._wake = asyncio.Event()
        self._ready: "asyncio.Queue[tuple]" = asyncio.Queue()
        self._slots = asyncio.Semaphore(self.IN_FLIGHT)
        self._epoch = 0
        self._playing = False
        self._ready_audio = 0
        self._run_task: Optional[asyncio.Task] = None
        self.dropped = 0

    # --- キュー操作 -----------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._pending)

    def enqueue(self, item: TTSItem, max_length: int = 0, overflow: str = OVERFLOW_DROP_OLDEST) -> bool:
        """キューに追加する。上限超過で新しい方を捨てた場合は False。"""
        if max_length and max_length > 0:
            while len(self._pending) >= max_length:
                if overflow == OVERFLOW_DROP_NEWEST:
                    self.dropped += 1
                    item.finish(False)
                    return False
                old = self._pending.popleft()
                self.dropped += 1
                old.finish(False)
        self._pending.append(item)
        self._wake.set()
        return True

    def clear(self) -> None:
        """待機中・合成済みの音声をすべて破棄する(再生中の音声の停止は呼び出し側で行う)。"""
        self._epoch += 1
        while self._pending:
            self._pending.popleft().finish(False)
        while not self._ready.empty():
            entry = self._ready.get_nowait()
            if entry[0] == "audio":
                self._ready_audio -= 1
                self._slots.release()
            entry[2].finish(False)

    @property
    def idle(self) -> bool:
        """再生中でも再生待ちでもない(=次の音声を今すぐ必要としている)か。"""
        return not self._playing and self._ready_audio == 0

    # --- ループ -------------------------------------------------------------------

    def _reset_run_state(self) -> None:
        """再生ループ1回分の状態(合成済み音声・枠)を作り直す。"""
        while not self._ready.empty():
            item = self._ready.get_nowait()[2]
            # キューに戻されたメッセージは、まだ読む予定なので完了扱いにしない
            if not any(item is pending for pending in self._pending):
                item.finish(False)
        self._ready = asyncio.Queue()
        self._slots = asyncio.Semaphore(self.IN_FLIGHT)
        self._playing = False
        self._ready_audio = 0

    async def run(self) -> None:
        """再生ループ。キャンセルされるまで動く。

        同じプレイヤーで新しく run() すると、前のループを止めて(後片付けを待って)から引き継ぐ。
        待機中のメッセージは引き継がれる。
        """
        previous = self._run_task
        current = asyncio.current_task()
        self._run_task = current
        if previous is not None and previous is not current and not previous.done():
            previous.cancel()
            await asyncio.gather(previous, return_exceptions=True)
        self._reset_run_state()
        producer = asyncio.create_task(self._producer())
        consumer = asyncio.create_task(self._consumer())
        try:
            done, _ = await asyncio.wait({producer, consumer}, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            producer.cancel()
            consumer.cancel()
            await asyncio.gather(producer, consumer, return_exceptions=True)
            if self._run_task is current:
                # 退出などでループが止められた: 残りは破棄する
                self._run_task = None
                self.clear()
                self._reset_run_state()

    async def _next_item(self) -> TTSItem:
        while not self._pending:
            self._wake.clear()
            await self._wake.wait()
        return self._pending.popleft()

    async def _producer(self) -> None:
        item: Optional[TTSItem] = None
        try:
            while True:
                item = None
                item = await self._next_item()
                await self._produce_item(item)
        except asyncio.CancelledError:
            # 処理途中のメッセージを失わない: 未再生なら先頭に戻す(破棄するかは run() の後片付けが決める)
            if item is not None and item.done is not None and item.done.done():
                pass
            elif item is not None and not item.played:
                self._pending.appendleft(item)
            elif item is not None:
                item.finish(True)
            raise

    async def _produce_item(self, item: TTSItem) -> None:
        epoch = self._epoch
        try:
            chunks = await self._prepare(item)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception(f"TTS prepare failed: {e}")
            self._report(e)
            item.finish(False)
            return
        for chunk in chunks:
            await self._slots.acquire()
            if epoch != self._epoch:
                self._slots.release()
                break
            priority = 0 if self.idle else 1
            try:
                audio = await self._synthesize(chunk, priority)
            except asyncio.CancelledError:
                self._slots.release()
                raise
            except Exception as e:
                self._slots.release()
                self.logger.error(f"TTS synth failed: {e}")
                self._report(e)
                continue
            if epoch != self._epoch:
                self._slots.release()
                break
            self._ready_audio += 1
            self._ready.put_nowait(("audio", epoch, item, audio))
        if epoch == self._epoch:
            self._ready.put_nowait(("end", epoch, item, None))
        else:
            item.finish(False)

    async def _consumer(self) -> None:
        while True:
            kind, epoch, item, audio = await self._ready.get()
            if kind == "end":
                item.finish(item.played)
                continue
            self._ready_audio -= 1
            try:
                if epoch != self._epoch:
                    continue
                if not item.played:
                    item.played = True
                    if self._on_first_audio:
                        self._on_first_audio(item, time.perf_counter() - item.enqueued_at)
                    if self._on_played:
                        self._on_played(item)
                self._playing = True
                try:
                    await self._play(audio)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"TTS playback failed: {e}")
                    self._report(e)
            finally:
                self._playing = False
                self._slots.release()

    def _report(self, error: BaseException) -> None:
        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                pass
