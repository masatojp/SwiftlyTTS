# ボット実行方法

フォーク版のため、Docker Compose (Dockerfileビルド) での実行のみをサポートしています。

## 必須要件
- Docker Desktop または Docker Engine
- Docker Compose

## 手順

1. リポジトリをクローンする
   ```bash
   git clone https://github.com/techfish-11/SwiftlyTTS.git
   cd SwiftlyTTS
   ```

2. 必須ファイルの準備
   `.env` と `config.yml` を作成します。
   
   ```bash
   # 環境変数のみ設定
   cp .env.example .env
   # 設定ファイルを作成
   cp config.yml.example config.yml
   ```
   
   作成後、`.env` にBotトークンやDB接続情報、`config.yml` にPrefix設定などを記述してください。

3. 起動する
   Bot、PostgreSQL、VOICEVOX サーバーが一括で起動します。
   ```bash
   docker compose up -d
   ```
   ※ 初回起動時は Dockerfile のビルドが行われるため時間がかかります。

4. 停止する
   ```bash
   docker compose down
   ```

### AI読み仮名判定 (Optional)
OpenRouterのAPIを使用することで、漢字や英語の読み方をAIで判定し、より自然な発音に変換できます。

`.env` に以下の設定を追加してください:
```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx...
OPENROUTER_MODEL_NAME=google/gemini-2.0-flash-exp:free # 任意のモデルを指定可能
```
- APIキーが設定されていない場合は、AI機能はスキップされ、通常の辞書処理のみが行われます。

## 既存のDB・VOICEVOXを使用する場合 (アプリのみ起動)
すでに PostgreSQL や VOICEVOX サーバーが稼働している場合は、アプリ単体のコンテナをビルドして接続できます。

1. イメージのビルド
   ```bash
   docker build -t swiftlytts .
   ```

2. 実行
   `.env` ファイルを使用するか、`-e` オプションで接続先を指定して起動します。
   
   ```bash
   # 例: .env ファイルを使用しつつ、DBホストとVOICEVOX URLを上書きする場合
   docker run -d \
    --name swiftlytts-app \
    --network host \
    -e DISCORD_TOKEN=your_token_here \
    -e DB_HOST=localhost \
    -e DB_USER=postgres \
    -e DB_PASSWORD=your_db_password \
    -e DB_NAME=swiftlytts \
    -e VOICEVOX_URL=http://localhost:50021 \
    -e OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx... \
    -v $(pwd)/config.yml:/app/config.yml \
    swiftlytts:latest
```
- `OPENROUTER_API_KEY` を指定することで、このモードでもAI読み仮名判定が有効になります（不要な場合は省略可）。
   
   **注意**: 
   - ホストOSのDB等に接続する場合は、適切なネットワーク設定（`--network host` や適切なIPアドレス指定）を行ってください。
   - `DB_HOST` や `VOICEVOX_URL` は `.env` 内の設定よりも `-e` で指定した値が優先されます。