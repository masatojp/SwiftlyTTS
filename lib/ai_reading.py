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
            
        # 漢字・英数字・一部の記号が含まれていないかチェック（ひらがな・カタカナのみの場合はAIをスキップして高速化）
        import re
        if not re.search(r'[a-zA-Z0-9０-９ａ-ｚＡ-Ｚ\u4e00-\u9faf]', text):
            # ひらがな・カタカナのみの場合はAIをスキップして高速化、ただしAquesTalk互換にするためカタカナ化＋末尾アクセント
            hira = "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔ"
            kata = "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴ"
            tr = str.maketrans(hira, kata)
            translated = text.translate(tr)
            return f"{translated}'" if not translated.endswith("'") else translated

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
            "あなたは日本語のテキスト読み上げ（TTS）エンジンのためのプリプロセッサです。入力された日本語テキストを解析し、以下のJSON形式で「AquesTalk風記法」に変換して出力してください。\n\n"
            "Format:\n"
            "{\n"
            "  \"aques_talk\": \"AquesTalk風記法に変換されたテキスト\"\n"
            "}\n\n"
            "「AquesTalk風記法」のルール：\n"
            "1. 全てのカナはカタカナで記述される\n"
            "2. アクセント句は / または 、 で区切る。 、 で区切った場合に限り無音区間が挿入される。\n"
            "3. カナの手前に _ を入れるとそのカナは無声化される\n"
            "4. アクセント位置を ' で指定する。全てのアクセント句にはアクセント位置を 1 つ指定する必要がある。\n"
            "5. アクセント句末に ？ (全角)を入れることにより疑問文の発音ができる\n"
            "6. 記号や絵文字等で発音に関係ないものは除去するか、文脈に応じて適切な言葉（例: 笑顔）に変換し、数字やアルファベットもカタカナ読み（例: 1 -> イチ'）にする。\n"
            "7. 「｟」と「｠」で囲まれたテキストは手動辞書置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力してください（例: input: ｟固定｠だよ -> output: ｟固定｠ダヨ'）。\n"
            "8. JSONのフォーマットを厳格に守り、文字列に改行を含める場合は必ず「\\n」のようにエスケープするか、改行をスペースに置換してください。生（リテラル）の改行を含めないでください。"
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
                        result = json_content.get("aques_talk", text).strip()
                        print(f"AI Reading Result: {text[:30]}... -> {result[:30]}...")
                        self.cache[text] = result
                        if len(self.cache) > self.cache_max_size:
                            self.cache.popitem(last=False)
                        return result
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON response: {content}")
                        # 正規表現で aques_talk の中身を抽出するフォールバック
                        import re
                        # 末尾のダブルクォーテーションや括弧が欠損している場合にも対応する強力な正規表現
                        match = re.search(r'"aques_talk"\s*:\s*"([^"]*)(?:"|\}*|$)', clean_content)
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
