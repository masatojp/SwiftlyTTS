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

2. 環境変数の設定
   `.env.example` をコピーして `.env` を作成し、BotトークンやDB接続情報を設定してください。
   ```bash
   cp .env.example .env
   vi .env
   ```

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