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
        # 起動時にAI読み仮名変換が有効かどうかをログに出す(動作確認用)
        if self.api_key:
            print(f"AI Reading: 有効 (model={self.model_name})")
        else:
            print("AI Reading: 無効 (OPENROUTER_API_KEY が未設定です)")

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
            # ひらがな・カタカナのみの場合はAIをスキップして高速化、ただしAquesTalk互換にするためカタカナ化＋末尾アクセント
            hira = "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔ"
            kata = "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴ"
            print(f"AI Reading: Skipped (ひらがな・カタカナのみのためAI不使用): {text[:20]}")
            tr = str.maketrans(hira, kata)
            translated = text.translate(tr)
            result = f"{translated}'" if not translated.endswith("'") else translated
            return result, True

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

        # 自然に聞こえることを最優先にしたプロンプト。
        # 細かく区切りすぎる・無声化記号を推測で付ける・アクセント位置が不自然、といった
        # 「ブツブツ途切れる」原因を避けるルールと、お手本(few-shot)を含める。
        system_prompt = (
            "あなたは日本語のテキスト読み上げ（TTS）エンジンのためのプリプロセッサです。入力された日本語テキストを、"
            "自然で滑らかに聞こえる「AquesTalk風記法」に変換し、以下のJSON形式のみで出力してください。\n\n"
            "Format:\n"
            "{\n"
            "  \"aques_talk\": \"AquesTalk風記法に変換されたテキスト\"\n"
            "}\n\n"
            "【記法のルール】\n"
            "1. 全てのカナはカタカナで記述する。長音は「ー」を使う（例: 今日 -> キョ'ー）。\n"
            "2. アクセント句は / で区切る。 、 は無音（ポーズ）が入るため、文中の読点「、」と文末の「。」の位置にだけ使う。\n"
            "3. 全てのアクセント句に、アクセント位置を表す ' を必ず1つだけ付ける。' は「アクセント核のカナの直後」に置く。\n"
            "4. アクセント句末に ？ (全角) を付けると疑問文の発音になる。\n\n"
            "【自然に聞かせるための重要な指針】\n"
            "A. アクセント句は細かく切りすぎない。基本は「自立語＋助詞・助動詞」の文節単位（目安は1句あたり3〜10拍程度）。"
            "助詞（が・は・を・に・で・と・も 等）や「です・ます・しました」は前の語に付けて同じ句にする。\n"
            "B. 長い複合語は1つの句にまとめるか、2つ程度に分ける。1拍や2拍だけの極端に短い句を連続させない。\n"
            "C. アクセント核の位置が分からない・自信がない場合は、句の最後のカナの直後に ' を置く（平板型として扱う）。"
            "無理に句頭に ' を置くと不自然になる。\n"
            "D. ' は「ッ」「ン」「ー」の直後には置かない（その1つ前のカナの直後に置く）。例: ボ'ット（×ボッ'ト）。\n"
            "E. 無声化記号 _ は原則として使わない。付ける位置を間違えると音が欠けて途切れて聞こえるため、出力に _ を含めない。\n"
            "F. 「。」「！」「？」などの文末記号は、文末では 、 に置き換えてよい。文の途中にある不要な記号は付けない。\n"
            "G. 記号や絵文字等で発音に関係ないものは除去するか、文脈に応じた適切な言葉に変換する。"
            "数字やアルファベットも自然な読み（例: 1 -> イチ'、3個 -> サ'ンコ、URL -> ユーアールエル'）にする。\n"
            "H. 「w」「草」などの笑いの表現は、読み上げて自然な「ワラ'」「クサ'」程度に簡潔にする。\n"
            "I. 「｟」と「｠」で囲まれたテキストは手動辞書置換結果です。この部分は**絶対に**変更せず、囲まれたまま出力してください（例: input: ｟固定｠だよ -> output: ｟固定｠ダヨ'）。\n"
            "J. JSONのフォーマットを厳格に守り、文字列に改行を含めないでください。生（リテラル）の改行は禁止です。\n\n"
            "【お手本】\n"
            "入力: 音声再生ボットが参加しました。\n"
            "出力: {\"aques_talk\": \"オンセイサイセイ'/ボ'ットガ/サンカシマ'シタ、\"}\n"
            "入力: 今日はいい天気ですね\n"
            "出力: {\"aques_talk\": \"キョ'ーワ/イ'イ/テ'ンキデスネ\"}\n"
            "入力: 明日の会議は何時から？\n"
            "出力: {\"aques_talk\": \"アシタノ'/カイギワ/ナンジカラ？\"}\n"
            "入力: 3人でゲームしよう\n"
            "出力: {\"aques_talk\": \"サ'ンニンデ/ゲ'ームシヨー'\"}"
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
                        result = json_content.get("aques_talk", text).strip().replace("_", "")  # 無声化記号は音欠けの原因になるため除去
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
                        match = re.search(r'"aques_talk"\s*:\s*"([^"]*)(?:"|\}*|$)', clean_content)
                        if match:
                            fallback_result = match.group(1).replace('\n', ' ').replace('\\n', ' ').replace("_", "").strip()  # 無声化記号は音欠けの原因になるため除去
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
