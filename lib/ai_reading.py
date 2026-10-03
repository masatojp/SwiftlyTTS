import aiohttp
import asyncio
import os
import json
import logging
import re

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
        # 起動時にAI読み仮名変換が有効かどうかをログに出す(動作確認用)
        if self.api_key:
            print(f"AI Reading: 有効 (model={self.model_name})")
        else:
            print("AI Reading: 無効 (OPENROUTER_API_KEY が未設定です)")

    @staticmethod
    def _clean(text: str) -> str:
        """AIが誤って出力したアクセント記号・区切り記号を除去し、前後の空白を整える"""
        return re.sub(r"[_'/]", "", text).strip()

    async def get_reading(self, text: str) -> tuple[str, bool]:
        """
        AIを使用してテキストを読みビ（ひらがな・カタカナのみ）に変換する
        """
        if not self.api_key:
            print("AI Reading: Skipped (No API Key configured)")
            return text, False

        if not text or not text.strip():
            return text, False
            
        # 漢字・英数字・一部の記号が含まれていないかチェック（ひらがな・カタカナのみの場合はAIをスキップして高速化）
        import re
        if not re.search(r'[a-zA-Z0-9０-９ａ-ｚＡ-Ｚ\u4e00-\u9faf]', text):
            # ひらがな・カタカナのみの場合はAIをスキップして高速化し、そのまま通常テキストとして返す
            print(f"AI Reading: Skipped (ひらがな・カタカナのみのためAI不使用): {text[:20]}")
            return text, False

        # キャッシュのチェック
        if text in self.cache:
            print(f"AI Reading: キャッシュを使用: {text[:20]}")
            self.cache.move_to_end(text)
            return self.cache[text], True
        
        print(f"AI Reading: Processing text: {text[:20]}...")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/masatojp/SwiftlyTTS", # OpenRouter requirement
            "X-Title": "SwiftlyTTS", # OpenRouter requirement
        }

        # アクセント記号(' / _ など)は付けさせず、VOICEVOX標準のアクセント判定に任せる。
        # AIには「読み間違いやすい部分だけを、読みやすい表記に直す」役割のみを与える。
        system_prompt = (
            "あなたは日本語のテキスト読み上げ（TTS）エンジンのためのプリプロセッサです。"
            "入力された文章を、音声合成エンジンが正しく自然に読めるテキストに整えて、以下のJSON形式のみで出力してください。\n\n"
            "Format:\n"
            "{\n"
            "  \"text\": \"整えたテキスト\"\n"
            "}\n\n"
            "【ルール】\n"
            "1. アクセント記号や区切り記号（' / _ など）は絶対に付けない。読みの表記を整えるだけにする。\n"
            "2. 一般的に正しく読める漢字・ひらがな・カタカナは、そのまま変更しない。文の意味・語順・口調も変えない。\n"
            "3. 読み間違えやすい部分だけをひらがなまたはカタカナに直す。対象は、人名・固有名詞・当て字の難読語、"
            "文脈で読みが変わる語、アルファベット・数字・単位、記号などである（例: 3個 -> さんこ、URL -> ユーアールエル、1/2 -> にぶんのいち）。\n"
            "4. 発音に関係ない記号や絵文字は除去するか、文脈に応じた適切な言葉に変換する。「w」「草」などの笑いは「わら」程度に簡潔にする。\n"
            "5. 句読点（、。）や「？」「！」は、そのまま残してよい。読点・句点は自然な間として使われる。\n"
            "6. 「｟」と「｠」で囲まれたテキストは手動辞書置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力する。\n"
            "7. JSONのフォーマットを厳格に守り、文字列に改行を含めない。生（リテラル）の改行は禁止。\n\n"
            "【お手本】\n"
            "入力: あかねちゃんが退出しました。\n"
            "出力: {\"text\": \"あかねちゃんが退出しました。\"}\n"
            "入力: 3人でGTA5やろうw\n"
            "出力: {\"text\": \"さんにんでジーティーエーファイブやろうわら\"}\n"
            "入力: 明日の会議は10時からです\n"
            "出力: {\"text\": \"明日の会議はじゅうじからです\"}"
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

        # APIの混雑や生成時間などを考慮し、タイムアウトを12秒に延長
        timeout_limit = aiohttp.ClientTimeout(total=12) 
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
                        return text, False # エラー時は元のテキストを返す

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
                        result = self._clean(json_content.get("text", text))
                        print(f"AI Reading Result: {text[:30]}... -> {result[:30]}...")
                        self.cache[text] = result
                        if len(self.cache) > self.cache_max_size:
                            self.cache.popitem(last=False)
                        return result, True
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON response: {content}")
                        # 正規表現で aques_talk の中身を抽出するフォールバック
                        import re
                        # 末尾のダブルクォーテーションや括弧が欠損している場合にも対応する強力な正規表現
                        match = re.search(r'"text"\s*:\s*"([^"]*)(?:"|\}*|$)', clean_content)
                        if match:
                            fallback_result = self._clean(match.group(1)).replace('\n', ' ').replace('\\n', ' ').replace("_", "").strip()  # 無声化記号は音欠けの原因になるため除去
                            print(f"Fallback extracted: {fallback_result[:30]}...")
                            self.cache[text] = fallback_result
                            if len(self.cache) > self.cache_max_size:
                                self.cache.popitem(last=False)
                            return fallback_result, True
                        
                        return text, False # パース失敗時は元のテキストを返す (JSONの生テキストを読み上げないように)
        # TimeoutErrorのキャッチを追加
        except asyncio.TimeoutError:
            print(f"AI Reading Timeout: 4 seconds elapsed for text '{text[:20]}...'")
            return text, False
        except Exception as e:
            print(f"Failed to get AI reading: {e}")
            return text, False # エラー時は元のテキストを返す
