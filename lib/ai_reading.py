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
            return text

        if not text or not text.strip():
            return text

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/masatojp/SwiftlyTTS", # OpenRouter requirement
            "X-Title": "SwiftlyTTS", # OpenRouter requirement
        }

        system_prompt = (
            "あなたは日本語の読み上げボットのためのテキスト処理エンジンです。\n"
            "入力されたテキストを、音声合成エンジンが自然に読める「読み仮名（ひらがな、またはカタカナ）」に変換して出力してください。\n"
            "以下のルールを厳守してください：\n"
            "1. 出力は変換後のテキストのみを含めること。説明や引用符は一切不要。\n"
            "2. 漢字はひらがなに変換する。\n"
            "3. 英語やアルファベットは、自然な発音に近いカタカナに変換する（例: Apple -> アップル）。\n"
            "4. 数字はそのまま、または文脈に応じて読み下す。\n"
            "5. 絵文字や記号は、読み上げに不要なら削除するか、意味を表す言葉（「えがお」「わら」など）に変換する。\n"
            "6. 文脈を考慮し、自然なアクセントやイントネーションになるような表記を目指す。\n"
            "7. 元のテキストが既にひらがな・カタカナのみの場合はそのまま出力する。\n"
            "8. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力してください（例: input: ｟固定｠だよ -> output: ｟固定｠だよ）。"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.3, # 読みの安定性のため低めに
            "max_tokens": 500,
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
                        self.logger.error(f"OpenRouter API Error: {response.status} - {error_text}")
                        return text # エラー時は元のテキストを返す

                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()
        except Exception as e:
            self.logger.error(f"Failed to get AI reading: {e}")
            return text # エラー時は元のテキストを返す
