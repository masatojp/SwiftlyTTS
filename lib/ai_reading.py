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
            "3. **アクセント位置を `'` (シングルクォート) で指定する**。**全てのアクセント句（1文字の助詞含む）には必ずアクセント位置を1つ指定すること**。\n"
            "4. アクセント句末に `？` (全角)を入れると疑問文の発音になる。\n"
            "5. カナの手前に `_` (アンダースコア) を入れると無声化される。\n"
            "6. 文全体を自然なイントネーションになるように構成する。\n"
            "7. 「｟」と「｠」で囲まれたテキストは、手動辞書による置換結果です。この部分もAquesTalk記法に従ってカタカナ化・アクセント付与を行ってくださいが、意味が変わらないように注意してください。\n\n"
            "Examples:\n"
            "input: ディープラーニングは万能薬ではありません\n"
            "output: {\"original\": \"ディープラーニングは万能薬ではありません\", \"yomi\": \"ディ'イプ/ラ'アニングワ/バンノ'オヤクデワ/アリマセ'ン\"}\n\n"
            "input: テスト：退出しました\n"
            "output: {\"original\": \"テスト：退出しました\", \"yomi\": \"テ'スト/タイシュツシマシ'タ\"}\n"
            "input: そうだね\n"
            "output: {\"original\": \"そうだね\", \"yomi\": \"ソ'オダネ\"}\n" # 短い文でもアクセント必須
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.1, # 安定性のため低く設定
            "max_tokens": 800, # トークン数を少し増やす
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
                        self.logger.error(f"OpenRouter API Error: {response.status} - {error_text}")
                        return text # エラー時は元のテキストを返す

                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    
                    # JSONパース
                    try:
                        json_content = json.loads(content)
                        result = json_content.get("yomi", text).strip()
                        self.logger.info(f"AI Reading Result (Raw): {text} -> {result}")

                        # AquesTalk記法のバリデーションと修正
                        # ルール: 全ての句（/区切り）に ' が含まれている必要がある（記号のみの場合は除く）
                        # 句読点（、）や疑問符（？）はセパレータとして扱う
                        
                        # 簡易的な修正ロジック:
                        # 1. / で分割
                        # 2. 各要素について、さらに 、 で分割（あるいは、含むかどうかチェック）
                        # 3. カナが含まれているのに ' がない場合、末尾に ' を付与する
                        
                        checked_segments = []
                        segments = result.split('/')
                        for seg in segments:
                            # 句読点などでさらに分割される可能性があるが、まずは単純に
                            # カナが含まれているか判定（簡易判定）
                            has_kana = any('ァ' <= c <= 'ヶ' for c in seg)
                            if has_kana and "'" not in seg:
                                # アクセントがない場合、末尾（記号の前）に付与
                                # もし末尾が ？ や 、 ならその前に
                                if seg.endswith('？') or seg.endswith('、'):
                                     checked_segments.append(seg[:-1] + "'" + seg[-1])
                                else:
                                     checked_segments.append(seg + "'")
                            else:
                                checked_segments.append(seg)
                        
                        fixed_result = "/".join(checked_segments)
                        
                        if fixed_result != result:
                             self.logger.info(f"AI Reading Result (Fixed): {result} -> {fixed_result}")

                        # AquesTalk記法であることを示すプレフィックスを付与
                        return f"AQUESTALK:{fixed_result}"
                    except json.JSONDecodeError:
                        self.logger.error(f"Failed to parse JSON response: {content}")
                        return content.strip() # パース失敗時はそのまま返す

        except Exception as e:
            print(f"Failed to get AI reading: {e}")
            return text # エラー時は元のテキストを返す
