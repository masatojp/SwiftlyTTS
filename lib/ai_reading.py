import aiohttp
import asyncio
import os
import json
import logging

class AIReadingClient:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.model_name = os.getenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash-exp:free")
        self.base_url = "https://openrouter.ai/api/v1"
        self.logger = logging.getLogger(__name__)
        # LRU用のキャッシュ (OrderedDict を使用)
        import collections
        self.cache = collections.OrderedDict()
        self.cache_max_size = 1000

    async def get_reading(self, text: str) -> str:
        """
        AIを使用してテキストを読みビ（ひらがな・カタカナのみ）に変換する
        """
        if not self.api_key:
            print("AI Reading: Skipped (No API Key configured)")
            return text

        if not text or not text.strip():
            return text
            
        # キャッシュのチェック
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        
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
            "  \"yomi_katakana\": \"カタカナ変換後のテキスト（数字やアルファベットもカタカナ読み・句読点は維持・漢字はひらがな等に開く）\"\n"
            "}\n\n"
            "Rule:\n"
            "1. 数字やアルファベットは、文脈に応じて自然な読み仮名（カタカナ）にする。\n"
            "2. 絵文字や記号は、読み上げに不要なら削除するか、意味を表す言葉に変換する。\n"
            "3. 文脈を考慮し、自然なアクセントやイントネーションになるような表記を目指す。\n"
            "4. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力してください（例: input: ｟固定｠だよ -> output: ｟固定｠だよ）。\n"
            "5. JSONのフォーマットを厳格に守り、文字列に改行を含める場合は必ず「\\n」のようにエスケープするか、改行をスペースに置換してください。生（リテラル）の改行を含めないでください。"
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

        timeout_limit = aiohttp.ClientTimeout(total=4) # 4秒タイムアウト
        try:
            async with aiohttp.ClientSession(timeout=timeout_limit) as session:
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
                        import re
                        # マークダウンのコードブロックを除去
                        clean_content = content.replace("```json", "").replace("```", "").strip()
                        
                        # 改行をエスケープできていない不正なJSONが返ってくる場合があるため、
                        # 簡易的に \n を除去するか置換するなどの対処は難しいので厳密なパースを試みる
                        json_content = json.loads(clean_content)
                        result = json_content.get("yomi_katakana", text).strip()
                        print(f"AI Reading Result: {text[:30]}... -> {result[:30]}...")
                        self.cache[text] = result
                        if len(self.cache) > self.cache_max_size:
                            self.cache.popitem(last=False)
                        return result
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON response: {content}")
                        # 正規表現で yomi_katakana の中身を抽出するフォールバック
                        import re
                        # 末尾のダブルクォーテーションや括弧が欠損している場合にも対応する強力な正規表現
                        match = re.search(r'"yomi_katakana"\s*:\s*"([^"]*)(?:"|\}*|$)', clean_content)
                        if match:
                            fallback_result = match.group(1).replace('\n', ' ').replace('\\n', ' ').strip()
                            print(f"Fallback extracted: {fallback_result[:30]}...")
                            self.cache[text] = fallback_result
                            if len(self.cache) > self.cache_max_size:
                                self.cache.popitem(last=False)
                            return fallback_result
                        
                        return text # パース失敗時は元のテキストを返す (JSONの生テキストを読み上げないように)
        # TimeoutErrorのキャッチを追加
        except asyncio.TimeoutError:
            print(f"AI Reading Timeout: 4 seconds elapsed for text '{text[:20]}...'")
            return text
        except Exception as e:
            print(f"Failed to get AI reading: {e}")
            return text # エラー時は元のテキストを返す
