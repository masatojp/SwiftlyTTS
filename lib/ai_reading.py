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
            "2. **アクセント句は `/` (スラッシュ) で区切る**。\n"
            "3. **アクセント位置を `'` (シングルクォート) で指定する**。**必ず文字の『直後』に付けること**（例: `テ'スト` はOK、 `'テスト` はNG）。各フレーズに必ず1つだけ指定する。\n"
            "4. **アクセント型（イントネーション）の意識**:\n"
            "   - **平板型**: 1文字目の次は高く、最後のアウトまで高い（例: 「ハナガ」→「ハ'ナガ」ではなく「ハナ'ガ」）。AquesTalkでは「ハナ'ガ」のように助詞の直前または語末にアクセントを置くことで平板を表現する。\n"
            "   - **頭高型**: 1文字目が高く、2文字目以降下がる（例: 「イノチ」→「イ'ノチ」）。\n"
            "   - **中高型**: 途中の文字が高く、その後下がる（例: 「アナタ」→「アナ'タ」）。\n"
            "   - **尾高型**: 最後の文字が高く、助詞で下がる（平板と区別するため、単語単体では最後が高いが、助詞が付くと下がる。AquesTalkでは「ハナ'」）。\n"
            "5. 疑問文は文末に `？` をつける。\n"
            "6. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分もAquesTalk記法に従ってカタカナ化・アクセント付与を行ってくださいが、意味が変わらないように注意してください。\n\n"
            "Examples:\n"
            "input: ディープラーニングは万能薬ではありません\n"
            "output: {\"original\": \"ディープラーニングは万能薬ではありません\", \"yomi\": \"ディ'イプ/ラ'アニングワ/バンノ'オヤクデワ/アリマセ'ン\"}\n\n"
            "input: テスト：退出しました\n"
            "output: {\"original\": \"テスト：退出しました\", \"yomi\": \"テ'スト/タイシュツシマシ'タ\"}\n"
            "input: こんにちは\n"
            "output: {\"original\": \"こんにちは\", \"yomi\": \"コンニチワ'\"}\n" # 平板（助詞なしだが挨拶なので平板っぽく処理、便宜上末尾）
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.1,
            "max_tokens": 800,
            "response_format": {"type": "json_object"}
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
                        return text

                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    
                    # JSONパース
                    try:
                        json_content = json.loads(content)
                        result = json_content.get("yomi", text).strip()
                        self.logger.info(f"AI Reading Result (Raw): {text} -> {result}")

                        # AquesTalk記法のバリデーションと修正
                        # 厳格なルール適用:
                        # 1. 使用可能な文字はカタカナ、長音(ー)、アクセント(')、区切り(/)、疑問符(？)のみ
                        # 2. _ (無声化) はVOICEVOX互換性のため削除
                        # 3. 句読点（、。）はスラッシュに置換
                        # 4. ッ、ー の直後に ' は置かない
                        # 5. 先頭の ' は削除（文字の後ろにつけるルール）
                        # 6. 各フレーズに必ず1つの ' を含める
                        # 7. 空フレーズ除去

                        # Step 0: 記号置換
                        result = result.replace("、", "/").replace("。", "/").replace("！", "/")
                        result = result.replace(",", "/").replace(".", "/").replace("!", "/")
                        result = result.replace("?", "？")

                        # Step 1: 文字種フィルタリング ( _ を削除)
                        valid_chars = set("アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴァィゥェォッャュョヮー'？/")
                        cleaned_text = "".join([c for c in result if c in valid_chars])
                        
                        checked_segments = []
                        segments = cleaned_text.split('/')
                        
                        for seg in segments:
                            if not seg:
                                continue 
                            
                            # Step 2: 不正なアクセント位置の修正
                            # 先頭の ' を削除 ('ア -> ア)
                            seg = seg.lstrip("'")
                            
                            # ッ, ー の後ろの ' を削除 (ッ' -> ッ)
                            seg = seg.replace("ッ'", "ッ").replace("ー'", "ー")

                            # カナが含まれているかチェック
                            kanas = [c for c in seg if 'ァ' <= c <= 'ヶ' or c == 'ー']
                            if not kanas:
                                checked_segments.append(seg)
                                continue

                            # アクセント記号の数をチェック
                            accent_count = seg.count("'")

                            if accent_count == 0:
                                # アクセントがない場合、有効な箇所の最後（記号の手前）に付与
                                insert_pos = len(seg)
                                for i in range(len(seg) - 1, -1, -1):
                                    if seg[i] not in "？":
                                        insert_pos = i + 1
                                        break
                                
                                # ッ, ー の後ろは避ける
                                while insert_pos > 0 and seg[insert_pos-1] in "ッー":
                                    insert_pos -= 1
                                
                                if insert_pos > 0:
                                    seg = seg[:insert_pos] + "'" + seg[insert_pos:]
                                else:
                                    pass

                            elif accent_count > 1:
                                # 最初の有効なアクセントを残す
                                first_pos = seg.find("'")
                                seg = seg[:first_pos+1] + seg[first_pos+1:].replace("'", "")
                            
                            checked_segments.append(seg)
                        
                        fixed_result = "/".join(checked_segments)
                        
                        if fixed_result != result:
                             self.logger.info(f"AI Reading Result (Fixed): {result} -> {fixed_result}")

                        return f"AQUESTALK:{fixed_result}"
                    except json.JSONDecodeError:
                        self.logger.error(f"Failed to parse JSON response: {content}")
                        return content.strip()

        except Exception as e:
            self.logger.error(f"AI Reading Error: {e}")
            return text
