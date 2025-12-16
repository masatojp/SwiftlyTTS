import aiohttp
import os
import json
import logging

class AIReadingClient:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.model_name = os.getenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash-exp:free")
        self.base_url = "https://openrouter.ai/api/v1"
        self.logger = logging.getLogger(__name__)

    async def get_reading(self, text: str) -> str:
        """
        AIを使用してテキストを読みビ（ひらがな・カタカナのみ）に変換する
        """
        if not self.api_key:
            print("AI Reading: Skipped (No API Key configured)")
            return text

        if not text or not text.strip():
            return text
        
        print(f"AI Reading: Processing text: {text[:20]}...")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/masatojp/SwiftlyTTS", # OpenRouter requirement
            "X-Title": "SwiftlyTTS", # OpenRouter requirement
        }

        system_prompt = (
            "あなたは日本語のテキスト読み上げ（TTS）エンジンのためのプリプロセッサです。入力された日本語テキストを解析し、以下のJSON形式で出力してください。\n\n"
            "Format:\n"
            "{\n"
            "  \"original\": \"元のテキスト\",\n"
            "  \"yomi\": \"変換後のテキスト（AquesTalk風記法）\"\n"
            "}\n\n"
            "Rule (AquesTalk風記法):\n"
            "1. **すべての文字を『カタカナ』に変換**してください（漢字・ひらがな・英語・数字すべて）。\n"
            "2. **アクセント句は `/` (スラッシュ) または `、` (読点) で区切る**。\n"
            "3. **アクセント位置を `'` (シングルクォート) で指定する**。全てのアクセント句にはアクセント位置を1つ指定する。\n"
            "4. アクセント句末に `？` (全角)を入れると疑問文の発音になる。\n"
            "5. カナの手前に `_` (アンダースコア) を入れると無声化される。\n"
            "6. 文全体を自然なイントネーションになるように構成する。\n"
            "7. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分もAquesTalk記法に従ってカタカナ化・アクセント付与を行ってくださいが、意味が変わらないように注意してください。\n\n"
            "Examples:\n"
            "input: ディープラーニングは万能薬ではありません\n"
            "output: {\"original\": \"ディープラーニングは万能薬ではありません\", \"yomi\": \"ディ'イプ/ラ'アニングワ/バンノ'オヤクデワ/アリマセ'ン\"}\n\n"
            "input: テスト：退出しました\n"
            "output: {\"original\": \"テスト：退出しました\", \"yomi\": \"テ'スト/タイシュツシマシ'タ\"}\n"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.1, # 安定性のため低く設定
            "max_tokens": 500,
            "response_format": {"type": "json_object"} # JSONモードを有効化
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"OpenRouter API Error: {response.status} - {error_text}")
                        return text # エラー時は元のテキストを返す

                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    
                    # JSONパース
                    try:
                        json_content = json.loads(content)
                        result = json_content.get("yomi", text).strip()
                        print(f"AI Reading Result: {text} -> {result}")
                        # AquesTalk記法であることを示すプレフィックスを付与
                        return f"AQUESTALK:{result}"
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON response: {content}")
                        return content.strip() # パース失敗時はそのまま返す

        except Exception as e:
            print(f"Failed to get AI reading: {e}")
            return text # エラー時は元のテキストを返す
