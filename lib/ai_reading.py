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
        AIを使用してテキストを読みビ（カタカナ・AquesTalk記法）に変換する
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
            "  \"yomi\": \"変換後のテキスト（すべてカタカナで出力・単語はつなげる）\"\n"
            "}\n\n"
            "Rule:\n"
            "1. **すべての文字を「カタカナ」に変換**してください（漢字・ひらがな・英語・数字すべて）。\n"
            "2. **単語と単語の間にスペースを入れない**でください。\n"
            "3. 句読点（、。）は残してください。\n"
            "4. アクセント核（音が下がる場所）には `'` を、アクセント句の区切りには `/` または `、` を入れて、AquesTalk風の記法でイントネーションを表現してください。\n"
            "5. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力してください（例: input: ｟固定｠だよ -> output: ｟固定｠ダヨ）。\n\n"
            "Examples:\n"
            "input: テスト：退出しました\n"
            "output: {\"original\": \"テスト：退出しました\", \"yomi\": \"テ'スト：タイシュツ/シマシタ\"}\n\n"
            "input: Discordの使い方\n"
            "output: {\"original\": \"Discordの使い方\", \"yomi\": \"ディスコ'ードノ/ツカイカタ\"}\n"
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
                        # Markdownのコードブロックがある場合は削除
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        
                        json_content = json.loads(content.strip())
                        
                        if isinstance(json_content, list):
                            if len(json_content) > 0 and isinstance(json_content[0], dict):
                                result = json_content[0].get("yomi", text).strip()
                            else:
                                result = text
                        elif isinstance(json_content, dict):
                            result = json_content.get("yomi", text).strip()
                        else:
                            result = text
                            
                        print(f"AI Reading Result: {text} -> {result}")
                        return result
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON response: {content}")
                        return content.strip() # パース失敗時はそのまま返す

        except Exception as e:
            print(f"Failed to get AI reading: {e}")
            return text # エラー時は元のテキストを返す
