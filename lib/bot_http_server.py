
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from lib import postgres
import asyncio


app = FastAPI()

_bot = None  # グローバルでbotインスタンスを保持

def set_bot(bot):
    global _bot
    _bot = bot

def _call_dictionary_cog(method_name: str, *args) -> bool:
    """DictionaryCog のキャッシュ破棄を Bot のイベントループ上で実行する。

    この HTTP サーバーは別スレッド(別イベントループ)で動くため、Bot 側のオブジェクトは
    call_soon_threadsafe 経由で操作する。
    """
    if _bot is None:
        return False
    cog = _bot.get_cog("DictionaryCog")
    if cog is None:
        return False
    try:
        _bot.loop.call_soon_threadsafe(getattr(cog, method_name), *args)
        return True
    except Exception as e:
        print(f"[Notify] cache clear error: {e}")
        return False


# CORS許可（必要に応じて調整）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB初期化
pg = postgres.PostgresDB()
@app.on_event("startup")
async def startup():
    await pg.initialize()

@app.on_event("shutdown")
async def shutdown():
    await pg.close()

@app.get("/user-dictionary/{user_id}")
async def get_user_dictionary(user_id: int):
    print(f"[API] Fetching user dictionary for user_id={user_id}")
    try:
        entries = await pg.get_all_user_dictionary(user_id)
        return {"dictionary": [{"key": r["key"], "value": r["value"]} for r in entries]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/servers")
async def get_server_count():
    """Return the number of guilds the bot is connected to.

    Response shape:
      { "count": 123 }

    If the global bot instance is not set, return a 503 with ok:false.
    """
    try:
        if _bot is None:
            raise HTTPException(status_code=503, detail="bot not ready")

        # AutoShardedBot and Bot both expose guilds; use len(list(_bot.guilds)) to be safe
        try:
            guilds = list(_bot.guilds)
            count = len(guilds)
        except Exception:
            # Fallback: try attribute `guild_count` if available
            count = getattr(_bot, "guild_count", None)
            if count is None:
                raise

        return JSONResponse({"count": int(count)})
    except HTTPException:
        raise
    except Exception as e:
        # Return 500 on unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/user-dictionary/notify")
async def notify_user_dictionary(request: Request):
    print("[Notify] Received user dictionary change notification")
    data = await request.json()
    user_id = data.get("user_id")
    print(f"[Notify] user_dictionary changed for user_id={user_id}")
    if user_id is not None:
        _call_dictionary_cog("invalidate_user", int(user_id))
    return {"ok": True}


# ギルド辞書変更通知
@app.post("/guild-dictionary/notify")
async def notify_guild_dictionary(request: Request):
    print("[Notify] Received guild dictionary change notification")
    data = await request.json()
    guild_id = data.get("guild_id")
    print(f"[Notify] guild_dictionary changed for guild_id={guild_id}")
    if guild_id is not None:
        _call_dictionary_cog("invalidate_guild", int(guild_id))
    return {"ok": True}


# ボイスサンプル生成（キャッシュ付き）
_voice_sample_cache = {}  # {speaker_id: wav_bytes}

@app.get("/voice-sample/{speaker_id}")
async def get_voice_sample(speaker_id: int):
    """Generate a voice sample for the given speaker_id.
    
    Returns cached sample if available, otherwise generates a new one.
    Sample text: "こんにちは、私の声のサンプルです。"
    """
    try:
        # キャッシュチェック
        if speaker_id in _voice_sample_cache:
            print(f"[VoiceSample] Returning cached sample for speaker_id={speaker_id}")
            from fastapi.responses import Response
            return Response(
                content=_voice_sample_cache[speaker_id],
                media_type="audio/wav",
                headers={
                    "Content-Disposition": f"inline; filename=sample_{speaker_id}.wav",
                    "Cache-Control": "public, max-age=86400"  # 24時間キャッシュ
                }
            )
        
        # キャッシュになければ生成
        if _bot is None:
            raise HTTPException(status_code=503, detail="bot not ready")
        
        # VoiceReadCogからVOICEVOXLibを取得
        cog = _bot.get_cog("VoiceReadCog")
        if cog is None or not hasattr(cog, "voicelib"):
            raise HTTPException(status_code=503, detail="VoiceReadCog not available")
        
        sample_text = "こんにちは、私の声のサンプルです。"
        print(f"[VoiceSample] Generating new sample for speaker_id={speaker_id}")
        
        # synthesize_bytesを使用してバイトデータを取得
        # Bot のイベントループ上で合成する(セッション・同時実行数の制限を共有するため)
        future = asyncio.run_coroutine_threadsafe(
            cog.voicelib.synthesize_bytes(sample_text, speaker_id), _bot.loop
        )
        _, wav_bytes = await asyncio.wrap_future(future)
        
        # キャッシュに保存
        _voice_sample_cache[speaker_id] = wav_bytes
        print(f"[VoiceSample] Cached sample for speaker_id={speaker_id}, size={len(wav_bytes)} bytes")
        
        from fastapi.responses import Response
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f"inline; filename=sample_{speaker_id}.wav",
                "Cache-Control": "public, max-age=86400"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VoiceSample] Error generating sample for speaker_id={speaker_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ユーザーボイス変更通知
@app.post("/user-voice/notify")
async def notify_user_voice(request: Request):
    """ユーザーのボイス設定が変更されたときに呼ばれる
    
    ユーザー辞書のキャッシュをクリアする（ボイス設定変更時に辞書も再読み込み）
    """
    print("[Notify] Received user voice change notification")
    data = await request.json()
    user_id = data.get("user_id")
    print(f"[Notify] user_voice changed for user_id={user_id}")
    
    # ユーザー辞書キャッシュをクリア（ボイス変更時に辞書も再適用させる）
    if user_id is not None and _call_dictionary_cog("invalidate_user", int(user_id)):
        print(f"[Notify] Cleared user dictionary cache for user_id={user_id}")

    return {"ok": True}
