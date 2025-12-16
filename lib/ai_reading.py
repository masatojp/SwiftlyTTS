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
            "2. **アクセント句は `/` (スラッシュ) で区切る**（極力 `/` を使用し、 `、` は避ける）。\n"
            "3. **アクセント位置を `'` (シングルクォート) で指定する**。**全てのアクセント句（1文字の助詞含む）には必ずアクセント位置を1つだけ指定すること**（複数回指定は禁止）。\n"
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
                        # ルール:
                        # 1. 全ての句（/区切り）に ' が必ず1つだけ含まれている必要がある
                        # 2. 複数の ' がある場合は最初の1つを残す（あるいは適切なルールで処理）
                        # 3. ' がない場合は末尾（記号の前）に付与する
                        # 4. 空の句は除去する
                        
                        checked_segments = []
                        # まず区切り文字を統一（、を / に置換して処理しやすくする手もあるが、元の区切りを残したい）
                        # ここでは簡易的に / で分割して処理
                        segments = result.split('/')
                        for seg in segments:
                            if not seg:
                                continue # 空要素はスキップ
                            
                            # 句読点（、。）や疑問符（？）が含まれる場合の扱い
                            # これらが区切り文字として機能する場合もあるが、VOICEVOXのis_kana=trueでは
                            # 基本的に / 区切り推奨。AIには / 区切りを指示している。
                            # もし AI が 、 を使ってきた場合、それも残すが、アクセントチェックは「カナの塊」ごとに行う必要がある。
                            
                            # 簡易実装: セグメント内にカナがあるか確認
                            kanas = [c for c in seg if 'ァ' <= c <= 'ヶ']
                            if not kanas:
                                # 記号のみ（例: ？）の場合はそのまま
                                checked_segments.append(seg)
                                continue

                            # アクセント記号の数をチェック
                            accent_count = seg.count("'")
                            
                            if accent_count == 0:
                                # アクセントがない場合、末尾（記号の前）に付与
                                if seg.endswith('？') or seg.endswith('、') or seg.endswith('。'):
                                     # 末尾の記号を除いた部分の最後に ' を入れる
                                     # ただし記号が連続する場合などを考慮して、後ろから見て最初のカナの後ろに入れるのが安全
                                     # ここでは簡易的に「最後の1文字が記号ならその前」とする
                                     fixed_seg = seg[:-1] + "'" + seg[-1]
                                     checked_segments.append(fixed_seg)
                                else:
                                     checked_segments.append(seg + "'")
                            elif accent_count > 1:
                                # アクセントが複数ある場合、最初の1つだけ残して他は削除する（単純化）
                                # 例: "タンタア'ーン'ト" -> "タンタア'ーント"
                                first_accent_index = seg.find("'")
                                fixed_seg = seg[:first_accent_index+1] + seg[first_accent_index+1:].replace("'", "")
                                checked_segments.append(fixed_seg)
                            else:
                                # アクセントが1つだけある場合 (正常)
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
            self.logger.error(f"AI Reading Error: {e}")
            return text # エラー時は元のテキストを返す
```
